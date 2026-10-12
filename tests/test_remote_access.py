import copy
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from prometheus_assistant.remote_access import RemoteAccess, parse_devices, validate_arguments, verify_runtime
from prometheus_assistant import remote_access


ID = 'dell-device-123'
SCHEMA = {'type': 'object', 'properties': {'deviceId': {'type': 'string'},
    'command': {'type': 'string', 'minLength': 1}}, 'required': ['command'], 'additionalProperties': False}


class Bridge:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.online = True
        self.responses = [{'content': [{'type': 'text', 'text': 'finished'}]}]
        self.error = None
        self.account = 'test-account'

    def initialize(self, timeout=180):
        return {}

    def request(self, method, params, timeout=None):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'tools/list':
            return {'tools': [
                {'name': 'list_devices', 'inputSchema': {'type': 'object'}},
                {'name': 'who_am_i', 'inputSchema': {'type': 'object'}},
                {'name': 'get_tool_result', 'inputSchema': {'type': 'object', 'properties': {'call_id': {'type': 'string'}}}},
                {'name': 'start_process', 'description': 'Run command', 'inputSchema': SCHEMA}]}
        if params['name'] == 'list_devices':
            return {'structuredContent': {'devices': [{'id': ID, 'name': 'Dell', 'online': self.online}]}}
        if params['name'] == 'who_am_i':
            return {'content': [{'type': 'text', 'text': json.dumps({'id': self.account, 'email': 'not-retained@example.invalid'})}]}
        if self.error:
            raise self.error
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def settled(manager):
    deadline = time.monotonic()+4
    while manager.state()['phase'] in {'working', 'connecting'}:
        if time.monotonic() > deadline:
            raise AssertionError('Manager did not settle')
        time.sleep(.005)
    return manager.state()


@pytest.fixture
def connection(tmp_path):
    bridge = Bridge()
    manager = RemoteAccess(bridge_factory=lambda: bridge, audit_root=tmp_path)
    assert manager.state()['phase'] == 'disconnected'
    assert not bridge.calls
    manager.action({'action': 'connect'})
    assert settled(manager)['phase'] == 'ready'
    manager.action({'action': 'select', 'device_id': ID})
    yield manager, bridge
    manager.close()


def prepare(manager):
    return manager.action({'action': 'prepare', 'tool': 'start_process',
                           'arguments': {'command': 'echo test'}})['pending']


def test_observed_targets_and_single_use_exact_dispatch(connection, tmp_path):
    manager, bridge = connection
    assert [t['name'] for t in manager.state()['tools']] == ['start_process']
    args = {'command': 'echo test'}
    pending = manager.action({'action': 'prepare', 'tool': 'start_process', 'arguments': args})['pending']
    args['command'] = 'changed after preview'
    assert pending['device_id'] == ID
    pending['arguments']['command'] = 'changed return value'
    manager.action({'action': 'execute', 'pending_id': pending['id']})
    result = settled(manager)
    actual = [p for _, p in bridge.calls if p.get('name') == 'start_process']
    assert actual == [{'name': 'start_process', 'arguments': {'command': 'echo test', 'deviceId': ID}}]
    assert result['result']['content'][0]['text'] == 'finished'
    with pytest.raises(ValueError):
        manager.action({'action': 'execute', 'pending_id': pending['id']})
    log = (manager.audit_root/'remote-actions.jsonl').read_text()
    assert 'echo test' not in log and 'finished' not in log
    assert 'arguments_sha256' in log


def test_different_or_unobserved_device_cannot_be_injected(connection):
    manager, bridge = connection
    with pytest.raises(ValueError):
        manager.action({'action': 'select', 'device_id': 'other'})
    with pytest.raises(ValueError):
        manager.action({'action': 'prepare', 'tool': 'start_process',
                        'arguments': {'command': 'anything', 'deviceId': 'other'}})
    assert not [p for _, p in bridge.calls if p.get('name') == 'start_process']


@pytest.mark.parametrize('action', ['refresh', 'disconnect', 'connect', 'cancel'])
def test_connection_changes_invalidate_review(connection, action):
    manager, bridge = connection
    pending = prepare(manager)
    manager.action({'action': action})
    settled(manager)
    with pytest.raises(ValueError):
        manager.action({'action': 'execute', 'pending_id': pending['id']})
    assert not [p for _, p in bridge.calls if p.get('name') == 'start_process']


def test_offline_just_before_dispatch_sends_no_command(connection):
    manager, bridge = connection
    pending = prepare(manager)
    bridge.online = False
    manager.action({'action': 'execute', 'pending_id': pending['id']})
    assert 'not sent' in settled(manager)['message']
    assert not [p for _, p in bridge.calls if p.get('name') == 'start_process']


def test_timeout_marks_unknown_without_replaying(connection):
    manager, bridge = connection
    pending = prepare(manager)
    bridge.error = TimeoutError()
    manager.action({'action': 'execute', 'pending_id': pending['id']})
    state = settled(manager)
    assert state['phase'] == 'error' and 'unconfirmed' in state['message']
    assert len([p for _, p in bridge.calls if p.get('name') == 'start_process']) == 1
    assert state['pending'] is None


def test_account_change_before_dispatch_sends_no_command(connection):
    manager, bridge = connection
    pending = prepare(manager)
    bridge.account = 'other-account'
    manager.action({'action': 'execute', 'pending_id': pending['id']})
    assert 'not sent' in settled(manager)['message']
    assert not [p for _, p in bridge.calls if p.get('name') == 'start_process']


def test_expired_review_cannot_execute(connection):
    manager, bridge = connection
    pending = prepare(manager)
    manager.clock = lambda: pending['expires_at']+1
    with pytest.raises(ValueError):
        manager.action({'action': 'execute', 'pending_id': pending['id']})
    assert not [p for _, p in bridge.calls if p.get('name') == 'start_process']


def test_background_call_only_polls_owned_structured_id(connection, monkeypatch):
    manager, bridge = connection
    bridge.responses = [
        {'structuredContent': {'status': 'running', 'call_id': 'owned-123', 'retry_original': False}},
        {'content': [{'type': 'text', 'text': 'completed background action'}]}]
    pending = prepare(manager)
    manager.action({'action': 'execute', 'pending_id': pending['id']})
    assert settled(manager)['result']['content'][0]['text'] == 'completed background action'
    actual = [p for _, p in bridge.calls if p.get('name') not in {None, 'list_devices', 'who_am_i'}]
    assert [p['name'] for p in actual] == ['start_process', 'get_tool_result']
    assert actual[1]['arguments'] == {'call_id': 'owned-123'}
    with pytest.raises(ValueError):
        manager.action({'action': 'prepare', 'tool': 'get_tool_result', 'arguments': {'call_id': 'unowned'}})


def test_json_in_file_text_does_not_trigger_background_poll(connection):
    manager, bridge = connection
    bridge.responses = [{'content': [{'type': 'text', 'text': '{"status":"running","call_id":"unowned"}'}]}]
    manager.action({'action': 'execute', 'pending_id': prepare(manager)['id']})
    settled(manager)
    assert not [p for _, p in bridge.calls if p.get('name') == 'get_tool_result']


@pytest.mark.parametrize('continuation', [
    {'status': 'running', 'call_id': 'different-call'},
    {'status': 'running', 'call_id': 'owned-call', 'device_id': 'different-device'}])
def test_background_continuation_cannot_change_identity(connection, continuation):
    manager, bridge = connection
    bridge.responses = [{'structuredContent': {'status': 'running', 'call_id': 'owned-call'}},
                        {'structuredContent': continuation}]
    manager.action({'action': 'execute', 'pending_id': prepare(manager)['id']})
    assert settled(manager)['phase'] == 'error'
    calls = [p for _, p in bridge.calls if p.get('name') == 'get_tool_result']
    assert calls == [{'name': 'get_tool_result', 'arguments': {'call_id': 'owned-call'}}]


def test_disconnect_during_pairing_does_not_restore_old_connection(tmp_path):
    release = threading.Event()
    entered = threading.Event()
    bridge = Bridge()
    def initialize(timeout):
        entered.set()
        assert release.wait(3)
    bridge.initialize = initialize
    manager = RemoteAccess(bridge_factory=lambda: bridge, audit_root=tmp_path)
    manager.action({'action': 'connect'})
    assert entered.wait(2)
    manager.close()
    release.set()
    for thread in manager.threads:
        thread.join(2)
    assert manager.state()['phase'] == 'disconnected'
    assert manager.state()['devices'] == [] and bridge.closed


def test_device_names_are_not_used_as_identity():
    text = ('Desktop Commander devices\n\n1. RickyGarcia\n   Status: Online\n   ID: first-id\n'
            '   Last seen: now\n\n2. RickyGarcia\n   Status: Offline\n   ID: second-id\n')
    rows = parse_devices({'content': [{'type': 'text', 'text': text}]})
    assert [(r['id'], r['status']) for r in rows] == [('first-id', 'online'), ('second-id', 'offline')]
    with pytest.raises(ValueError):
        parse_devices({'structuredContent': {'devices': [{'id': 'x'}, {'id': 'x'}]}})


@pytest.mark.parametrize('arguments', [{'command': 8}, {'command': ''}, {'command': 'x', 'extra': 1},
                                       {'command': 'x\x00y'}, {'deviceId': 'x'}])
def test_schema_rejects_bad_arguments(arguments):
    with pytest.raises(ValueError):
        validate_arguments(arguments, SCHEMA)


def test_runtime_verification_rejects_changes_and_unlisted_files(tmp_path):
    runtime = tmp_path/'runtime'
    (runtime/'node_modules/mcp-remote/dist').mkdir(parents=True)
    (runtime/'node.exe').write_bytes(b'fixture-node')
    (runtime/'node_modules/mcp-remote/dist/proxy.js').write_bytes(b'fixture-proxy')
    manifest = tmp_path/'RUNTIME-MANIFEST.json'
    entries = [{'path': p.relative_to(runtime).as_posix(), 'size_bytes': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in runtime.rglob('*') if p.is_file()]
    manifest.write_text(json.dumps(entries))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert verify_runtime(runtime, manifest, expected=digest)[0] == runtime/'node.exe'
    (runtime/'unexpected.js').write_bytes(b'extra')
    with pytest.raises(ValueError, match='unlisted'):
        verify_runtime(runtime, manifest, expected=digest)
    (runtime/'unexpected.js').unlink()
    (runtime/'node.exe').write_bytes(b'changed-node')
    with pytest.raises(ValueError):
        verify_runtime(runtime, manifest, expected=digest)


def test_invalid_action_fields(connection):
    manager, _ = connection
    with pytest.raises(ValueError):
        manager.action({'action': 'execute', 'pending_id': 'missing', 'arguments': {'command': 'replacement'}})


def test_bridge_uses_pinned_small_client_and_filters_inherited_environment(tmp_path, monkeypatch):
    local = tmp_path/'host'
    local.mkdir()
    resources = tmp_path/'resources'
    observed = {}
    monkeypatch.setattr(remote_access, 'host_root', lambda: local)
    monkeypatch.setattr(remote_access, 'resource_root', lambda: resources)
    monkeypatch.setenv('NODE_OPTIONS', '--require=unreviewed.js')
    monkeypatch.setenv('NODE_PATH', 'unreviewed-modules')
    monkeypatch.setenv('HTTP_PROXY', 'http://unreviewed.invalid')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-not-a-key')
    def verified(runtime, manifest, *, expected):
        observed.update(runtime=runtime, manifest=manifest, expected=expected)
        return runtime/'node.exe', runtime/'node_modules/mcp-remote/dist/proxy.js'
    def bridge(command, **kwargs):
        observed.update(command=command, **kwargs)
        return 'fixture-client'
    monkeypatch.setattr(remote_access, 'verify_runtime', verified)
    monkeypatch.setattr(remote_access, 'MCPBridge', bridge)
    assert remote_access.launch_bridge() == 'fixture-client'
    assert observed['runtime'] == resources/'RemoteAccess/runtime/client'
    assert observed['manifest'] == resources/'RemoteAccess/runtime/CLIENT-MANIFEST.json'
    assert observed['expected'] == remote_access.CLIENT_SHA256
    assert not {'NODE_OPTIONS', 'NODE_PATH', 'HTTP_PROXY', 'OPENAI_API_KEY'} & set(observed['env'])
    assert observed['env']['MCP_REMOTE_CONFIG_DIR'] == str(local/'ClientAuth')
    assert remote_access.ENDPOINT in observed['command']
