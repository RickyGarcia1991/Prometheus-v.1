import threading

import pytest

from prometheus_assistant.model_lifecycle import (
    LifecycleBlocked, LifecycleBusy, LifecycleCancelled, LifecycleCooldown,
    ModelLifecycleGovernor, optional_scope, task_scope,
)


KEY = ('http://localhost:11434', 'model', 2048)


def test_concurrent_lease_fails_fast_and_releases_after_exception():
    governor = ModelLifecycleGovernor()
    held = threading.Event()
    release = threading.Event()
    def worker():
        with governor.lease(*KEY):
            held.set()
            assert release.wait(3)
    thread = threading.Thread(target=worker)
    thread.start()
    assert held.wait(3)
    try:
        with pytest.raises(LifecycleBusy):
            with governor.lease('other', 'other', 4096):
                pytest.fail('duplicate engine admitted')
        with pytest.raises(LifecycleBusy):
            with governor.lease(*KEY, maintenance=True):
                pytest.fail('active inference unloaded')
    finally:
        release.set()
        thread.join(3)
    with pytest.raises(RuntimeError):
        with governor.lease(*KEY):
            raise RuntimeError('backend failed')
    with governor.lease('other', 'other', 4096):
        pass


def test_cooldown_is_monotonic_exact_and_bounded():
    clock = [100.0]
    governor = ModelLifecycleGovernor(clock=lambda: clock[0], cooldown_seconds=10, max_failures=2)
    def fail(key):
        with pytest.raises(OSError):
            with governor.lease(*key):
                raise OSError('backend failed')
    fail(KEY)
    with pytest.raises(LifecycleCooldown):
        with governor.lease(*KEY):
            pass
    for key in [('other', KEY[1], KEY[2]), (KEY[0], 'other', KEY[2]), (KEY[0], KEY[1], 4096)]:
        with governor.lease(*key):
            pass
    clock[0] += 10
    with governor.lease(*KEY):
        pass
    for number in range(3):
        fail((str(number), 'model', 2048))
    assert len(governor._failures) == 2
    with governor.lease('0', 'model', 2048):
        pass


def test_scope_restores_and_optional_inherits_fresh_provider():
    governor = ModelLifecycleGovernor()
    mode = ['Balanced']
    with task_scope(mode_provider=lambda: mode[0]):
        with optional_scope():
            with governor.lease(*KEY) as lease:
                assert lease.keep_alive('15m') == 0
                mode[0] = 'Idle'
                with pytest.raises(LifecycleBlocked):
                    lease.check()
            with task_scope(optional=False):
                with pytest.raises(LifecycleBlocked):
                    with governor.lease(*KEY):
                        pass
        with governor.lease(*KEY) as lease:
            assert lease.keep_alive() == 0
    with governor.lease(*KEY) as lease:
        assert lease.keep_alive('15m') == '15m'
        assert lease.keep_alive('24h') == 900
        assert lease.keep_alive(-1) == 0


@pytest.mark.parametrize('mode', ['unknown', 'Critical Recovery', None])
def test_unknown_and_critical_modes_block_foreground(mode):
    with task_scope(mode_provider=lambda: mode):
        with pytest.raises(LifecycleBlocked):
            with ModelLifecycleGovernor().lease(*KEY):
                pytest.fail('restricted inference admitted')


def test_provider_exception_fails_closed_and_scope_resets():
    def broken():
        raise OSError('telemetry unavailable')
    with pytest.raises(LifecycleBlocked):
        with task_scope(mode_provider=broken):
            with ModelLifecycleGovernor().lease(*KEY):
                pass
    with ModelLifecycleGovernor().lease(*KEY):
        pass


def test_cancellation_before_and_during_operation_does_not_cooldown():
    governor = ModelLifecycleGovernor()
    cancel = threading.Event()
    cancel.set()
    with task_scope(cancel_event=cancel):
        with pytest.raises(LifecycleCancelled):
            with governor.lease(*KEY):
                pytest.fail('cancelled inference admitted')
    cancel.clear()
    with pytest.raises(LifecycleCancelled):
        with task_scope(cancel_event=cancel):
            with optional_scope():
                with governor.lease(*KEY) as lease:
                    cancel.set()
                    lease.check()
    with governor.lease(*KEY):
        pass


def test_maintenance_bypasses_mode_and_cooldown_without_recording_failure():
    governor = ModelLifecycleGovernor()
    with pytest.raises(OSError):
        with governor.lease(*KEY):
            raise OSError('failed')
    with task_scope(mode_provider=lambda: 'Critical Recovery'):
        with governor.lease(*KEY, maintenance=True):
            pass
        with pytest.raises(OSError):
            with governor.lease('other', 'model', 2048, maintenance=True):
                raise OSError('unload failed')
    with governor.lease('other', 'model', 2048):
        pass


def test_base_exception_releases_lease():
    governor = ModelLifecycleGovernor()
    with pytest.raises(KeyboardInterrupt):
        with governor.lease(*KEY):
            raise KeyboardInterrupt()
    with governor.lease(*KEY):
        pass

@pytest.mark.parametrize('mode', ['Idle', 'Battery Saver'])
def test_restrictive_modes_only_block_optional_inference(mode):
    governor = ModelLifecycleGovernor()
    with task_scope(mode_provider=lambda: mode):
        with governor.lease(*KEY) as lease:
            assert lease.keep_alive('15m') == 0
        with optional_scope():
            with pytest.raises(LifecycleBlocked):
                with governor.lease(*KEY):
                    pass


def test_scope_does_not_leak_between_worker_threads():
    governor = ModelLifecycleGovernor()
    results = []
    def worker():
        with governor.lease(*KEY) as lease:
            results.append(lease.keep_alive())
    with task_scope(mode_provider=lambda: 'Critical Recovery', optional=True):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
    assert results == [120]


def test_mode_change_during_foreground_operation_releases_without_cooldown():
    governor = ModelLifecycleGovernor()
    mode = ['Performance']
    with task_scope(mode_provider=lambda: mode[0]):
        with pytest.raises(LifecycleBlocked):
            with governor.lease(*KEY) as lease:
                mode[0] = 'Critical Recovery'
                lease.check()
        mode[0] = 'Balanced'
        with governor.lease(*KEY):
            pass
