"""Exercise the real loopback HTTP handler with a local fake provider boundary."""
import http.client
import json
from types import SimpleNamespace
import threading
import time

import pytest

from prometheus_assistant import ui_server as ui


_MISSING = object()


class Provider:
    """No subprocess, OAuth, remote service or network is used by this provider."""
    def __init__(self):
        self.started = 0
        self.calls = []

    def factory(self):
        self.started += 1
        return self

    def initialize(self, **kwargs):
        return {}

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == 'tools/list':
            return {'tools': [
                {'name': 'list_devices', 'inputSchema': {'type': 'object'}},
                {'name': 'who_am_i', 'inputSchema': {'type': 'object'}},
                {'name': 'inspect_file', 'inputSchema': {'type': 'object',
                    'required': ['path'], 'additionalProperties': False,
                    'properties': {'deviceId': {'type': 'string'}, 'path': {'type': 'string'}}}}]}
        if params['name'] == 'list_devices':
            return {'structuredContent': {'devices': [
                {'id': 'observed-computer', 'name': 'Test computer', 'status': 'online'}]}}
        if params['name'] == 'who_am_i':
            return {'content': [{'type': 'text', 'text': '{"id":"fixture-account"}'}]}
        return {'content': [{'type': 'text', 'text': 'Fixture file result'}]}

    def close(self):
        pass


@pytest.fixture
def remote_http(tmp_path):
    provider = Provider()
    app = ui.Interface(tmp_path / 'memory.sqlite3')
    app.remote.factory = provider.factory
    app.remote.audit_root = tmp_path / 'remote-audit'
    server = ui.Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path='/api/remote/state', *, method='GET', body=_MISSING, raw=None, headers=None):
        payload = raw if raw is not None else None if body is _MISSING else json.dumps(body).encode('utf-8')
        fields = {'Host': f'127.0.0.1:{server.server_port}', 'Origin': server.origin,
                  'X-Prometheus-Token': app.token}
        if method == 'POST':
            fields.update({'Content-Type': 'application/json', 'Content-Length': str(len(payload or b''))})
        for name, value in (headers or {}).items():
            if value is None:
                fields.pop(name, None)
            else:
                fields[name] = value
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in fields.items():
                client.putheader(name, value)
            client.endheaders(payload)
            response = client.getresponse()
            return response.status, response.read(), response.headers
        finally:
            client.close()

    yield SimpleNamespace(app=app, provider=provider, request=request)
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    app.close()


def settled(http):
    deadline = time.monotonic() + 4
    while True:
        code, body, _ = http.request()
        assert code == 200
        state = json.loads(body)
        if state['phase'] not in {'connecting', 'working'}:
            return state
        if time.monotonic() >= deadline:
            raise AssertionError('Remote fixture did not settle')
        time.sleep(.005)


def test_startup_and_authenticated_state_never_connect_to_provider(remote_http):
    http = remote_http
    assert http.app.remote.client is None
    for path in ('/', '/api/bootstrap', '/api/remote/state', '/api/remote/state'):
        assert http.request(path)[0] == 200
    assert http.provider.started == 0 and http.provider.calls == []
    assert not http.app.memory_path.exists()
    assert not http.app.remote.audit_root.exists()


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('headers', [
    {'Host': 'foreign.example'}, {'Host': None},
    {'Origin': 'https://foreign.example'}, {'Origin': 'null'},
    {'X-Prometheus-Token': None}, {'X-Prometheus-Token': 'incorrect'},
    {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
])
def test_remote_endpoints_reject_wrong_host_origin_or_token(remote_http, method, headers):
    path = '/api/remote/action' if method == 'POST' else '/api/remote/state'
    code, _, _ = remote_http.request(path, method=method,
        body={'action': 'connect'} if method == 'POST' else _MISSING, headers=headers)
    assert code == 403
    assert remote_http.provider.started == 0 and remote_http.provider.calls == []


def test_post_requires_origin_but_same_origin_get_can_omit_it(remote_http):
    assert remote_http.request(headers={'Origin': None})[0] == 200
    assert remote_http.request('/api/remote/action', method='POST', body={'action': 'connect'},
                               headers={'Origin': None})[0] == 403
    assert remote_http.provider.started == 0


def test_repeated_rejected_posts_receive_response_without_dispatch(remote_http):
    for _ in range(20):
        code, _, _ = remote_http.request('/api/remote/action', method='POST',
            body={'action': 'connect'}, headers={'Sec-Fetch-Site': 'cross-site'})
        assert code == 403
    assert remote_http.provider.started == 0 and remote_http.provider.calls == []


def test_rejected_upload_lingering_close_is_bounded_and_response_is_first():
    events = []
    class Connection:
        def gettimeout(self): return 8
        def settimeout(self, seconds): events.append(('timeout', seconds))
        def shutdown(self, side): events.append(('shutdown', side))
    class Input:
        def read1(self, size):
            assert events[0] == ('response', 415)
            assert 0 < size <= 24576
            events.append(('read', size))
            return b'x'*size
    handler = SimpleNamespace(connection=Connection(), rfile=Input(),
                              respond=lambda code, data: events.append(('response', code)))
    ui.Handler.reject(handler, 415, {})
    assert handler.close_connection is True
    assert sum(size for event, size in events if event == 'read') == 24576
    assert events[-1] == ('timeout', 8)


@pytest.mark.parametrize('path', ['/api/remote/action', '/api/remote/state?action=connect', '/api/remote/connect'])
def test_get_routes_cannot_start_remote_actions(remote_http, path):
    assert remote_http.request(path)[0] == 404
    assert remote_http.provider.started == 0 and remote_http.provider.calls == []


@pytest.mark.parametrize('headers', [
    {'Content-Type': None}, {'Content-Type': 'text/plain'},
    {'Content-Type': 'application/x-www-form-urlencoded'},
    {'Transfer-Encoding': 'chunked'},
])
def test_remote_post_requires_unambiguous_json_content(remote_http, headers):
    for _ in range(5):
        code, _, _ = remote_http.request('/api/remote/action', method='POST', body={'action': 'connect'}, headers=headers)
        assert code == 415
    assert remote_http.provider.started == 0


@pytest.mark.parametrize('body', [
    [], None, 'connect', {}, {'action': 'unsupported'}, {'action': []},
    {'action': 'connect', 'command': 'unexpected'},
    {'action': 'prepare', 'tool': 'inspect_file'},
    {'action': 'execute', 'pending_id': 'not-reviewed', 'arguments': {'command': 'injected'}},
])
def test_remote_action_field_validation_through_http(remote_http, body):
    assert remote_http.request('/api/remote/action', method='POST', body=body)[0] == 400
    assert remote_http.provider.started == 0 and remote_http.provider.calls == []


def test_malformed_json_does_not_reach_provider(remote_http):
    assert remote_http.request('/api/remote/action', method='POST', raw=b'{broken')[0] == 400
    assert remote_http.provider.started == 0


@pytest.mark.parametrize('size, expected', [(150000, 200), (150001, 413)])
def test_remote_request_byte_limit(remote_http, size, expected):
    base = b'{"action":"cancel"}'
    payload = base + b' ' * (size - len(base))
    # A rejected length must get a response before any body is uploaded. Sending
    # a large rejected body races Windows' reset of a socket with unread bytes.
    raw = payload if expected == 200 else b''
    assert remote_http.request('/api/remote/action', method='POST', raw=raw,
                               headers={'Content-Length': str(size)})[0] == expected
    assert remote_http.provider.started == 0


def test_remote_oversize_guard_runs_before_json_parsing(remote_http):
    assert remote_http.request('/api/remote/action', method='POST', raw=b'',
                               headers={'Content-Length': '150001'})[0] == 413
    assert remote_http.provider.started == 0


@pytest.mark.parametrize('length, expected', [('0', 413), ('-1', 413), ('nope', 400)])
def test_invalid_remote_request_lengths_are_rejected(remote_http, length, expected):
    assert remote_http.request('/api/remote/action', method='POST', headers={'Content-Length': length})[0] == expected
    assert remote_http.provider.started == 0


def test_exact_prepared_action_dispatch_and_replay_protection_over_http(remote_http):
    http = remote_http
    assert http.request('/api/remote/action', method='POST', body={'action': 'connect'})[0] == 200
    assert settled(http)['phase'] == 'ready'
    assert http.provider.started == 1
    assert http.request('/api/remote/action', method='POST',
        body={'action': 'select', 'device_id': 'observed-computer'})[0] == 200
    code, raw, _ = http.request('/api/remote/action', method='POST',
        body={'action': 'prepare', 'tool': 'inspect_file', 'arguments': {'path': 'reviewed.txt'}})
    assert code == 200
    pending = json.loads(raw)['pending']
    assert pending['arguments'] == {'path': 'reviewed.txt', 'deviceId': 'observed-computer'}
    assert not [params for _, params in http.provider.calls if params.get('name') == 'inspect_file']
    # Repeated state reads are observations and cannot dispatch the pending action.
    assert http.request()[0] == http.request()[0] == 200
    assert not [params for _, params in http.provider.calls if params.get('name') == 'inspect_file']
    payload = {'action': 'execute', 'pending_id': pending['id']}
    assert http.request('/api/remote/action', method='POST', body=payload)[0] == 200
    assert settled(http)['result']['content'][0]['text'] == 'Fixture file result'
    assert http.request('/api/remote/action', method='POST', body=payload)[0] == 400
    sent = [params for _, params in http.provider.calls if params.get('name') == 'inspect_file']
    assert sent == [{'name': 'inspect_file', 'arguments': pending['arguments']}]


def test_remote_script_and_image_csp_without_script_relaxation(remote_http):
    code, content, headers = remote_http.request('/remote.js', headers={'X-Prometheus-Token': None})
    assert code == 200 and b'/api/remote/action' in content
    assert headers['Content-Type'].startswith('text/javascript')
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert headers['Cache-Control'] == 'no-store'
    policy = headers['Content-Security-Policy']
    directives = {parts[0]: parts[1:] for part in policy.split(';') if (parts := part.split())}
    assert directives['img-src'] == ["'self'", 'data:']
    assert directives['script-src'] == ["'self'"]
    assert directives['connect-src'] == ["'self'"]
    assert directives['frame-ancestors'] == ["'none'"]
    assert remote_http.provider.started == 0
