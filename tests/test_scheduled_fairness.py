"""Fair automatic admission across persisted, exact approved waiting jobs."""
import pytest

from test_scheduled_syntax import controller


def approve(f, number):
    job_id = f'{number:032x}'
    review = f.controller.prepare(f.files, job_id, session='e'*32)
    f.decide(review)
    return job_id


@pytest.mark.parametrize('offline', [False, True])
def test_deferred_oldest_does_not_starve_younger_and_regains_turn(controller, offline):
    f = controller
    older = approve(f, 1)
    f.now += 1
    younger = approve(f, 2)
    if offline:
        f.controller.adapters = []
    else:
        f.ram = .1
    assert f.controller.run_one()['job_id'] == older
    f.now += 31
    f.ram = 4
    f.controller.adapters = [f.adapter]
    # Both are due now. The untouched younger job has waited longer for an
    # attempt, even though the older approval remains first in queue.list().
    assert f.controller.run_one()['job_id'] == younger
    assert f.controller.run_one()['job_id'] == older
    assert f.controller.run_one() is None
    assert [r['job_id'] for r in f.executions] == [younger, older]
    assert f.controller.run_one(older, recover=True)['state'] == 'completed'
    assert len(f.executions) == 2


def test_restart_retains_attempt_order_and_one_admission_per_tick(controller):
    f = controller
    first, second = approve(f, 1), approve(f, 2)
    original = f.adapter.admission
    admissions = []
    def admission(req=None):
        admissions.append(req['job_id'])
        return original(req)
    f.adapter.admission = admission
    f.ram = .1
    assert f.controller.run_one()['job_id'] == first
    f.now += 31
    f.controller = f.reopen()
    assert f.controller.run_one()['job_id'] == second
    assert admissions == [first, second]
    f.now += 31
    f.ram = 4
    assert f.controller.run_one()['job_id'] == first
    assert len(f.executions) == 1


def test_explicit_target_keeps_exact_selection(controller):
    f = controller
    first, second = approve(f, 1), approve(f, 2)
    f.ram = .1
    f.controller.run_one(first)
    f.now += 31
    f.ram = 4
    assert f.controller.run_one(first)['job_id'] == first
    assert f.controller.run_one(first) is None
    assert f.controller.run_one()['job_id'] == second
    assert len(f.executions) == 2


@pytest.mark.parametrize('now', [99, 3700])
def test_backward_clock_and_expiry_execute_nothing(controller, now):
    f = controller
    approve(f, 1)
    approve(f, 2)
    f.now = now
    assert f.controller.run_one() is None
    assert {r['state'] for r in f.queue.list()} == {'expired'}
    assert f.executions == []


def test_equal_attempt_times_use_approval_then_job_identity(controller):
    f = controller
    higher = approve(f, 2)
    lower = approve(f, 1)
    f.now += 1
    later = approve(f, 3)
    # Equal attempt deadlines must retain approval chronology, then identity.
    for job in [higher, lower, later]:
        f.queue.update_wait(job, [], now=f.now, next_attempt_at=150)
    f.now = 150
    assert [f.controller.run_one()['job_id'] for _ in range(3)] == [lower, higher, later]


def test_bounded_hundred_job_queue_advances_one_job_per_tick(controller):
    f = controller
    ids = [approve(f, i) for i in range(100)]
    with pytest.raises(ValueError, match='capacity'):
        approve(f, 100)
    f.ram = .1
    assert f.controller.run_one()['job_id'] == ids[0]
    f.now += 31
    assert f.controller.run_one()['job_id'] == ids[1]
    assert len(f.queue.list()) == 100
    assert sum(bool(row['reasons']) for row in f.queue.list()) == 2
    assert f.executions == []


@pytest.mark.parametrize('offline', [False, True])
def test_unavailable_old_destination_yields_to_ready_exact_peer(controller, offline):
    from copy import deepcopy
    from types import SimpleNamespace
    f = controller
    old_target = deepcopy(f.target)
    older = approve(f, 1)
    f.now += 1
    f.target['device_id'] = 'other-device'
    f.target['device_name'] = 'other-device'
    younger = approve(f, 2)
    admission_calls = []
    original = f.adapter.admission
    def busy(req=None):
        admission_calls.append(('old', req['job_id']))
        report = original(req)
        report['decision'] = 'defer'
        return report
    def ready(req=None):
        admission_calls.append(('young', req['job_id']))
        return original(req)
    old_adapter = SimpleNamespace(target=lambda: deepcopy(old_target), admission=busy,
                                  execute=f.adapter.execute, recover=f.adapter.recover)
    f.adapter.admission = ready
    f.controller.adapters = [f.adapter] + ([] if offline else [old_adapter])
    assert f.controller.run_one()['job_id'] == older
    f.now += 31
    result = f.controller.run_one()
    assert result['job_id'] == younger and result['state'] == 'completed'
    assert [r['device_id'] for r in f.executions] == ['other-device']
    # The older target is still unavailable; a subsequent tick gives it a turn
    # without redirecting its approved snapshot to the younger job's device.
    result = f.controller.run_one()
    assert result['job_id'] == older and result['state'] == 'waiting'
    assert len(f.executions) == 1
    assert len(admission_calls) == (1 if offline else 3)
