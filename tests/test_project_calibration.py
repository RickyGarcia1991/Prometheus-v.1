import sys
from test_project_resources import service, candidate, results


def test_calibration_explained_without_samples(tmp_path):
    review=service(tmp_path).review(candidate(tmp_path))
    assert review['prediction_calibration']['applied'] is False
    assert review['prediction_calibration']['baseline_forecasts']['direct_ram_gib']==review['estimated_cost']['ram_gib']


def test_original_forecast_is_bound_and_duplicate_observation_is_idempotent(tmp_path):
    engine=service(tmp_path);plan=candidate(tmp_path);data=results(plan)
    for row in data['checks']:row['argv']=[sys.executable,'-m','py_compile','fixture.py']
    with engine.reserve(plan,'calibration') as reservation:
        baseline=reservation['prediction_calibration']['baseline_forecasts']['direct_ram_gib']
        reservation['prediction_calibration']['baseline_forecasts']['direct_ram_gib']=999
        report=engine.observe(plan,data,reservation=reservation)
        assert report['prediction_calibration']['recorded']
        assert not engine.observe(plan,data,reservation=reservation)['prediction_calibration']['recorded']
    review=service(tmp_path).review(plan)
    assert review['prediction_calibration']['evaluation']['samples']==1
    assert baseline<999


def test_interrupted_reservation_records_censored_outcome(tmp_path):
    engine=service(tmp_path);plan=candidate(tmp_path)
    with engine.reserve(plan,'interrupted'):pass
    assert engine.review(plan)['prediction_calibration']['evaluation']['counts']['interrupted']==1


def test_unsupported_runtime_family_keeps_baseline(tmp_path):
    plan=candidate(tmp_path);plan['checks']=[{'preset':'node-test','target':'.'}]
    review=service(tmp_path).review(plan)
    assert review['prediction_calibration']['status']=='unsupported'
    assert not review['prediction_calibration']['applied']


def native_results(plan):
    data = results(plan)
    for row in data['checks']:
        row['argv'] = [sys.executable, '-m', 'py_compile', row['target']]
    return data


def test_pending_is_durable_before_execution_and_survives_lost_context(tmp_path):
    engine = service(tmp_path); plan = candidate(tmp_path)
    with engine.reserve(plan, 'crash') as reservation:
        scope = reservation['prediction_calibration']['scope']
        evaluation = service(tmp_path)._calibration_history().evaluate(scope)
        assert evaluation['pending_runs'] == 1
        assert evaluation['counts']['interrupted'] == 1
        engine._accuracy_reservations.clear()  # simulate loss of process memory
    assert service(tmp_path)._calibration_history().evaluate(scope)['pending_runs'] == 1


def test_mismatched_observation_does_not_consume_pending(tmp_path):
    engine = service(tmp_path); plan = candidate(tmp_path)
    with engine.reserve(plan, 'mismatch') as reservation:
        scope = reservation['prediction_calibration']['scope']
        changed = {**plan, 'content': 'different'}
        assert not engine.observe(changed, native_results(changed), reservation=reservation)['prediction_calibration']['recorded']
        assert engine._calibration_history().evaluate(scope)['pending_runs'] == 1
    evaluation = engine._calibration_history().evaluate(scope)
    assert evaluation['pending_runs'] == 0
    assert evaluation['counts']['interrupted'] == 1


def test_copied_reservation_cannot_consume_pending(tmp_path):
    engine = service(tmp_path); plan = candidate(tmp_path)
    with engine.reserve(plan, 'copy') as reservation:
        assert not engine.observe(plan, native_results(plan), reservation=dict(reservation))['prediction_calibration']['recorded']
        assert engine.observe(plan, native_results(plan), reservation=reservation)['prediction_calibration']['recorded']


def test_begin_failure_keeps_execution_and_disables_learning(tmp_path, monkeypatch):
    from prometheus_assistant.prediction_calibration import CalibrationHistory
    engine = service(tmp_path); plan = candidate(tmp_path)
    def broken(*args, **kwargs): raise OSError('disk unavailable')
    monkeypatch.setattr(CalibrationHistory, 'begin', broken)
    with engine.reserve(plan, 'begin-failure') as reservation:
        assert reservation['prediction_calibration']['recording_available'] is False
        assert not engine.observe(plan, native_results(plan), reservation=reservation)['prediction_calibration']['recorded']
    assert not engine.scheduler.leases
    assert engine.review(plan)['prediction_calibration']['evaluation']['samples'] == 0


def test_terminal_failure_retains_durable_censor(tmp_path, monkeypatch):
    from prometheus_assistant.prediction_calibration import CalibrationHistory
    engine = service(tmp_path); plan = candidate(tmp_path)
    with engine.reserve(plan, 'write-failure') as reservation:
        scope = reservation['prediction_calibration']['scope']
        def broken(*args, **kwargs): raise OSError('disk unavailable')
        monkeypatch.setattr(CalibrationHistory, 'record', broken)
        assert not engine.observe(plan, native_results(plan), reservation=reservation)['prediction_calibration']['recorded']
    assert not engine.scheduler.leases
    assert engine._calibration_history().evaluate(scope)['pending_runs'] == 1


import pytest


@pytest.mark.parametrize('outcome', ['failed', 'timed_out', 'cancelled', 'unavailable'])
def test_failed_or_untrusted_runs_resolve_pending_as_censored(tmp_path, outcome):
    engine = service(tmp_path); plan = candidate(tmp_path); data = native_results(plan)
    if outcome == 'failed': data['status'] = 'failed'
    elif outcome == 'unavailable': data['checks'][0]['argv'] = ['missing-interpreter']
    else: data['checks'][0][outcome] = True
    with engine.reserve(plan, outcome) as reservation:
        scope = reservation['prediction_calibration']['scope']
        assert engine.observe(plan, data, reservation=reservation)['prediction_calibration']['recorded']
    evaluation = engine._calibration_history().evaluate(scope)
    assert evaluation['counts'][outcome] == 1
    assert evaluation['pending_runs'] == 0


def test_eligible_uplift_preserves_policy_and_original_forecast(tmp_path):
    import json
    import sqlite3
    engine = service(tmp_path); plan = candidate(tmp_path)
    for _ in range(5):
        engine._history().record_observation(engine._category(plan), {'ram_gib': .25, 'cpu_seconds': 2})
    initial = engine.review(plan)
    scope = initial['prediction_calibration']['scope']
    baseline = initial['prediction_calibration']['baseline_forecasts']
    history = engine._calibration_history()
    for index in range(30):
        delta = .1 if index < 20 else .2
        actuals = {key: value + delta for key, value in baseline.items() if value is not None}
        history.record(f'{index:032x}', scope, baseline, actuals)
    review = engine.review(plan)
    assert review['prediction_calibration']['applied']
    assert review['estimated_cost']['ram_gib'] == pytest.approx(baseline['direct_ram_gib'] + .1)
    assert review['estimated_cost']['cpu_seconds'] == pytest.approx(baseline['direct_cpu_seconds'] + .1)
    assert review['policy'] == initial['policy']
    assert review['policy_calibrated'] is False
    assert review['estimated_cost']['disk_gib'] == initial['estimated_cost']['disk_gib']
    with engine.reserve(plan, 'qualified') as reservation:
        assert engine.observe(plan, native_results(plan), reservation=reservation)['prediction_calibration']['recorded']
    with sqlite3.connect(history.path) as connection:
        payload = json.loads(connection.execute('SELECT payload FROM samples ORDER BY sequence DESC LIMIT 1').fetchone()[0])
    assert payload['forecasts'] == baseline


def test_eligible_host_calibration_not_used_for_alternate_bundled_runtime(tmp_path, monkeypatch):
    from prometheus_assistant import hardware
    monkeypatch.setattr(hardware, 'resource_root', lambda: None)
    engine = service(tmp_path); plan = candidate(tmp_path)
    initial = engine.review(plan)
    calibration = initial['prediction_calibration']
    for index in range(30):
        engine._calibration_history().record(f'{index:032x}', calibration['scope'],
            calibration['baseline_forecasts'], {'direct_ram_gib': calibration['baseline_forecasts']['direct_ram_gib'] + (.1 if index < 20 else .2)})
    assert engine.review(plan)['prediction_calibration']['applied']
    resources = tmp_path / 'resources'
    runtime = resources / 'Python/python.exe'
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b'alternate runtime fixture, never executed')
    monkeypatch.setattr(hardware, 'resource_root', lambda: resources)
    review = engine.review(plan)
    assert review['prediction_calibration']['status'] == 'unsupported'
    assert not review['prediction_calibration']['applied']
    assert review['estimated_cost'] == initial['estimated_cost']
    with engine.reserve(plan, 'alternate') as reservation:
        assert reservation['prediction_calibration']['scope'] is None
        assert not reservation['prediction_calibration']['recording_available']
