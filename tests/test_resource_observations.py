from concurrent.futures import ThreadPoolExecutor

import pytest

from prometheus_assistant.resource_observations import ResourceProbe


def test_cache_cpu_delta_and_isolated_return(tmp_path):
    now = [0]
    calls = []
    def sample(path):
        calls.append(path)
        return {'cpu_times': (20, 100) if len(calls) == 1 else (30, 150),
                'available_ram_gib': 3, 'free_disk_gib': 10}
    probe = ResourceProbe(tmp_path, clock=lambda: now[0], sample=sample)
    first = probe.snapshot()
    assert first['cpu_percent'] is None
    first['sources']['cpu_percent'] = 'forged'
    now[0] = 9.99
    assert probe.snapshot()['sources']['cpu_percent'] != 'forged'
    assert len(calls) == 1
    now[0] = 10
    assert probe.snapshot()['cpu_percent'] == 80
    assert len(calls) == 2


def test_reset_or_wrapped_counters_are_unknown_then_rebaseline(tmp_path):
    now = [0]
    samples = iter([(100, 1000), (3, 20), (4, 30)])
    probe = ResourceProbe(tmp_path, clock=lambda: now[0],
                          sample=lambda _: {'cpu_times': next(samples)})
    assert probe.snapshot()['cpu_percent'] is None
    now[0] = 10
    assert probe.snapshot()['cpu_percent'] is None
    now[0] = 20
    assert probe.snapshot()['cpu_percent'] == 90


def test_unknowns_and_failures_remain_unknown_and_cached(tmp_path):
    calls = []
    def sample(_):
        calls.append(1)
        raise OSError('unavailable')
    probe = ResourceProbe(tmp_path, sample=sample)
    result = probe.snapshot()
    for field in ('available_ram_gib', 'free_disk_gib', 'cpu_percent',
                  'battery_percent', 'plugged_in', 'vram_gib', 'thermal_distress',
                  'disk_io_mib', 'battery_drain_percent', 'low_memory'):
        assert result[field] is None
    probe.snapshot()
    assert len(calls) == 1


def test_concurrent_snapshots_sample_once(tmp_path):
    calls = []
    def sample(_):
        calls.append(1)
        return {}
    probe = ResourceProbe(tmp_path, sample=sample)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: probe.snapshot(), range(32)))
    assert len(calls) == 1
    assert all(result == results[0] for result in results)


def test_execution_refresh_bypasses_cached_headroom(tmp_path):
    samples = iter([{'available_ram_gib': 8}, {'available_ram_gib': .2}])
    probe = ResourceProbe(tmp_path, clock=lambda: 1, sample=lambda _: next(samples))
    assert probe.snapshot()['available_ram_gib'] == 8
    assert probe.snapshot()['available_ram_gib'] == 8
    assert probe.snapshot(force=True)['available_ram_gib'] == .2


def test_invalid_numbers_do_not_create_measurements(tmp_path):
    probe = ResourceProbe(tmp_path, sample=lambda _: dict(
        available_ram_gib=float('nan'), free_disk_gib=-1,
        battery_percent=255, plugged_in=255, total_ram_gib=0,
        cpu_times=(100, 10)))
    result = probe.snapshot()
    assert all(result[k] is None for k in ('available_ram_gib', 'free_disk_gib',
        'battery_percent', 'plugged_in', 'total_ram_gib', 'cpu_percent'))


def test_missing_cpu_sample_breaks_interval(tmp_path):
    now = [0]
    samples = iter([(20, 100), None, (30, 200), (30, 200)])
    probe = ResourceProbe(tmp_path, clock=lambda: now[0],
                          sample=lambda _: {'cpu_times': next(samples)})
    for tick in (0, 10, 20, 30):
        now[0] = tick
        assert probe.snapshot()['cpu_percent'] is None


def test_clock_reset_does_not_reuse_future_cache(tmp_path):
    now = [20]
    probe = ResourceProbe(tmp_path, clock=lambda: now[0],
                          sample=lambda _: {'cpu_times': (10, 100)})
    probe.snapshot()
    now[0] = 0
    assert probe.snapshot()['cpu_percent'] is None


def test_windows_battery_unknown_and_absent(monkeypatch):
    from types import SimpleNamespace
    from prometheus_assistant import resource_observations as module
    monkeypatch.setattr(module.os, 'name', 'nt')
    def status(pointer):
        pointer._obj.ac = 255
        pointer._obj.percent = 255
        pointer._obj.flag = 255
        return 1
    monkeypatch.setattr(module.ctypes, 'windll',
        SimpleNamespace(kernel32=SimpleNamespace(GetSystemPowerStatus=status)), raising=False)
    assert module._power_status() == (None, None)
    def present(pointer):
        pointer._obj.ac = 0
        pointer._obj.percent = 42
        pointer._obj.flag = 1
        return 1
    module.ctypes.windll.kernel32.GetSystemPowerStatus = present
    assert module._power_status() == (42, False)


def test_windows_cpu_kernel_already_contains_idle(monkeypatch):
    from types import SimpleNamespace
    from prometheus_assistant import resource_observations as module
    monkeypatch.setattr(module.os, 'name', 'nt')
    def times(idle, kernel, user):
        idle._obj.low = 20
        kernel._obj.low = 60
        user._obj.low = 40
        return 1
    monkeypatch.setattr(module.ctypes, 'windll',
        SimpleNamespace(kernel32=SimpleNamespace(GetSystemTimes=times)), raising=False)
    assert module._cpu_times() == (20, 100)


def battery_probe(tmp_path):
    state = {'now': 0, 'percent': 80, 'power': False, 'calls': 0}
    def sample(_):
        state['calls'] += 1
        return {'battery_percent': state['percent'], 'plugged_in': state['power']}
    return ResourceProbe(tmp_path, clock=lambda: state['now'], sample=sample), state


def test_device_battery_observation_requires_interval_and_preserves_cache(tmp_path):
    probe, state = battery_probe(tmp_path)
    first = probe.snapshot()
    assert first['battery_drain_percent'] is None
    assert first['battery_observation']['status'] == 'unavailable'
    state.update(now=30, percent=79)
    assert probe.snapshot()['battery_drain_percent'] is None
    state.update(now=60, percent=78)
    result = probe.snapshot()
    assert result['battery_drain_percent'] == 2
    assert result['battery_observation'] == dict(status='observed', scope='whole_device',
        percentage_points=2, interval_seconds=60, reason='Observed battery percentage change across the whole device; includes all activity.')
    assert 'whole device' in result['sources']['battery_drain_percent']
    result['battery_observation']['percentage_points'] = 99
    state.update(now=69, percent=10)
    assert probe.snapshot()['battery_observation']['percentage_points'] == 2
    assert state['calls'] == 3


def test_unchanged_percentage_is_observed_change_not_first_sample_zero(tmp_path):
    probe, state = battery_probe(tmp_path)
    assert probe.snapshot(force=True)['battery_drain_percent'] is None
    assert probe.snapshot(force=True)['battery_drain_percent'] is None
    state['now'] = 60
    observed = probe.snapshot()['battery_observation']
    assert observed['status'] == 'observed' and observed['percentage_points'] == 0
    assert observed['interval_seconds'] == 60 and observed['scope'] == 'whole_device'


@pytest.mark.parametrize('change,reason', [
    ({'power': True}, 'external power'), ({'power': None}, 'Power source'),
    ({'power': 1}, 'Power source'), ({'percent': None}, 'Battery percentage'),
    ({'percent': -1}, 'Battery percentage'), ({'percent': 101}, 'Battery percentage'),
    ({'percent': True}, 'Battery percentage'), ({'percent': float('nan')}, 'Battery percentage'),
    ({'percent': 10**1000}, 'Battery percentage'),
])
def test_unknown_or_invalid_battery_reading_breaks_interval_then_recovers(tmp_path, change, reason):
    probe, state = battery_probe(tmp_path)
    probe.snapshot()
    state.update(now=30, **change)
    invalid = probe.snapshot()
    assert invalid['battery_drain_percent'] is None
    assert reason in invalid['battery_observation']['reason']
    state.update(now=40, percent=70, power=False)
    assert probe.snapshot()['battery_drain_percent'] is None
    state.update(now=100, percent=69)
    recovered = probe.snapshot()['battery_observation']
    assert recovered['percentage_points'] == 1 and recovered['interval_seconds'] == 60


@pytest.mark.parametrize('reset,reason', [
    ({'now': 30, 'percent': 81}, 'increased'),
    ({'now': 61, 'percent': 79}, 'gap'),
    ({'now': -1, 'percent': 79}, 'clock'),
])
def test_charge_increase_long_gap_and_clock_reset_start_fresh_baseline(tmp_path, reset, reason):
    probe, state = battery_probe(tmp_path)
    probe.snapshot()
    state.update(reset)
    invalid = probe.snapshot()
    assert invalid['battery_drain_percent'] is None
    assert reason in invalid['battery_observation']['reason']
    state.update(now=reset['now'] + 60, percent=reset['percent'] - 1)
    recovered = probe.snapshot()['battery_observation']
    assert recovered['percentage_points'] == 1 and recovered['interval_seconds'] == 60


def test_battery_window_is_bounded_and_restart_has_no_old_baseline(tmp_path):
    probe, state = battery_probe(tmp_path)
    probe.snapshot()
    for elapsed in range(30, 901, 30):
        state['now'] = elapsed
        result = probe.snapshot()
    assert result['battery_observation']['interval_seconds'] == 900
    state['now'] = 930
    result = probe.snapshot()
    assert result['battery_drain_percent'] is None
    assert 'window' in result['battery_observation']['reason']
    state.update(now=990, percent=79)
    assert probe.snapshot()['battery_observation']['percentage_points'] == 1
    fresh, _ = battery_probe(tmp_path)
    assert fresh.snapshot()['battery_drain_percent'] is None


def test_concurrent_readers_share_one_battery_observation(tmp_path):
    probe, state = battery_probe(tmp_path)
    probe.snapshot()
    state.update(now=60, percent=79)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: probe.snapshot(), range(24)))
    assert state['calls'] == 2
    assert all(row['battery_observation']['percentage_points'] == 1 for row in results)
    results[0]['battery_observation']['percentage_points'] = 99
    assert results[1]['battery_observation']['percentage_points'] == 1


def test_device_discharge_does_not_calibrate_or_authorize_a_job(tmp_path):
    from prometheus_assistant.project_resources import ProjectResources
    now = [0]
    probe = ResourceProbe(tmp_path, clock=lambda: now[0], sample=lambda _: dict(
        total_ram_gib=16, available_ram_gib=8, free_disk_gib=20, plugged_in=False,
        battery_percent=80 if now[0] == 0 else 79,
        cpu_times=(20, 100) if now[0] == 0 else (110, 200)))
    probe.snapshot()
    now[0] = 60
    resources = ProjectResources(tmp_path, probe=probe, clock=lambda: now[0])
    assert resources.status()['observations']['battery_observation']['percentage_points'] == 1
    result = resources.review({'project': str(tmp_path), 'content': 'pass', 'checks': []})
    assert result['decision'] == 'defer'
    assert 'Battery budget is unknown.' in result['reasons']
    assert result['estimated_cost']['battery_percent'] is None
    assert result['history_samples'] == 0
