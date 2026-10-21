"""Preparation must load a selected local helper without recording or saving chat."""
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import ui_server as ui
from prometheus_assistant.ollama import GenerationCancelled, OllamaClient


@pytest.fixture(autouse=True)
def synthetic_speech(monkeypatch):
    from prometheus_assistant import voice
    calls = []
    def synthesize(text, *, cancelled):
        assert not cancelled()
        calls.append(('synthesize', text))
        return b'synthetic WAV supplied to the fake decoder'
    def transcribe(path, *, model, cancel_event):
        assert model == 'auto' and not cancel_event.is_set()
        assert path.is_file()
        calls.append(('transcribe', path))
        return {'text': 'Private decoded calibration must be discarded', 'model': 'base.en', 'local': True}
    monkeypatch.setattr(voice, 'synthesize_wav', synthesize)
    monkeypatch.setattr(voice, 'transcribe', transcribe)
    return calls


def test_empty_preparation_is_valid_but_cannot_include_a_question():
    assert ui.validate_job({'mode': 'voice_prepare', 'prompt': ''})['mode'] == 'voice_prepare'
    with pytest.raises(ValueError):
        ui.validate_job({'mode': 'voice_prepare', 'prompt': 'write a message'})


def test_preload_uses_empty_messages_and_same_generation_options(monkeypatch):
    seen = []
    def stream(self, messages, **kwargs):
        seen.append((messages, kwargs, self.num_ctx, self.num_thread))
        self.last_metrics = {'model': self.model, 'load_seconds': .25}
        return '', 0
    monkeypatch.setattr(OllamaClient, '_chat_stream', stream)
    client = OllamaClient(model='qwen3:1.7b', num_ctx=3072, num_thread=4)
    assert client.prepare(cancel_event=None)['load_seconds'] == .25
    assert seen == [([], {'cancel_event': None, 'preparing': True}, 3072, 4)]


def test_preparation_selects_real_general_profile_twice_without_history(monkeypatch, synthetic_speech):
    from prometheus_assistant import voice_preparation as prep
    profiles = [SimpleNamespace(model='llama3.2:1b', context=2048, cpu_threads=3)]
    calls = []
    monkeypatch.setattr(prep, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=3))
    monkeypatch.setattr(prep, 'candidates', lambda prompt, names, hardware, evidence, **kw:
                        (calls.append(('selection', prompt, names, kw)) or ('general', profiles)))
    class Client:
        def __init__(self, **kw): self.kw = kw
        def resident_memory_gib(self): return 1.2
        def ensure_local_model(self): calls.append(('local', self.kw))
        def prepare(self, **kw):
            assert not kw['cancel_event'].is_set()
            calls.append(('prepare', self.kw))
            return {'load_seconds': .5}
    monkeypatch.setattr(prep, 'OllamaClient', Client)
    result = prep.prepare_voice(installed=lambda: {'llama3.2:1b'}, evidence=object(),
        ensure_runtime=lambda **kw: calls.append(('runtime',)), cancel_event=threading.Event(),
        lease_alive=lambda: True)
    assert result['voice_ready'] is True
    assert result['model'] == 'llama3.2:1b'
    assert result['context'] == 2048 and result['threads'] == 3 and result['batch'] == 32
    assert len([r for r in calls if r[0] == 'selection']) == 2
    assert [r for r in calls if r[0] == 'prepare'][0][1] == {
        'model': 'llama3.2:1b', 'num_ctx': 2048, 'num_thread': 3, 'timeout': 120}
    assert 'reply' not in result and 'session' not in result
    assert result['recognizer_model'] == 'base.en'
    assert synthetic_speech[0] == ('synthesize', prep.CALIBRATION_PHRASE)
    assert synthetic_speech[1][0] == 'transcribe' and not synthetic_speech[1][1].exists()
    assert 'Private decoded' not in str(result)
    assert all(0 <= result[key] <= 3600 for key in ('model_preparation_seconds',
        'tts_preparation_seconds', 'asr_preparation_seconds', 'preparation_seconds'))


@pytest.mark.parametrize('condition', ['cancel', 'lease', 'memory'])
def test_preparation_refuses_before_runtime_or_microphone(monkeypatch, condition):
    from prometheus_assistant import voice_preparation as prep
    event = threading.Event()
    if condition == 'cancel': event.set()
    monkeypatch.setattr(prep, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=.2 if condition == 'memory' else 3))
    with pytest.raises((GenerationCancelled, ValueError)):
        prep.prepare_voice(installed=lambda: pytest.fail('inventory should not start'), evidence=None,
            ensure_runtime=lambda **kw: pytest.fail('runtime should not start'), cancel_event=event,
            lease_alive=lambda: condition != 'lease')


def test_preparation_pulse_is_bound_to_current_owner_and_cannot_revive(tmp_path):
    app = ui.Interface(tmp_path/'private'/'memory.sqlite3')
    try:
        app.current = 'test'
        app.jobs['test'] = {'action': 'voice_prepare', 'state': 'running', 'cancellable': True}
        app._speech_owners['test'] = {'binding': 'owner-a'}
        app.dictation_last_pulse = ui.time.monotonic()
        with pytest.raises(ValueError): app.voice_preparation_pulse('test', 'owner-b')
        assert app.voice_preparation_pulse('test', 'owner-a') == {'preparing': True}
        app.dictation_last_pulse -= 21
        with pytest.raises(ValueError): app.voice_preparation_pulse('test', 'owner-a')
        app.dictation_last_pulse = ui.time.monotonic()
        app.stop_dictation()
        assert app.cancel_event.is_set()
        with pytest.raises(ValueError): app.voice_preparation_pulse('test', 'owner-a')
    finally:
        app.current = None
        app.close()


@pytest.mark.parametrize('reply,tokens,accepted', [('', 0, True), ('invented', 0, False), ('', 1, False)])
def test_empty_load_terminal_is_only_valid_for_preparation(monkeypatch, reply, tokens, accepted):
    from test_ollama import stream_events
    from prometheus_assistant.ollama import LocalModelError
    event = {'message': {'role': 'assistant', 'content': reply}, 'done': True,
             'done_reason': 'load', 'eval_count': tokens, 'load_duration': 250000000}
    client = OllamaClient()
    stream_events(monkeypatch, [event])
    if accepted:
        assert client.prepare()['load_seconds'] == .25
    else:
        with pytest.raises(LocalModelError): client.prepare()
    if not reply:
        # Check a distinct ordinary request, not the prior failed preparation's
        # intentional backend cooldown (covered by lifecycle tests).
        from prometheus_assistant import model_lifecycle
        monkeypatch.setattr(model_lifecycle, 'governor', model_lifecycle.ModelLifecycleGovernor())
        stream_events(monkeypatch, [event])
        with pytest.raises(LocalModelError, match='complete answer'): client.chat_stream([])


@pytest.mark.parametrize('reason', ['cancel', 'lease', 'memory'])
def test_cancellation_during_preload_never_returns_ready_and_releases_owned_load(monkeypatch, reason):
    from prometheus_assistant import voice_preparation as prep
    event = threading.Event()
    live = {'lease': True, 'free': 3}
    unloaded = []
    monkeypatch.setattr(prep, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=live['free']))
    monkeypatch.setattr(prep, 'candidates', lambda *a, **kw:
        ('general', [SimpleNamespace(model='llama3.2:1b', context=2048, cpu_threads=2)]))
    class Client:
        def __init__(self, **kw): self.kw = kw
        def resident_memory_gib(self): return 0
        def ensure_local_model(self): pass
        def prepare(self, *, cancel_event):
            if reason == 'cancel': event.set()
            if reason == 'lease': live['lease'] = False
            if reason == 'memory': live['free'] = .2; cancel_event.next_memory_check = 0
            if cancel_event.is_set(): raise GenerationCancelled('cancelled')
            pytest.fail('Cancellation signal was not propagated into the preload transport')
        def _request(self, path, payload): unloaded.append((path, payload, self.kw))
    monkeypatch.setattr(prep, 'OllamaClient', Client)
    with pytest.raises(GenerationCancelled, match='Microphone stayed off'):
        prep.prepare_voice(installed=lambda: {'llama3.2:1b'}, evidence=None,
            ensure_runtime=lambda **kw: None, cancel_event=event, lease_alive=lambda: live['lease'])
    assert unloaded == [('/api/generate', {'model': 'llama3.2:1b', 'keep_alive': 0, 'stream': False},
                         {'model': 'llama3.2:1b', 'timeout': 2})]


def test_preparation_job_never_opens_cache_microphone_or_history(tmp_path, monkeypatch):
    import time
    from prometheus_assistant import voice_preparation as prep, voice
    app = ui.Interface(tmp_path/'private'/'memory.sqlite3')
    monkeypatch.setattr(prep, 'prepare_voice', lambda **kw: {'voice_ready': True, 'model': 'fixture'})
    monkeypatch.setattr(ui, 'reference_session', lambda: pytest.fail('preparation has no reference cache'))
    monkeypatch.setattr(voice, 'capture_prompt_continuous', lambda **kw: pytest.fail('microphone must stay off'))
    monkeypatch.setattr(ui, 'MemoryStore', lambda *a: pytest.fail('preparation has no history'))
    try:
        job = app.submit({'mode': 'voice_prepare', 'prompt': ''}, approval_binding='owner')
        deadline = time.monotonic()+2
        while app.current is not None and time.monotonic() < deadline: time.sleep(.01)
        row = app.jobs[job['id']]
        assert row['state'] == 'done' and row['result']['voice_ready'] is True
        assert not app.memory_path.exists()
        assert row['action'] == 'voice_prepare' and app.state == 'idle'
    finally: app.close()


def test_native_preload_payload_matches_following_chat_options(monkeypatch):
    import io
    import json
    sent = []
    class Socket:
        def setblocking(self, value): pass
        def shutdown(self, value): pass
    class Connection:
        debuglevel = 0
        def __init__(self, *a, **kw): self.sock = Socket()
        def connect(self): pass
        def request(self, method, path, body, headers):
            assert method == 'POST' and path == '/api/chat'
            sent.append(json.loads(body))
        def getresponse(self):
            value = {'message': {'content': '' if len(sent) == 1 else 'Hello'}, 'done': True}
            response = io.BytesIO(json.dumps(value).encode()+b'\n')
            response.status = 200
            return response
        def close(self): pass
    monkeypatch.setattr('http.client.HTTPConnection', Connection)
    client = OllamaClient(model='qwen3:1.7b', num_ctx=3072, num_thread=4)
    client.prepare()
    client.chat_stream([{'role': 'user', 'content': 'Hello'}])
    assert sent[0]['messages'] == []
    assert sent[0]['options'] == sent[1]['options'] == {
        'num_ctx': 3072, 'num_predict': 384, 'temperature': .2, 'num_thread': 4, 'num_batch': 128}
    assert sent[0]['think'] is False and sent[0]['stream'] is True
    assert sent[0]['keep_alive'] == sent[1]['keep_alive']


def test_preparation_pulse_http_authentication_and_expiry(tmp_path, monkeypatch):
    import hashlib
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, ProxyHandler, build_opener
    app = ui.Interface(tmp_path/'private'/'memory.sqlite3')
    app.current = 'fixture'
    app.jobs['fixture'] = {'action': 'voice_prepare', 'state': 'running', 'cancellable': True}
    cookie = 'a'*48
    app._speech_owners['fixture'] = {'binding': hashlib.sha256(cookie.encode()).hexdigest()}
    app.dictation_last_pulse = ui.time.monotonic()
    server = ui.Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    def post(body, **headers):
        fields = {'Content-Type': 'application/json', 'Origin': server.origin,
                  'X-Prometheus-Token': app.token, 'Cookie': 'prometheus_owner='+cookie, **headers}
        try:
            with opener.open(Request(server.origin+'/api/voice/pulse', data=json.dumps(body).encode(),
                                     headers=fields), timeout=3) as result:
                return result.status, json.loads(result.read())
        except HTTPError as error: return error.code, json.loads(error.read())
    try:
        assert post({'id': 'fixture'}) == (200, {'preparing': True})
        assert post({'id': 'fixture'}, Cookie='prometheus_owner='+'b'*48)[0] == 400
        assert post({'id': 'fixture'}, **{'X-Prometheus-Token': 'bad'})[0] == 403
        assert post({'id': 'fixture'}, Origin='https://example.com')[0] == 403
        for body in ({}, {'id': 1}, {'id': 'fixture', 'extra': True}, []): assert post(body)[0] == 400
        with monkeypatch.context() as patch:
            patch.setattr(server.owner_access, 'authenticated', lambda session: False)
            assert post({'id': 'fixture'})[0] == 401
        app.dictation_last_pulse -= 21
        assert post({'id': 'fixture'})[0] == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        app.current = None
        app.close()


@pytest.mark.parametrize('reason', ['End', 'lease', 'lock'])
@pytest.mark.parametrize('phase', ['after-llm', 'before-asr', 'during-asr'])
def test_stopped_preparation_after_model_load_never_becomes_ready(tmp_path, monkeypatch, reason, phase):
    import time
    from prometheus_assistant import voice_preparation as prep, voice
    entered, release = threading.Event(), threading.Event()
    unloaded, decoder_calls = [], []
    monkeypatch.setattr(prep, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=4))
    monkeypatch.setattr(prep, 'candidates', lambda *a, **kw:
        ('general', [SimpleNamespace(model='llama3.2:1b', context=2048, cpu_threads=2)]))
    monkeypatch.setattr(ui, 'installed_models', lambda: {'llama3.2:1b'})
    class Client:
        def __init__(self, **kw): pass
        def resident_memory_gib(self): return 0
        def ensure_local_model(self): pass
        def prepare(self, **kw):
            if phase == 'after-llm': entered.set(); assert release.wait(3)
            return {}
        def _request(self, path, body): unloaded.append(body)
    monkeypatch.setattr(prep, 'OllamaClient', Client)
    app = ui.Interface(tmp_path/'private'/'memory.sqlite3')
    monkeypatch.setattr(app, 'ensure_runtime', lambda **kw: None)
    original_progress = app.progress
    def progress(message, **kw):
        original_progress(message, **kw)
        if phase == 'before-asr' and message.startswith('Preparing local speech recognition'):
            entered.set(); assert release.wait(3)
    monkeypatch.setattr(app, 'progress', progress)
    def transcribe(path, *, model, cancel_event):
        decoder_calls.append(model)
        if phase == 'during-asr':
            entered.set(); assert release.wait(3)
        voice._cancelled(cancel_event)
        return {'text': 'calibration', 'model': 'base.en'}
    monkeypatch.setattr(voice, 'transcribe', transcribe)
    monkeypatch.setattr(voice, 'capture_prompt_continuous', lambda **kw: pytest.fail('Microphone must stay off'))
    try:
        job = app.submit({'mode': 'voice_prepare', 'prompt': ''}, approval_binding='owner')
        assert entered.wait(3), phase
        if reason == 'End': app.cancel(job['id'])
        elif reason == 'lease': app.dictation_last_pulse -= 21
        else: app.stop_dictation()
        release.set()
        deadline = time.monotonic()+3
        while app.current is not None and time.monotonic() < deadline: time.sleep(.01)
        row = app.jobs[job['id']]
        assert row['state'] == 'cancelled' and 'result' not in row
        assert app.state == 'idle' and not app.memory_path.exists()
        assert len(unloaded) == 1 and unloaded[0]['keep_alive'] == 0
        assert decoder_calls == (['auto'] if phase == 'during-asr' else [])
    finally:
        release.set(); app.close()


@pytest.mark.parametrize('phase', ['synthesize_wav', 'transcribe'])
def test_real_speech_preparation_failure_is_not_ready_or_cancelled(monkeypatch, phase):
    from prometheus_assistant import voice_preparation as prep, voice
    unloaded = []
    monkeypatch.setattr(prep, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=4))
    monkeypatch.setattr(prep, 'candidates', lambda *a, **kw:
        ('general', [SimpleNamespace(model='llama3.2:1b', context=2048, cpu_threads=2)]))
    class Client:
        def __init__(self, **kw): pass
        def resident_memory_gib(self): return 0
        def ensure_local_model(self): pass
        def prepare(self, **kw): return {}
        def _request(self, path, body): unloaded.append(body)
    monkeypatch.setattr(prep, 'OllamaClient', Client)
    def fail(*a, **kw): raise ValueError('Native speech child failed')
    monkeypatch.setattr(voice, phase, fail)
    with pytest.raises(ValueError, match='Native speech child failed'):
        prep.prepare_voice(installed=lambda: {'llama3.2:1b'}, evidence=None, ensure_runtime=lambda **kw: None)
    assert len(unloaded) == 1
