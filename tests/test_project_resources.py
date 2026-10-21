from types import SimpleNamespace
import pytest

from prometheus_assistant.project_resources import ProjectResources


def service(tmp_path):
    return ProjectResources(tmp_path, probe=SimpleNamespace(snapshot=lambda **_: dict(
        available_ram_gib=8, free_disk_gib=20, cpu_percent=10,
        battery_percent=80, plugged_in=True)))


def candidate(tmp_path):
    return {'project': str(tmp_path), 'content': 'example', 'checks': [
        {'preset': 'python-compile', 'target': 'first.py'},
        {'preset': 'python-compile', 'target': 'second.py'}]}


def results(plan):
    return {'status': 'passed', 'checks': [{**check, 'passed': True, 'cancelled': False,
        'timed_out': False, 'exit_code': 0, 'resource_measurements': {'scope': 'direct_process',
        'ram_gib': peak, 'cpu_seconds': 2, 'io_mib': 3}} for check, peak in zip(plan['checks'], (1, 2))]}


def test_real_counter_history_changes_next_forecast_and_survives_restart(tmp_path):
    plan = candidate(tmp_path)
    engine = service(tmp_path)
    assert engine.review(plan)['history_samples'] == 0
    observed = engine.observe(plan, results(plan))
    assert observed['recorded'] and observed['ram_gib'] == 2
    assert observed['cpu_seconds'] == 4
    assert observed['io_mib'] == 6 and observed['disk_io_measured'] is False
    next_review = service(tmp_path).review(plan)
    assert next_review['history_samples'] == 1
    assert next_review['estimated_cost']['ram_gib'] == 3
    assert next_review['estimated_cost']['cpu_seconds'] == 6
    assert next_review['estimated_cost']['disk_io_mib'] is None
    assert next_review['estimated_cost']['battery_percent'] is None


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'unknown', 'nan'])
def test_incomplete_or_invalid_counter_sets_do_not_train_forecast(tmp_path, kind):
    plan = candidate(tmp_path)
    engine = service(tmp_path)
    data = results(plan)
    if kind == 'missing':
        data['checks'].pop()
    elif kind == 'duplicate':
        data['checks'][1] = data['checks'][0]
    elif kind == 'unknown':
        data['checks'][0]['resource_measurements'] = {}
    else:
        data['checks'][0]['resource_measurements']['ram_gib'] = float('nan')
    assert engine.observe(plan, data)['recorded'] is False
    assert engine.review(plan)['history_samples'] == 0


def test_history_is_separated_by_host_and_check_family(tmp_path, monkeypatch):
    from prometheus_assistant import project_resources
    plan = candidate(tmp_path)
    engine = service(tmp_path)
    engine.observe(plan, results(plan))
    different = {**plan, 'checks': [{'preset': 'python-unittest', 'target': 'tests'}]}
    assert engine.review(different)['history_samples'] == 0
    monkeypatch.setattr(project_resources.platform, 'node', lambda: 'another-computer')
    assert engine.review(plan)['history_samples'] == 0


def test_reservation_released_when_execution_fails(tmp_path):
    engine = service(tmp_path)
    with pytest.raises(RuntimeError):
        with engine.reserve(candidate(tmp_path), 'task'):
            assert len(engine.scheduler.leases) == 1
            raise RuntimeError('failed execution')
    assert not engine.scheduler.leases


@pytest.mark.parametrize('changes', [{'cancelled': True}, {'timed_out': True},
                                    {'passed': False}, {'exit_code': 1}])
def test_partial_failed_workloads_never_calibrate_forecasts(tmp_path, changes):
    plan, engine = candidate(tmp_path), service(tmp_path)
    data = results(plan)
    data['checks'][0].update(changes)
    for _ in range(6):
        assert engine.observe(plan, data)['recorded'] is False
    assert engine.review(plan)['history_samples'] == 0


def test_job_committed_memory_and_cpu_never_mix_into_direct_history(tmp_path):
    plan, engine = candidate(tmp_path), service(tmp_path)
    data = results(plan)
    for check in data['checks']:
        check['resource_measurements']['job_metrics'] = dict(
            scope='job_associated_processes', peak_commit_gib=99, cpu_seconds=999,
            io_mib=999, snapshot_complete=True)
    report = engine.observe(plan, data)
    assert report['ram_gib'] == 2 and report['cpu_seconds'] == 4
    forecast = engine.review(plan)['estimated_cost']
    assert forecast['ram_gib'] == 3 and forecast['cpu_seconds'] == 6


def test_disk_observation_only_in_status_outside_scheduler_lock(tmp_path):
    engine = service(tmp_path)
    calls = []
    def snapshot():
        # Another thread must be able to acquire the scheduler during sampling.
        import threading
        def check_lock():
            acquired = engine.lock.acquire(timeout=.5)
            calls.append(acquired)
            if acquired:
                engine.lock.release()
        thread = threading.Thread(target=check_lock)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        return {'status': 'observed', 'scope': 'whole_device', 'busy_percent': 100}
    baseline = engine.review(candidate(tmp_path))
    engine.disk_probe = SimpleNamespace(snapshot=snapshot)
    assert engine.review(candidate(tmp_path))['decision'] == baseline['decision']
    with engine.reserve(candidate(tmp_path), 'disk-independent'):
        pass
    assert calls == []  # Not sampled in reviews, reservations, or history.
    observed = engine.status()
    assert observed['disk_observation']['busy_percent'] == 100
    assert calls == [True]
    assert engine.review(candidate(tmp_path))['estimated_cost']['disk_io_mib'] is None


def test_default_resource_service_does_not_install_disk_helper(tmp_path):
    engine = service(tmp_path)
    assert engine.disk_probe is None
    assert 'disk_observation' not in engine.status()


def job_results(plan):
    data = results(plan)
    for row in data['checks']:
        row['resource_measurements']['job_metrics'] = dict(schema_version=1,
            scope='job_associated_processes', snapshot_complete=True,
            active_processes=0, total_processes=2, cpu_seconds=10)
    return data


def test_job_cpu_separate_restart_forecast_and_direct_estimates(tmp_path):
    plan, engine = candidate(tmp_path), service(tmp_path)
    baseline = engine.review(plan)
    assert baseline['job_cpu_prediction']['cpu_seconds'] is None
    report = engine.observe(plan, job_results(plan))
    assert report['job_cpu_observation']['recorded'] and report['job_cpu_observation']['cpu_seconds'] == 20
    review = service(tmp_path).review(plan)
    assert review['job_cpu_prediction']['cpu_seconds'] == 30
    assert review['job_cpu_prediction']['samples'] == 1
    assert review['job_cpu_prediction']['confidence'] == 'low'
    assert not review['job_cpu_prediction']['used_for_admission']
    assert 'Partial descendant' in review['job_cpu_prediction']['coverage']
    assert review['estimated_cost']['ram_gib'] == 3
    assert review['estimated_cost']['cpu_seconds'] == 6
    assert review['estimated_cost']['disk_io_mib'] is None


@pytest.mark.parametrize('field,value', [('schema_version',True),('schema_version',2),
    ('scope','direct_process'),('snapshot_complete',False),('snapshot_complete',1),
    ('active_processes',1),('active_processes',False),('total_processes',0),
    ('total_processes',True),('total_processes',2**32),('cpu_seconds',10**1000),('cpu_seconds',True),('cpu_seconds',-1),
    ('cpu_seconds',float('nan')),('cpu_seconds',float('inf'))])
def test_invalid_one_job_counter_prevents_whole_workload_training(tmp_path,field,value):
    plan, engine = candidate(tmp_path), service(tmp_path)
    data = job_results(plan)
    data['checks'][1]['resource_measurements']['job_metrics'][field] = value
    report = engine.observe(plan,data)
    assert report['recorded']  # Independent direct-process counters remain valid.
    assert not report['job_cpu_observation']['recorded']
    assert engine.review(plan)['job_cpu_prediction']['samples'] == 0


@pytest.mark.parametrize('kind',['missing','empty','incomplete','wrong_target','duplicate','failed'])
def test_missing_mixed_or_incomplete_job_workload_does_not_train(tmp_path,kind):
    plan, engine = candidate(tmp_path), service(tmp_path)
    data = job_results(plan)
    if kind == 'missing':data['checks'][1]['resource_measurements'].pop('job_metrics')
    if kind == 'empty':data['checks'][1]['resource_measurements']['job_metrics'] = None
    if kind == 'incomplete':data['checks'].pop()
    if kind == 'wrong_target':data['checks'][1]['target'] = 'else.py'
    if kind == 'duplicate':data['checks'][1] = data['checks'][0]
    if kind == 'failed':data['checks'][1]['passed'] = False
    assert not engine.observe(plan,data)['job_cpu_observation']['recorded']
    assert engine.review(plan)['job_cpu_prediction']['samples'] == 0


def test_job_cpu_host_family_isolation(tmp_path,monkeypatch):
    from prometheus_assistant import project_resources
    plan, engine = candidate(tmp_path), service(tmp_path)
    engine.observe(plan,job_results(plan))
    other={**plan,'checks':[dict(preset='python-unittest',target='tests')]}
    assert engine.review(other)['job_cpu_prediction']['samples'] == 0
    monkeypatch.setattr(project_resources.platform,'node',lambda:'different-host')
    assert engine.review(plan)['job_cpu_prediction']['samples'] == 0


def test_job_cpu_zero_and_cpu_only_storage_preserve_direct_retention(tmp_path):
    import sqlite3
    plan, engine = candidate(tmp_path), service(tmp_path)
    data = job_results(plan)
    for row in data['checks']:
        row['resource_measurements']['job_metrics']['cpu_seconds'] = 0
        row['resource_measurements']['job_metrics']['peak_commit_gib'] = 999
        row['resource_measurements']['job_metrics']['io_mib'] = 999
    assert engine.observe(plan,data)['job_cpu_observation']['recorded']
    assert engine.review(plan)['job_cpu_prediction']['cpu_seconds'] == 0
    with sqlite3.connect(engine._job_history().path) as db:
        row=db.execute('SELECT ram_gib,disk_gib,cpu_seconds,disk_io_mib,battery_percent,vram_gib FROM samples').fetchone()
        assert row == (None,None,0,None,None,None)
    for _ in range(501):engine._job_history().record_observation(engine._job_category(plan),{'cpu_seconds':1})
    assert engine.review(plan)['history_samples'] == 1
    assert engine.review(plan)['job_cpu_prediction']['samples'] == 500


def cpu_engine(tmp_path,load):
    engine=service(tmp_path)
    engine.probe=SimpleNamespace(snapshot=lambda **_:dict(available_ram_gib=8,free_disk_gib=20,
        cpu_percent=load[0],battery_percent=100,plugged_in=True))
    return engine


def train_cpu(engine,plan,source,values):
    history=engine._job_history() if source=='job' else engine._history()
    category=engine._job_category(plan) if source=='job' else engine._category(plan)
    for cpu in values:history.record_observation(category,{'ram_gib':.25,'cpu_seconds':cpu} if source=='direct' else {'cpu_seconds':cpu})


def test_minimum_actual_cpu_samples_not_missing_rows(tmp_path):
    plan,load=candidate(tmp_path),[70]
    engine=cpu_engine(tmp_path,load)
    train_cpu(engine,plan,'direct',[None]*8+[30]*4)
    assert engine.review(plan)['decision']=='run_local'
    assert engine.review(plan)['cpu_admission']['learned_cpu_seconds'] is None
    train_cpu(engine,plan,'direct',[30])
    review=engine.review(plan)
    assert review['decision']=='defer'
    assert review['cpu_admission']['samples']==5
    assert review['cpu_admission']['source']=='direct_process'


def test_cpu_uses_max_not_sum_and_survives_restart(tmp_path):
    plan,load=candidate(tmp_path),[70]
    engine=cpu_engine(tmp_path,load)
    train_cpu(engine,plan,'direct',[10]*5)
    train_cpu(engine,plan,'job',[20]*5)
    review=cpu_engine(tmp_path,load).review(plan)
    assert review['cpu_admission']['learned_cpu_seconds']==24
    assert review['cpu_admission']['source']=='job_associated_processes'
    assert review['decision']=='run_local' # Summing forecasts would wrongly defer.
    train_cpu(engine,plan,'job',[25])
    assert cpu_engine(tmp_path,load).review(plan)['decision']=='defer'


def test_reserve_refreshes_and_preserves_cpu_defer_and_cleanup(tmp_path):
    plan,load=candidate(tmp_path),[10]
    engine=cpu_engine(tmp_path,load)
    train_cpu(engine,plan,'job',[30]*5)
    assert engine.review(plan)['decision']=='run_local'
    load[0]=70
    with pytest.raises(ValueError,match='deferred'):
        with engine.reserve(plan,'busy'):pytest.fail('must not yield')
    assert not engine.scheduler.leases
    load[0]=10
    with pytest.raises(RuntimeError):
        with engine.reserve(plan,'idle'):
            assert engine.scheduler.leases
            raise RuntimeError('test failure')
    assert not engine.scheduler.leases


def test_low_sample_job_does_not_override_eligible_direct_cpu(tmp_path):
    plan,engine=candidate(tmp_path),cpu_engine(tmp_path,[70])
    train_cpu(engine,plan,'direct',[1]*5)
    train_cpu(engine,plan,'job',[999]*4)
    review=engine.review(plan)
    assert review['decision']=='run_local'
    assert review['cpu_admission']['source']=='direct_process'
    assert review['job_cpu_prediction']['used_for_admission'] is False
