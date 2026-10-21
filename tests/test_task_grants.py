"""Task envelopes use the existing durable host authority, with owner duration."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import secrets
import threading

import pytest

from prometheus_assistant.integration_contracts import canonical_task, allows_task, canonical_host
from prometheus_assistant.scoped_grants import GrantStore
from prometheus_assistant.tool_registry import ToolSpec, ArgSpec


def contract(*, adapter='agentmemory', duration=None, **changes):
    value = {'task_id': secrets.token_hex(16), 'task': 'Complete the approved synthetic project task.',
             'adapter': adapter, 'duration_seconds': duration,
             'actions': ['remember', 'recall', 'forget'] if adapter == 'agentmemory' else ['read'],
             'resources': {'project': 'alpha'} if adapter == 'agentmemory' else {'hosts': ['example.com']}}
    return {**value, **changes}


def fixture(tmp_path):
    clock, binding = [1000.0, 50.0], ['host-a', 'revision-a']
    def make():
        return GrantStore(tmp_path / 'grants.sqlite3', host=lambda: binding[0],
                          policy_revision=lambda: binding[1], wall=lambda: clock[0], monotonic=lambda: clock[1])
    return make(), make, clock, binding


def invoke(store, grant, *, invocation=None, owner='owner-a', adapter='agentmemory', action='recall',
           arguments=None, execute=lambda action, args: {'action': action, 'arguments': args}):
    return store.run_task(grant['id'], invocation or secrets.token_hex(16), adapter, action,
                          arguments or {'project': 'alpha', 'query': 'synthetic'}, owner=owner, execute=execute)


def test_indefinite_task_is_durable_reusable_and_revocable(tmp_path):
    store, restart, clock, _ = fixture(tmp_path)
    grant = store.issue_task(contract(), owner='owner-a')
    assert grant['expires_at'] is None and grant['contract']['duration_seconds'] is None
    for _ in range(7):  # No legacy five-use cap applies.
        assert invoke(store, grant)['arguments']['limit'] == 10
    clock[0] += 1000000
    clock[1] += 1000000
    restarted = restart()
    assert invoke(restarted, grant)['action'] == 'recall'
    assert restarted.inspect(owner='owner-a') == []
    assert len(restarted.inspect_tasks(owner='owner-a')) == 1
    restarted.revoke_task(grant['id'], owner='owner-a')
    with pytest.raises(ValueError):
        invoke(store, grant)
    assert restart().inspect_tasks(owner='owner-a')[0]['state'] == 'revoked'


def test_long_explicit_duration_uses_exact_owner_choice(tmp_path):
    store, restart, clock, _ = fixture(tmp_path)
    grant = store.issue_task(contract(duration=30 * 86400), owner='owner-a')
    assert grant['expires_at'] == clock[0] + 30 * 86400
    clock[0] += 86400
    clock[1] += 86400
    assert invoke(restart(), grant)['action'] == 'recall'
    clock[0] = grant['expires_at']
    with pytest.raises(ValueError):
        invoke(restart(), grant)
    assert restart().inspect_tasks(owner='owner-a')[0]['state'] == 'expired'


@pytest.mark.parametrize('duration', [None, 86400])
def test_task_survives_reboot_but_legacy_grant_retires(tmp_path, duration):
    store, restart, clock, _ = fixture(tmp_path)
    task = store.issue_task(contract(duration=duration), owner='owner-a')
    spec = ToolSpec('project', 'tests', 'bounded tests', lambda r: 'legacy', {'target': ArgSpec(required=False)}, command_execution=True)
    review = store.review(spec, {'target': 'tests'}, owner='owner-a', seconds=60, uses=2)
    legacy = store.issue(review['review_id'], review['scope_sha256'], owner='owner-a')
    clock[0] += 10
    clock[1] = 1
    restarted = restart()
    assert restarted.inspect(owner='owner-a')[0]['state'] == 'clock_invalid'
    assert invoke(restarted, task)['action'] == 'recall'
    with pytest.raises(ValueError):
        restarted.run(legacy['id'], secrets.token_hex(16), spec, {'target': 'tests'}, owner='owner-a')


def test_indefinite_clock_reset_does_not_invent_expiration(tmp_path):
    store, restart, clock, _ = fixture(tmp_path)
    indefinite = store.issue_task(contract(), owner='owner-a')
    finite = store.issue_task(contract(duration=100), owner='owner-a')
    clock[:] = [900, 1]
    restarted = restart()
    assert invoke(restarted, indefinite)['action'] == 'recall'
    with pytest.raises(ValueError):
        invoke(restarted, finite)
    assert {row['id']: row['state'] for row in restarted.inspect_tasks(owner='owner-a')}[finite['id']] == 'clock_invalid'


def test_finite_monotonic_expiry_with_unchanged_wall(tmp_path):
    store, _, clock, _ = fixture(tmp_path)
    grant = store.issue_task(contract(duration=60), owner='owner-a')
    clock[1] += 61
    with pytest.raises(ValueError):
        invoke(store, grant)
    assert store.inspect_tasks(owner='owner-a')[0]['state'] == 'expired'


@pytest.mark.parametrize('change', ['owner', 'host', 'policy', 'deny', 'project', 'action', 'adapter'])
def test_task_scope_cannot_widen_or_override_bindings(tmp_path, monkeypatch, change):
    store, _, _, binding = fixture(tmp_path)
    grant = store.issue_task(contract(actions=['recall']), owner='owner-a')
    kwargs, calls = {}, []
    if change == 'owner': kwargs['owner'] = 'owner-b'
    if change == 'host': binding[0] = 'host-b'
    if change == 'policy': binding[1] = 'revision-b'
    if change == 'deny': monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', 'integrations.agentmemory')
    if change == 'project': kwargs['arguments'] = {'project': 'beta', 'query': 'synthetic'}
    if change == 'action': kwargs.update(action='remember', arguments={'project': 'alpha', 'content': 'Cannot write'})
    if change == 'adapter': kwargs.update(adapter='browser_use', action='read', arguments={'url': 'https://example.com/'})
    with pytest.raises(ValueError):
        invoke(store, grant, execute=lambda *args: calls.append(args), **kwargs)
    assert not calls
    if change == 'owner':
        assert store.inspect_tasks(owner='owner-b') == []
        assert store.inspect_tasks(owner='owner-a')[0]['state'] == 'active'


def test_browser_can_choose_paths_only_inside_reviewed_hosts(tmp_path):
    store, _, _, _ = fixture(tmp_path)
    grant = store.issue_task(contract(adapter='browser_use', resources={'hosts': ['EXAMPLE.com', 'docs.python.org']}), owner='owner-a')
    for url in ('https://example.com/', 'https://example.com/selected/resource?q=1', 'https://docs.python.org/3/library/'):
        assert invoke(store, grant, adapter='browser_use', action='read', arguments={'url': url})['arguments']['url'] == url
    for url in ('https://other.example.com/', 'https://not-approved.org/', 'https://127.0.0.1/', 'https://example.com@evil.org/'):
        with pytest.raises(ValueError):
            invoke(store, grant, adapter='browser_use', action='read', arguments={'url': url})


def test_explicit_public_web_task_allows_resource_selection_but_not_private_urls(tmp_path):
    store, _, _, _ = fixture(tmp_path)
    task = contract(adapter='browser_use', resources={'public_web': True})
    grant = store.issue_task(task, owner='owner-a')
    for url in ('https://example.com/selected', 'https://docs.python.org/3/', 'https://nodejs.org/en/'):
        assert invoke(store, grant, adapter='browser_use', action='read', arguments={'url': url})['arguments']['url'] == url
    for url in ('https://127.0.0.1/', 'https://localhost/', 'file:///private', 'https://host.internal/'):
        with pytest.raises(ValueError):
            invoke(store, grant, adapter='browser_use', action='read', arguments={'url': url})
    for resources in ({'public_web': 1}, {'public_web': False}, {'public_web': True, 'hosts': ['example.com']}):
        with pytest.raises(ValueError):
            canonical_task(contract(adapter='browser_use', resources=resources))


def test_replay_spent_before_failed_effect_and_across_restart(tmp_path):
    store, restart, _, _ = fixture(tmp_path)
    grant = store.issue_task(contract(), owner='owner-a')
    invocation, calls = secrets.token_hex(16), []
    def fail(*args):
        calls.append(args)
        raise RuntimeError('Synthetic failed provider')
    with pytest.raises(RuntimeError):
        invoke(store, grant, invocation=invocation, execute=fail)
    with pytest.raises(ValueError, match='already consumed'):
        invoke(restart(), grant, invocation=invocation, execute=fail)
    assert len(calls) == 1


def test_duplicate_task_issue_and_wrong_owner_revoke_rejected(tmp_path):
    store, _, _, _ = fixture(tmp_path)
    task = contract()
    grant = store.issue_task(task, owner='owner-a')
    with pytest.raises(ValueError, match='already issued'):
        store.issue_task(task, owner='owner-a')
    with pytest.raises(ValueError, match='Unknown owner'):
        store.revoke_task(grant['id'], owner='owner-b')
    assert invoke(store, grant)['action'] == 'recall'


@pytest.mark.parametrize('restore', ['database', 'witness'])
def test_task_authority_rollback_is_detected(tmp_path, restore):
    store, restart, _, _ = fixture(tmp_path)
    grant = store.issue_task(contract(), owner='owner-a')
    old_db, old_witness = store.path.read_bytes(), store.ledger.witness.read_bytes()
    invoke(store, grant)
    if restore == 'database': store.path.write_bytes(old_db)
    else: store.ledger.witness.write_bytes(old_witness)
    with pytest.raises(ValueError, match='continuity'):
        invoke(restart(), grant)


@pytest.mark.parametrize('malformation', ['scope', 'target', 'remaining', 'expires'])
def test_invalid_task_row_cannot_gain_reboot_exemption(tmp_path, malformation):
    store, restart, clock, _ = fixture(tmp_path)
    grant = store.issue_task(contract(), owner='owner-a')
    # Use a witnessed transaction to model malformed older producer output;
    # direct external DB tampering would instead fail continuity first.
    bad = {'scope': 'f' * 64, 'target': '{}', 'remaining': 3, 'expires': 2000}[malformation]
    with store._db() as db:
        db.execute('UPDATE grants SET ' + malformation + '=? WHERE id=?', (bad, grant['id']))
    clock[:] = [1010, 1]
    restarted = restart()
    with pytest.raises(ValueError):
        invoke(restarted, grant)
    with restarted._db() as db:
        assert db.execute('SELECT state FROM grants WHERE id=?', (grant['id'],)).fetchone()['state'] == 'clock_invalid'


def test_task_revoke_serializes_with_dispatched_effect(tmp_path):
    store, restart, _, _ = fixture(tmp_path)
    grant = store.issue_task(contract(), owner='owner-a')
    other = restart()
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    def effect(*args):
        entered.set()
        assert release.wait(3)
        return 'complete'
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(invoke, store, grant, execute=effect)
        assert entered.wait(3)
        def revoke():
            other.revoke_task(grant['id'], owner='owner-a')
            revoked.set()
        revoking = pool.submit(revoke)
        assert not revoked.wait(.05)
        release.set()
        assert running.result() == 'complete'
        revoking.result()
    with pytest.raises(ValueError):
        invoke(store, grant)


@pytest.mark.parametrize('transaction', [1, 2])
def test_task_expiry_while_waiting_for_authority_prevents_effect(tmp_path, monkeypatch, transaction):
    store, _, clock, _ = fixture(tmp_path)
    grant = store.issue_task(contract(duration=2), owner='owner-a')
    original, count, calls = store._db, [0], []
    @contextmanager
    def waited():
        count[0] += 1
        if count[0] == transaction:
            clock[0] += 3
            clock[1] += 3
        with original() as db:
            yield db
    monkeypatch.setattr(store, '_db', waited)
    with pytest.raises(ValueError):
        invoke(store, grant, execute=lambda *args: calls.append(args))
    assert calls == []


@pytest.mark.parametrize('change', [
    {'duration_seconds': 0}, {'duration_seconds': True}, {'duration_seconds': -1},
    {'duration_seconds': 1.5}, {'duration_seconds': 10 ** 100}, {'task_id': 'not-a-task'},
    {'actions': ['recall', 'recall']}, {'actions': ['export']},
    {'resources': {'project': '../owner'}}, {'resources': {'project': 'alpha', 'all_projects': True}},
    {'extra_approval': True}, {'task': 'x' * 1001},
])
def test_malformed_task_contracts_are_rejected(change):
    with pytest.raises(ValueError):
        canonical_task(contract(**change))


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '[::1]', '*.example.com', 'example.com:443',
                                'https://example.com/', 'x.local', 'example.com/path', 'user@example.com'])
def test_host_envelope_cannot_mean_unbounded_or_private_access(host):
    with pytest.raises(ValueError):
        canonical_task(contract(adapter='browser_use', resources={'hosts': [host]}))


def test_canonical_task_review_and_scope_normalization():
    assert canonical_host('BÜCHER.de') == 'xn--bcher-kva.de'
    task = canonical_task(contract(task='  Approved task  ', actions=['recall', 'remember']))
    assert task['task'] == 'Approved task'
    assert allows_task(task, 'agentmemory', 'remember', {'project': 'alpha', 'content': 'Selected note'})
    assert not allows_task(task, 'agentmemory', 'remember', {'project': 'beta', 'content': 'Other note'})
    assert not allows_task(task, 'agentmemory', 'forget', {'project': 'alpha', 'memory_ids': ['mem_x'], 'reason': 'remove'})


def test_semantic_service_grants_are_distinct_durable_and_revocable(tmp_path):
    store, restart, _, _ = fixture(tmp_path)
    classic = contract()
    service = {**contract(), 'adapter': 'agentmemory_service'}
    classic_grant = store.issue_task(classic, owner='owner-a')
    service_grant = store.issue_task(service, owner='owner-a')
    assert not allows_task(classic, 'agentmemory_service', 'recall', {'project': 'alpha', 'query': 'color'})
    assert not allows_task(service, 'agentmemory', 'recall', {'project': 'alpha', 'query': 'color'})
    active = {row['id']: row for row in restart().inspect_tasks(owner='owner-a')}
    assert active[classic_grant['id']]['contract']['adapter'] == 'agentmemory'
    assert active[service_grant['id']]['contract']['adapter'] == 'agentmemory_service'
    assert active[service_grant['id']]['expires_at'] is None
    store.revoke_task(service_grant['id'], owner='owner-a')
    states = {row['id']: row['state'] for row in restart().inspect_tasks(owner='owner-a')}
    assert states == {classic_grant['id']: 'active', service_grant['id']: 'revoked'}
