"""Exercise the real client against in-memory transport boundaries only."""
import io
import json
import threading

import pytest

from prometheus_assistant import model_lifecycle, ollama
from prometheus_assistant.model_lifecycle import ModelLifecycleGovernor, task_scope
from prometheus_assistant.ollama import GenerationCancelled, LocalModelError, OllamaClient


@pytest.fixture(autouse=True)
def isolated_governor(monkeypatch):
    governor = ModelLifecycleGovernor()
    monkeypatch.setattr(model_lifecycle, 'governor', governor)
    monkeypatch.setattr(ollama, '_idle_residency', lambda: '15m')
    return governor


@pytest.fixture
def transports(monkeypatch):
    sent = []
    class Socket:
        def setblocking(self, value):
            pass
        def shutdown(self, value):
            pass
        def close(self):
            pass
    class Connection:
        debuglevel = 0
        def __init__(self, *args, **kwargs):
            self.sock = Socket()
        def connect(self):
            pass
        def request(self, method, path, body, headers):
            sent.append(json.loads(body))
        def getresponse(self):
            response = io.BytesIO(json.dumps({'message': {'role': 'assistant', 'content': 'Complete'}, 'done': True}).encode() + b'\n')
            response.status = 200
            return response
        def close(self):
            pass
    class Opener:
        def open(self, request, timeout):
            body = json.loads(request.data) if request.data else {'path': '/api/ps'}
            sent.append(body)
            return io.BytesIO(json.dumps({'models': []} if request.data is None else {'done': True}).encode())
    monkeypatch.setattr(ollama, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr('http.client.HTTPConnection', Connection)
    return sent


def blocking_client(sent, on_read=None):
    client = OllamaClient()
    class Response(io.BytesIO):
        def read(self, *args):
            if on_read:
                on_read()
            return super().read(*args)
    class Opener:
        def open(self, request, timeout):
            sent.append(json.loads(request.data) if request.data else {'path': '/api/ps'})
            result = {'models': []} if request.data is None else {'done': True, 'message': {'content': 'Complete'}}
            return Response(json.dumps(result).encode())
    client.opener = Opener()
    return client


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('mode,optional', [('Critical Recovery', False), ('unknown', False), ('Idle', True), ('Battery Saver', True)])
def test_restricted_admission_makes_zero_transport_requests(transports, stream, mode, optional):
    client = blocking_client(transports)
    with task_scope(mode_provider=lambda: mode, optional=optional):
        with pytest.raises(LocalModelError):
            (client.chat_stream if stream else client.chat)([])
    assert transports == []


@pytest.mark.parametrize('stream', [False, True])
def test_optional_residency_is_zero_at_transport(transports, stream):
    client = blocking_client(transports)
    with task_scope(mode_provider=lambda: 'Balanced', optional=True):
        assert (client.chat_stream if stream else client.chat)([])[0] == 'Complete'
    assert transports[0]['keep_alive'] == 0


def test_final_stream_event_cancellation_discards_result_without_cooldown(transports):
    client = OllamaClient()
    cancel = threading.Event()
    with task_scope(cancel_event=cancel):
        with pytest.raises(GenerationCancelled):
            client.chat_stream([], on_token=lambda token: cancel.set())
    assert len(transports) == 3
    assert transports[1] == {'model': client.model, 'keep_alive': 0}
    assert transports[2] == {'path': '/api/ps'}
    assert client.last_lifecycle['state'] == 'released'
    assert client.chat_stream([])[0] == 'Complete'


def test_blocking_cancellation_discards_return_without_cooldown(transports):
    cancel = threading.Event()
    client = blocking_client(transports, on_read=cancel.set)
    with task_scope(cancel_event=cancel):
        with pytest.raises(GenerationCancelled):
            client.chat([])
    client = blocking_client(transports)
    assert client.chat([])[0] == 'Complete'


@pytest.mark.parametrize('stream', [False, True])
def test_policy_failure_never_creates_backend_cooldown(monkeypatch, transports, stream):
    client = blocking_client(transports)
    capability = 'ollama.stream' if stream else 'ollama.request'
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', capability)
    with pytest.raises(ValueError):
        (client.chat_stream if stream else client.chat)([])
    assert transports == []
    monkeypatch.delenv('PROMETHEUS_DENY_CAPABILITIES')
    assert (client.chat_stream if stream else client.chat)([])[0] == 'Complete'


def test_cleanup_available_after_cancellation_but_cannot_unload_active_operation(isolated_governor, transports):
    client = blocking_client(transports)
    cancel = threading.Event()
    cancel.set()
    payload = {'model': client.model, 'keep_alive': 0}
    with task_scope(mode_provider=lambda: 'Critical Recovery', cancel_event=cancel):
        client._request('/api/generate', payload)
    assert len(transports) == 1
    with isolated_governor.lease(client.base_url, client.model, client.num_ctx):
        with pytest.raises(LocalModelError, match='already running'):
            client._request('/api/generate', payload)
    assert len(transports) == 1



def test_final_stream_mode_change_discards_result(transports):
    client = OllamaClient()
    mode = ['Balanced']
    with task_scope(mode_provider=lambda: mode[0]):
        with pytest.raises(LocalModelError):
            client.chat_stream([], on_token=lambda token: mode.__setitem__(0, 'Critical Recovery'))
    assert len(transports) == 3
    assert transports[1] == {'model': client.model, 'keep_alive': 0}
    assert transports[2] == {'path': '/api/ps'}
    assert client.last_lifecycle['state'] == 'released'
    assert client.chat_stream([])[0] == 'Complete'


@pytest.mark.parametrize('response,expected', [({'models': []}, 'released'), ({'models': [{'name': 'llama3.2:1b'}]}, 'resident'), ({'models': None}, 'unconfirmed')])
def test_release_reports_actual_residency_and_restores_timeout(monkeypatch, transports, response, expected):
    client = OllamaClient(timeout=77)
    original = OllamaClient._request_owned
    def request(cleanup, path, payload=None):
        assert client.timeout == 77
        assert cleanup is not client and cleanup.timeout == .25
        return response if path == '/api/ps' else original(cleanup, path, payload)
    monkeypatch.setattr(OllamaClient, '_request_owned', request)
    assert client.release_model()['state'] == expected
    assert client.timeout == 77
    assert transports == [{'model': client.model, 'keep_alive': 0}]


def test_release_failure_is_safe_and_does_not_create_cooldown(monkeypatch, transports):
    client = OllamaClient(timeout=77)
    original = OllamaClient._request_owned
    def failed(*args):
        raise OSError('closed transport')
    monkeypatch.setattr(OllamaClient, '_request_owned', failed)
    assert client.release_model()['state'] == 'unconfirmed'
    assert client.timeout == 77
    monkeypatch.setattr(OllamaClient, '_request_owned', original)
    assert client.chat_stream([])[0] == 'Complete'


def test_release_never_unloads_an_active_lease(isolated_governor, transports):
    client = OllamaClient(timeout=77)
    with isolated_governor.lease(client.base_url, client.model, client.num_ctx):
        assert client.release_model()['state'] == 'unconfirmed'
        assert transports == []
    assert client.timeout == 77


def test_release_requires_done_before_checking_residency(monkeypatch, transports):
    client = OllamaClient()
    calls = []
    def incomplete(cleanup, path, payload=None):
        calls.append(path)
        return {'done': False}
    monkeypatch.setattr(OllamaClient, '_request_owned', incomplete)
    assert client.release_model()['state'] == 'unconfirmed'
    assert calls == ['/api/generate']
