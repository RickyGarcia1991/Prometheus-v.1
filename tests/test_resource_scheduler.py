import pytest

from prometheus_assistant.resource_scheduler import Cost, Policy, Signals, Scheduler, WorkloadHistory


def signals(**changes):
    return Signals(**dict(dict(available_ram_gib=8, free_disk_gib=20,
        cpu_percent=10, battery_percent=80, plugged_in=True), **changes))


def test_modes_and_dwell_do_not_delay_critical_recovery():
    scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=2, dwell_seconds=30))
    assert scheduler.mode(signals(), now=0, foreground=False) == 'Idle'
    assert scheduler.mode(signals(), now=31, foreground=True) == 'Balanced'
    assert scheduler.mode(signals(), now=62, foreground=True, requested='Performance') == 'Performance'
    assert scheduler.mode(signals(battery_percent=20, plugged_in=False), now=63) == 'Battery Saver'
    assert scheduler.mode(signals(available_ram_gib=.3), now=64) == 'Critical Recovery'
    assert scheduler.mode(signals(), now=70) == 'Critical Recovery'
    assert scheduler.mode(signals(), now=95, foreground=True) == 'Balanced'


def test_battery_hysteresis_and_manual_override():
    scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=2))
    assert scheduler.mode(signals(battery_percent=25, plugged_in=False), now=0) == 'Battery Saver'
    assert scheduler.mode(signals(battery_percent=35, plugged_in=False), now=100, requested='Performance') == 'Battery Saver'
    assert scheduler.mode(signals(battery_percent=45, plugged_in=False), now=200) == 'Idle'


def test_unknown_pressure_defers_without_claiming_safe():
    scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=2))
    result = scheduler.assess(signals(available_ram_gib=None), Cost(ram_gib=1), now=0)
    assert result['decision'] == 'defer'
    assert result['mode'] == 'Critical Recovery'
    assert result['unknown_metrics']


def test_reserve_and_leases_prevent_double_admission():
    scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=2))
    first = scheduler.acquire('first', signals(), Cost(ram_gib=4), now=0)
    assert first['decision'] == 'run_local'
    assert scheduler.acquire('second', signals(), Cost(ram_gib=4), now=1)['decision'] == 'defer'
    scheduler.release('first')
    assert scheduler.acquire('second', signals(), Cost(ram_gib=4), now=2)['decision'] == 'run_local'
    with pytest.raises(ValueError):
        scheduler.acquire('second', signals(), Cost(ram_gib=1), now=3)


def test_history_retention_restart_and_conservative_prediction(tmp_path):
    path = tmp_path / 'metrics.sqlite3'
    store = WorkloadHistory(path, limit=2)
    for peak in (9, 2, 3):
        store.record('compile', Cost(ram_gib=peak, cpu_seconds=2, disk_io_mib=4, battery_percent=.1))
    estimate = WorkloadHistory(path, limit=2).predict('compile', Cost(ram_gib=1))
    assert estimate['samples'] == 2
    assert estimate['cost'].ram_gib == 4.5
    assert estimate['cost'].cpu_seconds == 3
    assert estimate['confidence'] == 'low'
    assert store.predict('unknown', Cost(ram_gib=2))['cost'].ram_gib == 3


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf'), True])
def test_invalid_costs_rejected(bad):
    with pytest.raises(ValueError):
        Cost(ram_gib=bad)


def test_cpu_disk_and_battery_budgets():
    scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=2))
    assert scheduler.assess(signals(cpu_percent=99), Cost(ram_gib=1), now=0)['decision'] == 'defer'
    assert scheduler.assess(signals(free_disk_gib=2.1), Cost(ram_gib=1, disk_gib=1), now=1)['decision'] == 'defer'
    assert scheduler.assess(signals(battery_percent=40, plugged_in=False), Cost(ram_gib=1, battery_percent=20), now=2)['decision'] == 'defer'


def test_partial_observations_do_not_invent_disk_or_battery_usage(tmp_path):
    store = WorkloadHistory(tmp_path/'measurements.sqlite3')
    store.record_observation('compile', {'ram_gib': 1, 'cpu_seconds': 2})
    result = store.predict('compile', Cost(ram_gib=.25))
    assert result['cost'].ram_gib == 1.5
    assert result['cost'].cpu_seconds == 3
    assert result['cost'].battery_percent is None
    assert result['cost'].disk_io_mib is None
    with pytest.raises(ValueError):
        store.record_observation('compile', {'ram_gib': float('nan')})
    with pytest.raises(ValueError):
        store.record_observation('compile', {})


def test_learned_heavy_cpu_tightens_busy_admission():
    scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=.5))
    signals = Signals(8,20,cpu_percent=60,plugged_in=True)
    assert scheduler.assess(signals,Cost(.25),now=1)['decision']=='run_local'
    result=scheduler.acquire('heavy',signals,Cost(.25),now=2,learned_cpu_seconds=30)
    assert result['decision']=='defer' and not scheduler.leases


@pytest.mark.parametrize('learned,load,decision',[(None,70,'run_local'),(29.99,60,'run_local'),
    (30,59.99,'run_local'),(30,60,'defer'),(30,70,'defer'),(300,10,'run_local'),
    (0,85,'defer'),(None,None,'defer')])
def test_learned_cpu_boundaries(learned,load,decision):
    engine=Scheduler(Policy(2,.5))
    assert engine.assess(Signals(8,20,cpu_percent=load,plugged_in=True),Cost(.25),now=1,
        learned_cpu_seconds=learned)['decision']==decision


@pytest.mark.parametrize('bad',[True,-1,float('nan'),float('inf'),'30',10**1000])
def test_bad_learned_cpu_rejected_without_lease(bad):
    engine=Scheduler(Policy(2,.5))
    with pytest.raises(ValueError):engine.acquire('job',Signals(8,20,cpu_percent=10,plugged_in=True),Cost(.25),now=1,learned_cpu_seconds=bad)
    assert not engine.leases


@pytest.mark.parametrize('options',[{'heavy_cpu_seconds':0},{'heavy_cpu_seconds':-1},
    {'heavy_cpu_ceiling':0},{'heavy_cpu_ceiling':101}])
def test_invalid_learned_thresholds(options):
    with pytest.raises(ValueError):Policy(2,.5,**options)


def test_explicit_critical_recovery_is_immediate_but_exit_dwells():
    scheduler=Scheduler(Policy(reserve_ram_gib=2,reserve_disk_gib=.5))
    signal=Signals(available_ram_gib=8,free_disk_gib=20,cpu_percent=10,battery_percent=80,plugged_in=True)
    assert scheduler.mode(signal,now=100,requested='Performance')=='Performance'
    assert scheduler.mode(signal,now=101,requested='Critical Recovery')=='Critical Recovery'
    assert scheduler.mode(signal,now=102,requested='Performance')=='Critical Recovery'
    assert scheduler.mode_details['dwell_pending']
