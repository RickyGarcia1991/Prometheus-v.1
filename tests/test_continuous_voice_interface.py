"""Real job and HTTP boundaries around an isolated, simulated voice device."""
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

import pytest

from prometheus_assistant import ui_server as ui, voice


@pytest.fixture
def app(tmp_path):
    instance = ui.Interface(tmp_path/'memory.sqlite3')
    yield instance
    instance.close()


def wait(app, job):
    for _ in range(200):
        with app.lock:
            row = app.jobs[job['id']].copy()
        if row['state'] != 'running':
            return row
        time.sleep(.01)
    raise AssertionError('Voice job did not finish')


def recorder(monkeypatch):
    entered = threading.Event()
    transcribing = threading.Event()
    release = threading.Event()
    def capture(*, cancel_event, finish_event, on_stage, on_level, on_started, lease_alive, on_diagnostics):
        assert not finish_event.is_set() and lease_alive()
        on_started()
        on_stage('Listening')
        on_level(.4, 12.4)
        entered.set()
        try:
            while not finish_event.wait(.01):
                if cancel_event.is_set() or not lease_alive():
                    raise ui.GenerationCancelled('Dictation cancelled')
            on_stage('Microphone off · transcribing locally')
            transcribing.set()
            assert release.wait(2)
            on_diagnostics({'transcription_seconds': .75, 'endpoint_silence_audio_seconds': .832,
                'private_transcript': 'must not appear', 'asr_load_seconds': float('nan'),
                'capture_wall_seconds': float('inf')})
            return 'What is six times seven?'
        finally:
            release.set()
    monkeypatch.setattr(voice, 'capture_prompt_continuous', capture)
    return entered, transcribing, release


def test_finish_preserves_transcript_resets_level_and_next_job_events(app, monkeypatch):
    entered, transcribing, release = recorder(monkeypatch)
    for _ in range(2):
        entered.clear()
        transcribing.clear()
        release.clear()
        job = app.submit({'mode': 'dictate', 'prompt': ''})
        assert entered.wait(2)
        with app.lock:
            row = app.jobs[job['id']].copy()
        assert row['recording'] and row['level'] == .4 and row['elapsed'] == 12.4
        assert app.dictation_control(job['id']) == {'recording': True}
        assert app.dictation_control(job['id'], finish=True) == {'finish_requested': True}
        assert transcribing.wait(2)
        with pytest.raises(ValueError, match='no longer recording'):
            app.dictation_control(job['id'], finish=True)
        release.set()
        row = wait(app, job)
        assert row['state'] == 'done' and row['result']['transcript'] == 'What is six times seven?'
        assert row['recording'] is False and row['level'] == 0 and row['elapsed'] == 12.4
        assert row['result']['details']['voice_timing'] == {
            'transcription_seconds': .75, 'endpoint_silence_audio_seconds': .832}


@pytest.mark.parametrize('reason', ['cancel', 'lease', 'close'])
def test_abandoned_voice_turn_never_produces_transcript(app, monkeypatch, reason):
    entered, _, _ = recorder(monkeypatch)
    job = app.submit({'mode': 'dictate', 'prompt': ''})
    assert entered.wait(2)
    if reason == 'cancel':
        app.cancel(job['id'])
    elif reason == 'lease':
        with app.lock:
            app.dictation_last_pulse -= 20
        assert app.dictation_alive(job['id']) is False
        with pytest.raises(ValueError):
            app.dictation_control(job['id'])
    else:
        app.closing = True
        app.stop_dictation()
    row = wait(app, job)
    assert row['state'] == 'cancelled' and not row['recording'] and 'result' not in row
    assert app.state == 'idle' and app.current is None


def test_failed_dictation_keeps_error_state_and_closes_recording_status(app, monkeypatch):
    def fail(*, on_started, **kwargs):
        on_started()
        raise ValueError('Microphone device failed')
    monkeypatch.setattr(voice, 'capture_prompt_continuous', fail)
    row = wait(app, app.submit({'mode': 'dictate', 'prompt': ''}))
    assert row['state'] == 'error' and row['error'] == 'Microphone device failed'
    assert row['recording'] is False and 'result' not in row
    assert app.state == 'error' and app.current is None


def test_finish_without_speech_is_normal_idle_with_no_transcript(app, monkeypatch):
    from prometheus_assistant import continuous_capture
    closed = []
    detector_closed = []
    class QuietDetector:
        # This test covers the job's empty-recording state transition. Native
        # VAD initialization/inference is exercised by test_speech_endpoint and
        # must not consume this isolated interface test's two-second deadline.
        def __enter__(self):
            return self
        def __exit__(self, *args):
            detector_closed.append(True)
        def feed(self, data, *, final=False):
            if data:
                assert not any(data)
                yield data, False
    class QuietDevice:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(True)
        def read(self):
            app.dictation_finish.set()
            return b'\x00\x00'*1600
        def finish(self):
            return b''
    monkeypatch.setattr(continuous_capture, '_WaveInput', QuietDevice)
    monkeypatch.setattr(continuous_capture, 'SpeechDetector', QuietDetector)
    monkeypatch.setattr(voice, '_transcriber', lambda model: None)
    monkeypatch.setattr(voice, 'transcribe', lambda *args, **kwargs: pytest.fail('Empty capture must not invoke Whisper'))
    row = wait(app, app.submit({'mode': 'dictate', 'prompt': ''}))
    assert row['state'] == 'cancelled' and row['reason'] == 'empty_recording'
    assert row['error'] == 'No audible speech detected. Microphone off; nothing was submitted.'
    assert row['recording'] is False and 'result' not in row
    assert app.state == 'idle' and app.current is None and closed == [True]
    assert detector_closed == [True]


def test_controls_reject_wrong_job_and_non_dictation(app, monkeypatch):
    entered, _, _ = recorder(monkeypatch)
    job = app.submit({'mode': 'dictate', 'prompt': ''})
    assert entered.wait(2)
    for finish in (True, False):
        with pytest.raises(ValueError):
            app.dictation_control('wrong-id', finish=finish)
    app.cancel(job['id'])
    wait(app, job)
    job = app.submit({'mode': 'calculate', 'prompt': '6*7'})
    for finish in (True, False):
        with pytest.raises(ValueError):
            app.dictation_control(job['id'], finish=finish)
    wait(app, job)


def test_finish_and_pulse_http_contract_enforces_owner_token_origin_and_exact_fields(app, monkeypatch):
    entered, transcribing, release = recorder(monkeypatch)
    server = ui.Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    def post(path, body, **headers):
        fields = {'Content-Type': 'application/json', 'Origin': server.origin,
                  'X-Prometheus-Token': app.token, **headers}
        try:
            with opener.open(Request(server.origin+path, data=json.dumps(body).encode(), headers=fields), timeout=3) as result:
                return result.status, json.loads(result.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        job = app.submit({'mode': 'dictate', 'prompt': ''})
        assert entered.wait(2)
        for endpoint in ('/api/dictation/pulse', '/api/dictation/finish'):
            for body in ({}, {'id': 12}, {'id': job['id'], 'extra': True}, []):
                assert post(endpoint, body)[0] == 400
            assert post(endpoint, {'id': job['id']}, **{'X-Prometheus-Token': 'bad'})[0] == 403
            assert post(endpoint, {'id': job['id']}, Origin='https://example.com')[0] == 403
            assert post(endpoint, {'id': 'wrong'})[0] == 400
            with monkeypatch.context() as patch:
                patch.setattr(server.owner_access, 'authenticated', lambda session: False)
                assert post(endpoint, {'id': job['id']})[0] == 401
        assert not app.dictation_finish.is_set()
        assert post('/api/dictation/pulse', {'id': job['id']}) == (200, {'recording': True})
        assert post('/api/dictation/finish', {'id': job['id']}) == (200, {'finish_requested': True})
        assert transcribing.wait(2)
        assert post('/api/dictation/pulse', {'id': job['id']})[0] == 400
        release.set()
        assert wait(app, job)['state'] == 'done'
    finally:
        release.set()
        app.stop_dictation()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize('capability', ['voice.record', 'voice.transcribe'])
def test_policy_denial_does_not_collect_audio(monkeypatch, capability):
    from prometheus_assistant import continuous_capture
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', capability)
    monkeypatch.setattr(continuous_capture, '_WaveInput', lambda: pytest.fail('Microphone must stay closed'))
    with pytest.raises(ValueError, match='denied'):
        voice.capture_prompt_continuous()


def test_capture_closes_and_erases_wav_before_returning_transcript(tmp_path, monkeypatch):
    from prometheus_assistant import continuous_capture
    seen = []
    stages = []
    monkeypatch.setattr(voice, '_transcriber', lambda model: None)
    def record(path, **kwargs):
        path.write_bytes(b'PCM test boundary')
        seen.append(path)
        kwargs['on_started']()
    monkeypatch.setattr(continuous_capture, 'record_continuous', record)
    def transcribe(path, **kwargs):
        assert path.is_file() and stages[-1].startswith('Microphone off')
        return {'text': 'six times seven'}
    monkeypatch.setattr(voice, 'transcribe', transcribe)
    assert voice.capture_prompt_continuous(on_stage=stages.append) == 'six times seven'
    assert not seen[0].exists()
