"""Connection dashboard reads must be bounded, passive and time-qualified."""
import copy
import json
import threading

import pytest

from prometheus_assistant.remote_access import RemoteAccess
from test_remote_access import Bridge, ID, prepare, settled


class InventoryBridge(Bridge):
    def __init__(self):
        super().__init__()
        self.inventory = [
            {'id': ID, 'name': 'private-computer-name', 'online': True},
            {'id': 'offline-device', 'name': 'private-offline-name', 'online': False},
        ]
        self.inventory_error = False
        self.inventory_gate = None

    def request(self, method, params, timeout=None):
        if method == 'tools/call' and params.get('name') == 'list_devices':
            self.calls.append((method, copy.deepcopy(params)))
            if self.inventory_gate:
                entered, release = self.inventory_gate
                self.inventory_gate = None
                entered.set()
                assert release.wait(4)
            if self.inventory_error:
                raise OSError('private provider failure')
            return {'structuredContent': {'devices': copy.deepcopy(self.inventory)}}
        return super().request(method, params, timeout)


@pytest.fixture
def connected(tmp_path):
    wall, mono = [1_800_000_000.0], [100.0]
    bridge = InventoryBridge()
    manager = RemoteAccess(bridge_factory=lambda: bridge, audit_root=tmp_path,
                           clock=lambda: wall[0], monotonic=lambda: mono[0])
    manager.action({'action': 'connect'})
    assert settled(manager)['phase'] == 'ready'
    manager.action({'action': 'select', 'device_id': ID})
    yield manager, bridge, wall, mono
    manager.close()


def test_initial_snapshot_is_unavailable_and_has_only_public_summary_fields(tmp_path):
    def no_bridge():
        raise AssertionError('Reading health must never start a connection')

    manager = RemoteAccess(bridge_factory=no_bridge, audit_root=tmp_path)
    assert manager.health_snapshot() == {
        'schema_version': 1, 'phase': 'disconnected', 'generation': 0,
        'freshness': 'unavailable', 'observed_at': None, 'expires_at': None,
        'ttl_seconds': 30, 'counts': {'online': 0, 'offline': 0, 'unknown': 0, 'total': 0},
        'selected_state': 'unselected',
    }
    assert list(tmp_path.iterdir()) == []


def test_successful_connection_records_bounded_fresh_counts_and_selection(connected):
    manager, bridge, wall, mono = connected
    snapshot = manager.health_snapshot()
    assert snapshot['phase'] == 'ready'
    assert snapshot['freshness'] == 'current'
    assert snapshot['observed_at'] == wall[0]
    assert snapshot['expires_at'] == wall[0] + 30
    assert snapshot['counts'] == {'online': 1, 'offline': 1, 'unknown': 0, 'total': 2}
    assert snapshot['selected_state'] == 'online'
    public = json.dumps(snapshot)
    for secret in (ID, 'private-computer-name', bridge.account, 'start_process', 'private-offline-name'):
        assert secret not in public
    snapshot['counts']['online'] = 999
    assert manager.health_snapshot()['counts']['online'] == 1


@pytest.mark.parametrize('wall_delta,mono_delta', [(30, 0), (0, 30), (-1, 0), (0, -1), (31, 31)])
def test_expired_or_rolled_back_clock_never_reports_current_devices(connected, wall_delta, mono_delta):
    manager, bridge, wall, mono = connected
    before = manager.health_snapshot()
    wall[0] += wall_delta
    mono[0] += mono_delta
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'stale'
    assert snapshot['counts'] == {'online': 0, 'offline': 0, 'unknown': 2, 'total': 2}
    assert snapshot['selected_state'] == 'unknown'
    assert snapshot['observed_at'] == before['observed_at']


def test_snapshot_does_not_refresh_expire_review_audit_or_change_state(connected, monkeypatch):
    manager, bridge, wall, mono = connected
    pending = prepare(manager)
    before_pending = copy.deepcopy(manager.pending)
    before_calls = copy.deepcopy(bridge.calls)
    audit_before = (manager.audit_root / 'remote-actions.jsonl').read_bytes()
    wall[0] = pending['expires_at'] + 1
    mono[0] += 301
    monkeypatch.setattr(manager, 'state', lambda: pytest.fail('health must not use mutating state()'))
    for _ in range(3):
        assert manager.health_snapshot()['freshness'] == 'stale'
    assert manager.pending == before_pending
    assert bridge.calls == before_calls
    assert (manager.audit_root / 'remote-actions.jsonl').read_bytes() == audit_before


def test_refresh_replaces_observation_but_preserves_existing_deselection_rules(connected):
    manager, bridge, wall, mono = connected
    wall[0] += 50
    mono[0] += 50
    bridge.inventory[0]['online'] = False
    manager.action({'action': 'refresh'})
    assert settled(manager)['phase'] == 'ready'
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'current'
    assert snapshot['observed_at'] == wall[0]
    assert snapshot['counts'] == {'online': 0, 'offline': 2, 'unknown': 0, 'total': 2}
    assert snapshot['selected_state'] == 'unselected'


def test_refresh_failure_invalidates_fresh_positive_counts(connected):
    manager, bridge, wall, mono = connected
    bridge.inventory_error = True
    manager.action({'action': 'refresh'})
    assert settled(manager)['phase'] == 'error'
    snapshot = manager.health_snapshot()
    assert snapshot['phase'] == 'error'
    assert snapshot['freshness'] == 'stale'
    assert snapshot['counts']['online'] == snapshot['counts']['offline'] == 0
    assert snapshot['counts']['unknown'] == 2
    assert snapshot['selected_state'] == 'unknown'
    assert 'private provider failure' not in json.dumps(snapshot)


def test_refresh_in_progress_does_not_claim_old_observation_is_current(connected):
    manager, bridge, wall, mono = connected
    entered, release = threading.Event(), threading.Event()
    bridge.inventory_gate = entered, release
    try:
        manager.action({'action': 'refresh'})
        assert entered.wait(2)
        snapshot = manager.health_snapshot()
        assert snapshot['phase'] == 'working'
        assert snapshot['freshness'] == 'stale'
        assert snapshot['counts']['online'] == 0
    finally:
        release.set()
    assert settled(manager)['phase'] == 'ready'
    assert manager.health_snapshot()['freshness'] == 'current'


def test_disconnect_clears_cache_and_late_reply_cannot_replace_new_generation(connected):
    manager, old_bridge, wall, mono = connected
    entered, release = threading.Event(), threading.Event()
    old_bridge.inventory_gate = entered, release
    manager.action({'action': 'refresh'})
    assert entered.wait(2)
    old_thread = manager.threads[-1]
    try:
        previous_generation = manager.health_snapshot()['generation']
        manager.close()
        snapshot = manager.health_snapshot()
        assert snapshot['freshness'] == 'unavailable'
        assert snapshot['observed_at'] is None
        assert snapshot['counts']['total'] == 0
        assert snapshot['generation'] > previous_generation
        new_bridge = InventoryBridge()
        new_bridge.inventory = []
        manager.factory = lambda: new_bridge
        wall[0] += 5
        mono[0] += 5
        manager.action({'action': 'connect'})
        assert settled(manager)['phase'] == 'ready'
        expected = manager.health_snapshot()
        assert expected['counts']['total'] == 0
    finally:
        release.set()
        old_thread.join(2)
    assert not old_thread.is_alive()
    assert manager.health_snapshot() == expected


def test_successful_predispatch_check_supplies_new_observation(connected):
    manager, bridge, wall, mono = connected
    pending = prepare(manager)
    wall[0] += 40
    mono[0] += 40
    manager.action({'action': 'execute', 'pending_id': pending['id']})
    assert settled(manager)['phase'] == 'ready'
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'current'
    assert snapshot['observed_at'] == wall[0]
    assert snapshot['selected_state'] == 'online'


def test_dispatch_failure_cannot_leave_fresh_positive_health(connected):
    manager, bridge, wall, mono = connected
    bridge.error = TimeoutError('private-error')
    manager.action({'action': 'execute', 'pending_id': prepare(manager)['id']})
    assert settled(manager)['phase'] == 'error'
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'stale'
    assert snapshot['counts']['online'] == 0
    assert snapshot['selected_state'] == 'unknown'


def test_device_count_never_exceeds_provider_limit(connected):
    manager, bridge, wall, mono = connected
    bridge.inventory = [{'id': f'device-{n}', 'name': f'private-name-{n}', 'online': True} for n in range(120)]
    manager.action({'action': 'refresh'})
    assert settled(manager)['phase'] == 'ready'
    assert manager.health_snapshot()['counts'] == {'online': 100, 'offline': 0, 'unknown': 0, 'total': 100}


@pytest.mark.parametrize('clock_name,value', [
    ('wall', float('inf')), ('wall', float('nan')), ('wall', 'invalid'),
    ('mono', float('inf')), ('mono', float('nan')), ('mono', 'invalid'),
])
def test_invalid_current_clock_never_produces_freshness_or_nonfinite_json(connected, clock_name, value):
    manager, bridge, wall, mono = connected
    (wall if clock_name == 'wall' else mono)[0] = value
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'stale'
    assert snapshot['counts']['unknown'] == 2
    json.dumps(snapshot, allow_nan=False)


def test_failed_reconnect_does_not_reuse_previous_account_observation(connected):
    manager, bridge, wall, mono = connected
    def failed_factory():
        raise OSError('private authentication details')
    manager.factory = failed_factory
    manager.action({'action': 'connect'})
    assert settled(manager)['phase'] == 'error'
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'unavailable'
    assert snapshot['counts']['total'] == 0
    assert snapshot['observed_at'] is snapshot['expires_at'] is None
    assert snapshot['selected_state'] == 'unselected'
    assert 'private' not in json.dumps(snapshot)


def test_late_connection_response_cannot_restore_disconnected_health(tmp_path):
    bridge = InventoryBridge()
    entered, release = threading.Event(), threading.Event()
    def blocked_initialize(timeout):
        entered.set()
        assert release.wait(4)
    bridge.initialize = blocked_initialize
    manager = RemoteAccess(bridge_factory=lambda: bridge, audit_root=tmp_path)
    manager.action({'action': 'connect'})
    assert entered.wait(2)
    assert manager.health_snapshot()['freshness'] == 'unavailable'
    assert manager.health_snapshot()['phase'] == 'connecting'
    manager.close()
    expected = manager.health_snapshot()
    release.set()
    for thread in manager.threads:
        thread.join(2)
        assert not thread.is_alive()
    assert manager.health_snapshot() == expected
    assert bridge.closed


def test_slow_account_verification_does_not_make_old_device_list_look_new(connected):
    manager, bridge, wall, mono = connected
    original = bridge.request
    def delayed_identity(method, params, timeout=None):
        result = original(method, params, timeout)
        if method == 'tools/call' and params.get('name') == 'who_am_i':
            wall[0] += 31
            mono[0] += 31
        return result
    bridge.request = delayed_identity
    observed_at = wall[0]
    manager.action({'action': 'execute', 'pending_id': prepare(manager)['id']})
    assert settled(manager)['phase'] == 'ready'
    snapshot = manager.health_snapshot()
    assert snapshot['observed_at'] == observed_at
    assert snapshot['freshness'] == 'stale'
    assert snapshot['counts']['online'] == 0


@pytest.mark.parametrize('status_fields,health_status,legacy_status', [
    ({}, 'unknown', 'offline'),
    ({'online': True}, 'online', 'online'),
    ({'online': False}, 'offline', 'offline'),
    ({'online': 'false'}, 'unknown', 'offline'),
    ({'online': 1}, 'unknown', 'offline'),
    ({'online': None}, 'unknown', 'offline'),
    ({'status': 'Online'}, 'online', 'online'),
    ({'status': 'OFFLINE'}, 'offline', 'offline'),
    ({'status': 'unknown'}, 'unknown', 'offline'),
    ({'status': None}, 'unknown', 'offline'),
    ({'online': True, 'status': 'offline'}, 'unknown', 'online'),
    ({'online': False, 'status': 'online'}, 'unknown', 'online'),
    ({'online': True, 'status': 'online'}, 'online', 'online'),
    ({'online': False, 'status': 'offline'}, 'offline', 'offline'),
    ({'online': False, 'status': 'pending'}, 'unknown', 'offline'),
])
def test_health_requires_explicit_consistent_provider_status_without_changing_dispatch_parser(
        connected, status_fields, health_status, legacy_status):
    manager, bridge, wall, mono = connected
    bridge.inventory = [{'id': ID, 'name': 'secret-name', **status_fields}]
    before_calls = len(bridge.calls)
    manager.action({'action': 'refresh'})
    assert settled(manager)['phase'] == 'ready'
    assert bridge.calls[before_calls:] == [('tools/call', {'name': 'list_devices', 'arguments': {}})]
    assert manager.state()['devices'][0]['status'] == legacy_status
    snapshot = manager.health_snapshot()
    assert snapshot['freshness'] == 'current'
    assert snapshot['counts']['total'] == 1
    assert snapshot['counts'][health_status] == 1
    assert sum(snapshot['counts'][status] for status in ('online', 'offline', 'unknown')) == 1
    assert 'secret-name' not in json.dumps(snapshot)
    if legacy_status == 'online':
        assert snapshot['selected_state'] == health_status
    else:
        assert snapshot['selected_state'] == 'unselected'


def test_text_provider_records_use_only_explicit_status_and_id(connected):
    manager, bridge, wall, mono = connected
    original = bridge.request
    def text_inventory(method, params, timeout=None):
        if method == 'tools/call' and params.get('name') == 'list_devices':
            bridge.calls.append((method, copy.deepcopy(params)))
            return {'content': [{'type': 'text', 'text': (
                f'1. secret-online-name\nStatus: Online\nID: {ID}\n'
                '2. secret-offline-name\nStatus: Offline\nID: offline-device\n'
                '3. not-an-accepted-target\nStatus: Maybe\nID: unknown-device\n')}]}
        return original(method, params, timeout)
    bridge.request = text_inventory
    manager.action({'action': 'refresh'})
    assert settled(manager)['phase'] == 'ready'
    snapshot = manager.health_snapshot()
    assert snapshot['counts'] == {'online': 1, 'offline': 1, 'unknown': 0, 'total': 2}
    assert snapshot['selected_state'] == 'online'
    assert 'secret-' not in json.dumps(snapshot)
