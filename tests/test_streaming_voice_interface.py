"""Bind early speech to the exact local voice job and attempt, not arbitrary text."""
import hashlib
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import pytest
from prometheus_assistant import ui_server as ui, voice


@pytest.mark.parametrize('mode,value', [('chat', 'yes'), ('chat', 1), ('coding', True), ('dictate', True)])
def test_voice_reply_hint_requires_boolean_chat(mode, value):
    with pytest.raises(ValueError):
        ui.validate_job({'mode': mode, 'prompt': 'Hello', 'voice_reply': value})


def test_voice_reply_hint_is_accepted_for_chat_only():
    assert ui.validate_job({'mode': 'chat', 'prompt': 'Hello', 'voice_reply': True})['voice_reply'] is True


def held_voice_job(app, monkeypatch, binding='a'*64):
    request = {'mode': 'chat', 'prompt': 'Explain local models', 'voice_reply': True}
    with monkeypatch.context() as patch:
        patch.setattr(app.executor, 'submit', lambda *a, **k: None)
        ident = app.submit(request, approval_binding=binding)['id']
    app.enable_voice_stream(request, 'general')
    app.progress('Generating', reset=True)
    return ident


def test_epoch_binding_cancellation_and_completed_audio(tmp_path, monkeypatch):
    app = ui.Interface(tmp_path/'memory.sqlite3')
    try:
        ident = held_voice_job(app, monkeypatch)
        assert app.speech_active(ident, 1, 'a'*64)
        assert not app.speech_active(ident, True, 'a'*64)
        assert not app.speech_active(ident, 1, 'b'*64)
        assert not app.speech_active('0'*24, 1, 'a'*64)
        app.progress('Retry', reset=True)
        assert not app.speech_active(ident, 1, 'a'*64)
        assert app.speech_active(ident, 2, 'a'*64)
        with pytest.raises(ValueError):
            app.cancel_speech(ident, 1, 'a'*64)
        app.jobs[ident]['state'] = 'done'
        app.current = None
        assert app.speech_active(ident, 2, 'a'*64), 'queued final audio remains valid after generation finishes'
        app.cancel_speech(ident, 2, 'a'*64)
        assert not app.speech_active(ident, 2, 'a'*64)
        assert app.jobs[ident]['state'] == 'done', 'stopping audio does not delete an accepted answer'
    finally:
        app.close()


@pytest.mark.parametrize('state', ['error', 'cancelled'])
def test_failed_or_cancelled_job_invalidates_synthesis(tmp_path, monkeypatch, state):
    app = ui.Interface(tmp_path/'memory.sqlite3')
    try:
        ident = held_voice_job(app, monkeypatch)
        app.jobs[ident]['state'] = state
        assert not app.speech_active(ident, 1, 'a'*64)
    finally:
        app.current = None
        app.close()


def test_non_voice_or_code_output_never_gets_early_speech(tmp_path, monkeypatch):
    app = ui.Interface(tmp_path/'memory.sqlite3')
    try:
        ident = held_voice_job(app, monkeypatch)
        app.enable_voice_stream({'mode': 'chat', 'prompt': 'Return JSON', 'voice_reply': True}, 'general')
        assert not app.speech_active(ident, 1, 'a'*64)
        app.enable_voice_stream({'mode': 'chat', 'prompt': 'Hello'}, 'general')
        assert not app.speech_active(ident, 1, 'a'*64)
    finally:
        app.current = None
        app.close()


def test_speech_http_uses_job_binding_and_cancellation(tmp_path, monkeypatch):
    app = ui.Interface(tmp_path/'memory.sqlite3')
    binding = hashlib.sha256(b'fixture-owner-session').hexdigest()
    ident = held_voice_job(app, monkeypatch, binding)
    server = ui.Server(('127.0.0.1', 0), app)
    monkeypatch.setattr(ui.Handler, 'owner_session', lambda _: 'fixture-owner-session')
    monkeypatch.setattr(ui.Handler, 'owner_unlocked', lambda _: True)
    calls = []
    def synthesize(text, *, cancelled):
        assert not cancelled()
        calls.append(text)
        app.cancel_speech(ident, 1, binding)
        assert cancelled(), 'End must reach a synthesis process even after generation is done'
        return b'RIFF synthetic'
    monkeypatch.setattr(voice, 'synthesize_wav', synthesize)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    def post(path, body):
        request = Request(server.origin+path, data=json.dumps(body).encode(), headers={
            'Content-Type': 'application/json', 'Origin': server.origin, 'X-Prometheus-Token': app.token})
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        assert post('/api/speech', {'text':'No', 'job_id':ident, 'speech_epoch':0})[0] == 400
        assert not calls
        status, _ = post('/api/speech', {'text':'A valid opening.', 'job_id':ident, 'speech_epoch':1})
        assert status == 400, 'cancelled generated audio must not be returned as success'
        assert calls == ['A valid opening.']
        assert not app.speech_lock.locked()
        assert post('/api/speech/cancel', {'job_id':ident, 'speech_epoch':1})[0] == 200, 'repeated End is idempotent'
    finally:
        app.current = None
        server.shutdown(); server.server_close(); thread.join(timeout=3); app.close()
