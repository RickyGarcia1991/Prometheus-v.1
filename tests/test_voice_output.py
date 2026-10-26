"""Speech output boundaries; no test opens an input or output audio device."""
import base64
import json
import threading
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import pytest
from prometheus_assistant import ui_server as ui, voice


@pytest.mark.parametrize('text', ['', '   ', 'x'*4001, 'x\x00y', None, 42])
def test_invalid_speech_never_starts_a_process(text, monkeypatch):
    monkeypatch.setattr(voice.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not launch speech'))
    with pytest.raises(ValueError):
        voice.synthesize_wav(text)


def test_speech_http_boundaries_and_cleanup(tmp_path, monkeypatch):
    app = ui.Interface(tmp_path/'memory.sqlite3')
    server = ui.Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    calls = []
    def synthesize(text, *, cancelled):
        calls.append(text)
        assert not cancelled()
        if text == 'fail':
            raise ValueError('Synthetic voice failure')
        return b'RIFF synthetic PCM'
    monkeypatch.setattr(voice, 'synthesize_wav', synthesize)
    response_lock_states = []
    original_respond = ui.Handler.respond
    def observe_response(handler, status, payload, *args, **kwargs):
        if handler.path == '/api/speech' and status == 200:
            response_lock_states.append(app.speech_lock.locked())
        return original_respond(handler, status, payload, *args, **kwargs)
    monkeypatch.setattr(ui.Handler, 'respond', observe_response)
    def request(body, **headers):
        fields = {'Content-Type':'application/json', 'Origin':server.origin, 'X-Prometheus-Token':app.token, **headers}
        req = Request(server.origin+'/api/speech', data=json.dumps(body).encode(), headers=fields)
        try:
            with opener.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        assert request({'text':'secret'}, Origin='https://evil.invalid')[0] == 403
        assert request({'text':'secret'}, **{'X-Prometheus-Token':'bad'})[0] == 403
        assert request({'text':'secret', 'path':'arbitrary.wav'})[0] == 400
        assert calls == []
        app.speech_lock.acquire()
        try:
            assert request({'text':'concurrent'})[0] == 409
        finally:
            app.speech_lock.release()
        assert calls == []
        assert request({'text':'fail'})[0] == 400
        status, result = request({'text':'literal $(text)'})
        assert status == 200 and base64.b64decode(result['audio']) == b'RIFF synthetic PCM'
        assert calls == ['fail', 'literal $(text)']
        assert response_lock_states == [False], 'The next speech chunk must be able to prepare as soon as this response arrives.'
        app.closing = True
        assert request({'text':'closed'})[0] == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


@pytest.mark.skipif(voice.sys.platform not in {'win32', 'linux'}, reason='Supported native speech runtime required')
def test_cancel_reaches_stalled_engine_without_blocking_input(monkeypatch):
    import subprocess
    import sys
    import time
    children = []
    original = subprocess.Popen
    text = '例' * 4000
    def start(argv, **options):
        stream = options['stdin']
        assert stream != subprocess.PIPE
        assert stream.read() == text.encode('utf-8')
        stream.seek(0)
        child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **options)
        children.append(child)
        return child
    monkeypatch.setattr(voice.subprocess, 'Popen', start)
    before = time.monotonic()
    with pytest.raises(ValueError, match='stopped'):
        voice.synthesize_wav(text, cancelled=lambda: bool(children))
    assert time.monotonic()-before < 5
    assert children and children[0].poll() is not None
