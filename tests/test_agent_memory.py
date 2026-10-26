"""Provider persistence, isolation and failure boundaries without owner data."""
import hashlib
import json
from pathlib import Path
import threading

import pytest

from prometheus_assistant import agent_memory as memory


@pytest.fixture
def rig(tmp_path, monkeypatch):
    resources, data = tmp_path / 'resources', tmp_path / 'data'
    bundle = resources / memory.BUNDLE
    bundle.mkdir(parents=True)
    node = bundle / 'node.exe'
    node.write_bytes(b'fake runtime for unit boundary')
    monkeypatch.setattr(memory, '_runtime', lambda root: (bundle, node))
    calls = []
    monkeypatch.setattr(memory, 'require_dispatch', calls.append)

    def run(action, **arguments):
        return memory.execute(action, arguments, data_root=data, resource_root=resources)

    def store(project='alpha'):
        return data / 'Integrations' / 'AgentMemory' / ('project-' + project) / 'store.json'

    return run, store, calls


def _saved(_bundle, _node, scratch, action, args, _cancel):
    path = scratch / 'store.json'
    value = json.loads(path.read_text())
    assert action == 'remember'
    notes = value.setdefault('mem:memories', {})
    identity = 'mem_synthetic_' + str(len(notes))
    notes[identity] = {'id': identity, 'content': args['content'], 'type': 'fact'}
    path.write_text(json.dumps(value))
    return {'saved': identity}


def test_transaction_save_and_project_isolation(rig, monkeypatch):
    run, store, calls = rig
    monkeypatch.setattr(memory, '_invoke', _saved)
    result = run('remember', project='alpha', content='Alpha note')
    run('remember', project='beta', content='Beta note')
    assert result['result']['saved'] == 'mem_synthetic_0'
    assert result['network_used'] is False
    assert json.loads(store().read_text())['mem:memories']['mem_synthetic_0']['content'] == 'Alpha note'
    assert json.loads(store('beta').read_text())['mem:memories']['mem_synthetic_0']['content'] == 'Beta note'
    assert not list(store().parent.glob('transaction-*'))
    assert set(calls) == {'integrations.agentmemory'}


def test_forget_changes_only_selected_ids(rig, monkeypatch):
    run, store, _ = rig
    monkeypatch.setattr(memory, '_invoke', _saved)
    run('remember', project='alpha', content='Keep this note')
    run('remember', project='alpha', content='Forget this note')

    def forget(_bundle, _node, scratch, _action, _args, _cancel):
        path = scratch / 'store.json'
        value = json.loads(path.read_text())
        del value['mem:memories']['mem_synthetic_1']
        path.write_text(json.dumps(value))
        return {'deleted': 1, 'requested': 1, 'reason': 'Test removal'}

    monkeypatch.setattr(memory, '_invoke', forget)
    run('forget', project='alpha', memory_ids=['mem_synthetic_1'], reason='Test removal')
    assert list(json.loads(store().read_text())['mem:memories']) == ['mem_synthetic_0']


@pytest.mark.parametrize('failure', ['silent_write_failure', 'wrong_note', 'changed_previous', 'cancel', 'process_error'])
def test_failed_write_preserves_original(rig, monkeypatch, failure):
    run, store, _ = rig
    monkeypatch.setattr(memory, '_invoke', _saved)
    run('remember', project='alpha', content='Preserve exactly')
    original = store().read_bytes()
    cancel = threading.Event()

    def broken(bundle, node, scratch, action, args, event):
        if failure == 'process_error':
            raise memory.AgentMemoryError('Synthetic stopped process')
        if failure == 'silent_write_failure':
            return {'saved': 'mem_synthetic_1'}
        result = _saved(bundle, node, scratch, action, args, event)
        path = scratch / 'store.json'
        value = json.loads(path.read_text())
        if failure == 'wrong_note':
            value['mem:memories'][result['saved']]['content'] = 'Unexpected replacement'
        elif failure == 'changed_previous':
            value['mem:memories']['mem_synthetic_0']['content'] = 'Unexpected previous update'
        elif failure == 'cancel':
            cancel.set()
        path.write_text(json.dumps(value))
        return result

    monkeypatch.setattr(memory, '_invoke', broken)
    with pytest.raises(memory.AgentMemoryError):
        memory.execute('remember', {'project': 'alpha', 'content': 'New note'},
                       data_root=store().parents[3], resource_root=store().parent, cancel_event=cancel)
    assert store().read_bytes() == original
    assert not list(store().parent.glob('transaction-*'))


@pytest.mark.parametrize('raw', [b'{bad', b'{"mem:memories":[],"extra":1}', b'{"mem:memories":{},"mem:memories":{}}',
                               b'{"mem:memories":{"mem_bad":{"id":"mem_bad","content":NaN}}}'])
def test_corrupt_store_is_preserved_without_launch(rig, monkeypatch, raw):
    run, store, _ = rig
    store().parent.mkdir(parents=True)
    store().write_bytes(raw)
    monkeypatch.setattr(memory, '_invoke', lambda *args: pytest.fail('Must not launch corrupt store'))
    with pytest.raises(memory.AgentMemoryError, match='preserved'):
        run('remember', project='alpha', content='New note')
    assert store().read_bytes() == raw


def test_recall_cannot_commit_or_return_cross_project_note(rig, monkeypatch):
    run, store, _ = rig
    monkeypatch.setattr(memory, '_invoke', _saved)
    run('remember', project='alpha', content='Original alpha')
    original = store().read_bytes()
    monkeypatch.setattr(memory, '_invoke', lambda *args: {'results': [{'id': 'mem_beta', 'content': 'Other project'}]})
    with pytest.raises(memory.AgentMemoryError, match='expected result'):
        run('recall', project='alpha', query='Other')
    assert store().read_bytes() == original


def test_external_change_not_overwritten(rig, monkeypatch):
    run, store, _ = rig
    monkeypatch.setattr(memory, '_invoke', _saved)
    run('remember', project='alpha', content='Original')
    replacement = b'{}'

    def changed(*args):
        result = _saved(*args)
        store().write_bytes(replacement)
        return result

    monkeypatch.setattr(memory, '_invoke', changed)
    with pytest.raises(memory.AgentMemoryError, match='changed during'):
        run('remember', project='alpha', content='New note')
    assert store().read_bytes() == replacement


def test_busy_store_lock_rejects_second_operation(rig, monkeypatch):
    run, store, _ = rig
    store().parent.mkdir(parents=True)
    monkeypatch.setattr(memory, '_invoke', lambda *args: pytest.fail('Locked project cannot launch'))
    with memory._lock(store().parent):
        with pytest.raises(memory.AgentMemoryError, match='Another memory operation'):
            run('recall', project='alpha', query='hello')
    with memory._lock(store().parent):
        pass  # The OS lock releases without a stale-lock cleanup policy.


@pytest.mark.parametrize('action,args', [
    ('remember', {'project': '../other', 'content': 'x'}),
    ('remember', {'project': 'ALPHA', 'content': 'x'}),
    ('remember', {'project': 'alpha', 'content': 'x' * 8001}),
    ('remember', {'project': 'alpha', 'content': 'x', 'path': 'other'}),
    ('recall', {'project': 'alpha', 'query': 'x', 'limit': True}),
    ('recall', {'project': 'alpha', 'query': 'x', 'limit': 21}),
    ('forget', {'project': 'alpha', 'memory_ids': ['../../notes'], 'reason': 'test'}),
    ('forget', {'project': 'alpha', 'memory_ids': ['mem_x', 'mem_x'], 'reason': 'test'}),
    ('export', {'project': 'alpha'}),
])
def test_invalid_scope_is_rejected_before_dispatch(rig, monkeypatch, action, args):
    run, _, calls = rig
    monkeypatch.setattr(memory, '_invoke', lambda *args: pytest.fail('Invalid scope cannot launch'))
    with pytest.raises(memory.AgentMemoryError):
        run(action, **args)
    assert calls == []


def test_policy_denial_and_early_cancel_create_no_data(tmp_path, monkeypatch):
    def deny(_):
        raise ValueError('Host denied')
    monkeypatch.setattr(memory, 'require_dispatch', deny)
    with pytest.raises(ValueError, match='Host denied'):
        memory.execute('recall', {'project': 'alpha', 'query': 'x'}, data_root=tmp_path / 'data', resource_root=tmp_path)
    monkeypatch.setattr(memory, 'require_dispatch', lambda _: None)
    event = threading.Event()
    event.set()
    with pytest.raises(memory.AgentMemoryError, match='cancelled'):
        memory.execute('recall', {'project': 'alpha', 'query': 'x'}, data_root=tmp_path / 'data',
                       resource_root=tmp_path, cancel_event=event)
    assert not (tmp_path / 'data').exists()


def test_runtime_pin_failure_and_missing_are_honest(tmp_path, monkeypatch):
    # This fixture exercises the portable Windows Node pin specifically.
    monkeypatch.setattr(memory.sys, 'platform', 'win32')
    assert memory.status(tmp_path)['installed'] is False
    bundle = tmp_path / memory.BUNDLE
    (bundle / 'dist').mkdir(parents=True)
    (bundle / 'dist/standalone.mjs').write_text('untrusted')
    assert memory.status(tmp_path)['integrity'] == 'failed'
    assert memory.status(tmp_path)['available'] is False
    data = {'dist/standalone.mjs': b'fixture', 'LICENSE': b'license'}
    for name, raw in data.items():
        (bundle / name).write_bytes(raw)
    node = tmp_path / memory.NODE_RELATIVE
    node.parent.mkdir(parents=True)
    node.write_bytes(b'node')
    monkeypatch.setattr(memory, 'PINS', {k: hashlib.sha256(v).hexdigest() for k, v in data.items()})
    monkeypatch.setattr(memory, 'NODE_PINS', {hashlib.sha256(b'node').hexdigest()})
    assert memory.status(tmp_path)['available'] is True
    node.write_bytes(b'replaced')
    assert memory.status(tmp_path)['integrity'] == 'failed'


def test_store_size_bound_preserves_file(rig, monkeypatch):
    run, store, _ = rig
    store().parent.mkdir(parents=True)
    store().write_bytes(b'x' * 65)
    monkeypatch.setattr(memory, 'MAX_STORE_BYTES', 64)
    with pytest.raises(memory.AgentMemoryError, match='limit'):
        run('recall', project='alpha', query='x')
    assert store().stat().st_size == 65


def test_transport_sanitizes_environment_and_closes(rig, tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMEMORY_FORCE_PROXY', '1')
    monkeypatch.setenv('AGENTMEMORY_API_KEY', 'must-not-leak')
    monkeypatch.setenv('NODE_OPTIONS', '--require=owner-hook')
    monkeypatch.setenv('HOME', 'owner-profile')
    closed = []

    class Bridge:
        def __init__(self, command, *, env, cwd, timeout):
            assert '--permission' in command and '--max-old-space-size=256' in command
            assert not any(value.startswith('--allow-child-process') for value in command)
            assert 'AGENTMEMORY_FORCE_PROXY' not in env and 'AGENTMEMORY_API_KEY' not in env
            assert 'NODE_OPTIONS' not in env
            assert env['HOME'] == str(tmp_path)
            assert Path(env['STANDALONE_PERSIST_PATH']) == tmp_path / 'store.json'
            assert 'network disabled' in (tmp_path / 'local-only.mjs').read_text()
            assert command[command.index('--import') + 1].startswith('file:')

        def initialize(self, timeout):
            return {'serverInfo': {'name': 'agentmemory', 'version': memory.VERSION}}

        def request(self, method, params, timeout):
            assert params == {'name': 'memory_recall', 'arguments': {'query': 'x', 'limit': 2}}
            return {'content': [{'type': 'text', 'text': '{"results":[]}'}]}

        def close(self):
            closed.append(True)

    monkeypatch.setattr(memory, 'MCPBridge', Bridge)
    result = memory._invoke(tmp_path, tmp_path / 'node.exe', tmp_path, 'recall',
                            {'query': 'x', 'limit': 2}, None)
    assert result == {'results': []} and closed


@pytest.mark.parametrize('response', [
    {'isError': True, 'content': [{'type': 'text', 'text': 'secret failure'}]},
    {'content': [{'type': 'text', 'text': 'not json'}]},
    {'content': [{'type': 'text', 'text': 'x' * 33}]},
    {'content': [{'type': 'text', 'text': '{"results":[],"results":[]}'}]},
])
def test_bad_or_excessive_mcp_result_closes_process(rig, tmp_path, monkeypatch, response):
    closed = []

    class Bridge:
        def __init__(self, *args, **kwargs):
            pass
        def initialize(self, **kwargs):
            return {'serverInfo': {'name': 'agentmemory', 'version': memory.VERSION}}
        def request(self, *args, **kwargs):
            return response
        def close(self):
            closed.append(True)

    monkeypatch.setattr(memory, 'MCPBridge', Bridge)
    monkeypatch.setattr(memory, 'MAX_RESULT_BYTES', 32)
    with pytest.raises(memory.AgentMemoryError) as caught:
        memory._invoke(tmp_path, tmp_path / 'node.exe', tmp_path, 'recall', {'query': 'x', 'limit': 2}, None)
    assert 'secret failure' not in str(caught.value) and closed


def test_cancellation_during_transport_closes_owned_process(rig, tmp_path, monkeypatch):
    entered, closed, cancel = threading.Event(), threading.Event(), threading.Event()

    class Bridge:
        def __init__(self, *args, **kwargs):
            pass
        def initialize(self, **kwargs):
            return {'serverInfo': {'name': 'agentmemory', 'version': memory.VERSION}}
        def request(self, *args, **kwargs):
            entered.set()
            assert closed.wait(2)
            raise memory.MCPBridgeError('Owned bridge stopped')
        def close(self):
            closed.set()

    monkeypatch.setattr(memory, 'MCPBridge', Bridge)
    def cancel_after_start():
        assert entered.wait(2)
        cancel.set()
    worker = threading.Thread(target=cancel_after_start)
    worker.start()
    try:
        with pytest.raises(memory.AgentMemoryError, match='cancelled'):
            memory._invoke(tmp_path, tmp_path / 'node.exe', tmp_path, 'recall', {'query': 'x', 'limit': 2}, cancel)
    finally:
        cancel.set()
        worker.join(timeout=3)
    assert closed.is_set() and not worker.is_alive()
