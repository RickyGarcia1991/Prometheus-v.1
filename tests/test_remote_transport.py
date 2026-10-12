"""Real isolated JSONL subprocess checks; no network, OAuth, or remote device."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from prometheus_assistant import remote_transport as module
from prometheus_assistant.remote_transport import MCPBridge, MCPBridgeError, MCPBridgeTimeout


SERVER = r'''
import json, os, sys, time
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
mode = sys.argv[1]
print('fixture-secret-on-stderr', file=sys.stderr, flush=True)
if mode == 'no-read':
    time.sleep(60)
initialized = False
waiting = []
server_request = None
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
def reply(identity, result):
    send({'jsonrpc': '2.0', 'id': identity, 'result': result})
for line in sys.stdin:
    request = json.loads(line)
    if 'method' not in request:
        if server_request and request.get('id') == 'server-request':
            reply(server_request, {'rejected': request.get('error', {}).get('code') == -32601})
        continue
    method = request['method']
    if method == 'notifications/initialized':
        initialized = True
        continue
    identity = request['id']
    if method == 'initialize':
        reply(identity, {'protocolVersion': request['params']['protocolVersion'],
                         'capabilities': {}, 'serverInfo': {'name': 'fixture', 'version': '1'},
                         'client': request['params']['clientInfo'], 'receivedCapabilities': request['params']['capabilities']})
        continue
    with open('requests.txt', 'a', encoding='utf-8') as record:
        record.write(method + '\n')
    if mode == 'stall':
        continue
    if mode == 'exit':
        sys.exit(0)
    if mode == 'bad-json':
        print('{broken-json', flush=True)
    elif mode == 'invalid-utf8':
        sys.stdout.buffer.write(b'\xff\n'); sys.stdout.buffer.flush()
    elif mode == 'oversize':
        print('x' * 4096, flush=True)
    elif mode == 'duplicate-members':
        print('{"jsonrpc":"2.0","id":%d,"id":%d,"result":{}}' % (identity, identity), flush=True)
    elif mode == 'unknown':
        reply(identity + 100, {})
    elif mode == 'boolean-id':
        reply(True, {})
    elif mode == 'nonfinite':
        reply(identity, float('nan'))
    elif mode == 'both':
        send({'jsonrpc': '2.0', 'id': identity, 'result': {}, 'error': {'code': -1, 'message': 'bad'}})
    elif mode == 'duplicate':
        reply(identity, {'ok': True}); reply(identity, {'ok': True})
    elif mode == 'remote-error':
        send({'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32000, 'message': 'fixture-secret-error', 'data': 'fixture-secret-data'}})
    elif mode == 'reverse':
        waiting.append(request)
        if len(waiting) == 2:
            for item in reversed(waiting): reply(item['id'], item['params'])
            waiting = []
    elif mode == 'server-request':
        server_request = identity
        send({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {'untrusted': 'not instructions'}})
        send({'jsonrpc': '2.0', 'id': 'server-request', 'method': 'sampling/createMessage', 'params': {}})
    elif method == 'environment':
        reply(identity, {'marker': os.environ.get('TRANSPORT_FIXTURE_MARKER'),
                         'inherited': os.environ.get('SENSITIVE_PARENT_TOKEN')})
    else:
        reply(identity, {'method': method, 'params': request['params'], 'initialized': initialized})
'''


@pytest.fixture
def make_bridge(tmp_path):
    script = tmp_path / 'fixture_server.py'
    script.write_text(SERVER, encoding='utf-8')
    bridges = []

    def create(mode='echo', *, timeout=8):
        env = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR') if name in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE='1', TRANSPORT_FIXTURE_MARKER='explicit')
        bridge = MCPBridge([sys.executable, '-B', '-u', str(script), mode], env=env,
                           cwd=tmp_path, timeout=timeout)
        bridges.append(bridge)
        return bridge

    yield create
    for bridge in bridges:
        bridge.close()


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            pytest.fail('Expected subprocess state was not reached.')
        time.sleep(.01)


def test_initialize_and_jsonl_unicode_round_trip(make_bridge):
    bridge = make_bridge()
    result = bridge.initialize(timeout=8)
    assert result['protocolVersion'] == '2025-11-25'
    assert result['client']['name'] == 'Prometheus'
    assert result['receivedCapabilities'] == {}
    assert bridge.initialize() == result
    arguments = {'deviceId': 'explicit-device', 'text': 'line one' + chr(10) + 'Snow: ☃'}
    response = bridge.request('tools/call', arguments)
    assert response == {'method': 'tools/call', 'params': arguments, 'initialized': True}
    assert bridge.alive


def test_only_explicit_environment_is_passed_and_stderr_is_discarded(make_bridge, monkeypatch, capfd):
    monkeypatch.setenv('SENSITIVE_PARENT_TOKEN', 'fixture-do-not-inherit')
    bridge = make_bridge()
    assert bridge.request('environment', {}) == {'marker': 'explicit', 'inherited': None}
    assert 'fixture-secret-on-stderr' not in capfd.readouterr().err


def test_parallel_out_of_order_results_keep_request_identity(make_bridge):
    bridge = make_bridge('reverse')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bridge.request, 'tools/call', {'value': 1})
        second = pool.submit(bridge.request, 'tools/call', {'value': 2})
        assert first.result(timeout=10) == {'value': 1}
        assert second.result(timeout=10) == {'value': 2}


def test_notifications_ignored_and_server_requests_refused(make_bridge):
    bridge = make_bridge('server-request')
    assert bridge.request('tools/call', {}) == {'rejected': True}
    assert bridge.alive


def test_remote_protocol_errors_do_not_expose_secret_payloads(make_bridge):
    bridge = make_bridge('remote-error')
    with pytest.raises(MCPBridgeError, match='code -32000') as caught:
        bridge.request('tools/call', {})
    assert 'fixture-secret' not in str(caught.value)
    assert bridge.alive


@pytest.mark.parametrize('mode', ['bad-json', 'invalid-utf8', 'duplicate-members',
                                  'unknown', 'boolean-id', 'nonfinite', 'both', 'exit'])
def test_malformed_response_fails_closed_and_wakes_waiter(make_bridge, mode):
    bridge = make_bridge(mode)
    with pytest.raises(MCPBridgeError):
        bridge.request('tools/call', {})
    assert not bridge.alive


def test_duplicate_response_fails_connection_even_if_first_reply_arrived(make_bridge):
    bridge = make_bridge('duplicate')
    try:
        assert bridge.request('tools/call', {}) == {'ok': True}
    except MCPBridgeError:
        pass
    wait_until(lambda: not bridge.alive)
    with pytest.raises(MCPBridgeError, match='disconnected'):
        bridge.request('tools/call', {})


def test_input_limit_is_checked_before_unbounded_line_read(make_bridge, monkeypatch):
    monkeypatch.setattr(module, 'MAX_MESSAGE_BYTES', 1024)
    bridge = make_bridge('oversize')
    with pytest.raises(MCPBridgeError):
        bridge.request('tools/call', {})
    assert not bridge.alive


def test_timeout_has_no_retry_and_closes_owned_bridge(make_bridge, tmp_path):
    bridge = make_bridge('stall')
    bridge.initialize(timeout=8)
    with pytest.raises(MCPBridgeTimeout, match='not retried'):
        bridge.request('mutation', {}, timeout=.15)
    assert not bridge.alive
    assert (tmp_path / 'requests.txt').read_text(encoding='utf-8').splitlines() == ['mutation']
    with pytest.raises(MCPBridgeError, match='disconnected'):
        bridge.request('mutation', {})


def test_blocked_pipe_write_is_interruptible_by_request_deadline(make_bridge):
    bridge = make_bridge('no-read')
    started = time.monotonic()
    with pytest.raises(MCPBridgeTimeout):
        bridge.request('mutation', {'body': 'x' * 1_000_000}, timeout=.15)
    assert time.monotonic() - started < 2
    assert not bridge.alive


def test_close_wakes_pending_call_and_does_not_wait_under_state_lock(make_bridge):
    bridge = make_bridge('stall')
    bridge.initialize(timeout=8)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(bridge.request, 'tools/call', {}, 30)
        wait_until(lambda: len(bridge._pending) == 1)
        started = time.monotonic()
        bridge.close()
        assert time.monotonic() - started < 2
        with pytest.raises(MCPBridgeError, match='disconnected'):
            pending.result(timeout=2)
    assert not bridge.alive
    bridge.close()  # Idempotent cleanup never starts another process.


def test_pending_request_limit_is_bounded(make_bridge, monkeypatch):
    monkeypatch.setattr(module, 'MAX_PENDING_REQUESTS', 2)
    bridge = make_bridge('stall')
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(bridge.request, 'tools/call', {}, 8) for _ in range(2)]
        wait_until(lambda: len(bridge._pending) == 2)
        with pytest.raises(MCPBridgeError, match='Too many'):
            bridge.request('tools/call', {})
        bridge.close()
        for request in pending:
            with pytest.raises(MCPBridgeError):
                request.result(timeout=2)


def test_outbound_oversize_or_invalid_json_is_not_sent(make_bridge, tmp_path, monkeypatch):
    bridge = make_bridge()
    monkeypatch.setattr(module, 'MAX_MESSAGE_BYTES', 1024)
    with pytest.raises(ValueError, match='size limit'):
        bridge.request('mutation', {'body': 'x' * 2048})
    with pytest.raises(ValueError, match='finite'):
        bridge.request('mutation', {'value': float('nan')})
    assert not (tmp_path / 'requests.txt').exists()
    assert bridge.request('safe', {})['method'] == 'safe'


@pytest.mark.parametrize('timeout', [0, -1, True, float('inf'), float('nan')])
def test_invalid_timeout_never_launches_bridge(tmp_path, timeout):
    with pytest.raises(ValueError, match='finite'):
        MCPBridge([sys.executable], env={}, cwd=tmp_path, timeout=timeout)


def test_command_string_is_never_accepted_as_shell(tmp_path):
    with pytest.raises(ValueError, match='argument list'):
        MCPBridge('echo unsafe', env={}, cwd=tmp_path)
