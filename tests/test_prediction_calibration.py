"""Chronological calibration controls and durable prospective evidence."""
import sqlite3
import pytest

from prometheus_assistant.prediction_calibration import CalibrationHistory, METRICS

SCOPE = 'a'*64


def values(ram=1., cpu=100.):
    return dict(direct_ram_gib=ram, direct_cpu_seconds=cpu, job_cpu_seconds=cpu)


def fill(history, count=30, start=0, scope=SCOPE, train=None, holdout=None):
    for index in range(start, start+count):
        history.record(f'{index:032x}', scope, values(),
            (train or values(1.1, 110.)) if index < 20 else (holdout or values(1.2, 120.)))


def test_first_twenty_train_and_next_ten_validate_without_policy_mutation(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    fill(history)
    result=history.evaluate(SCOPE)
    assert result['samples']==30 and result['shadow_only'] and result['policy_tuned'] is False
    assert result['counts']['completed']==30
    for metric in METRICS:
        row=result['metrics'][metric]
        assert row['status']=='eligible'
        assert row['train_samples']==20 and row['holdout_samples']==10 and row['training_frozen']
        assert row['correction']>0 and row['candidate']['mae']<row['baseline']['mae']
        assert row['candidate']['underprediction_count']<=row['baseline']['underprediction_count']


def test_holdout_never_retrains_and_restart_preserves_dataset(tmp_path):
    path=tmp_path/'calibration.db';history=CalibrationHistory(path)
    fill(history)
    before=history.evaluate(SCOPE)
    fill(history,count=5,start=30,holdout=values(1.8,150.))
    after=CalibrationHistory(path).evaluate(SCOPE)
    assert before['dataset_sha256'] != after['dataset_sha256']
    for metric in METRICS:
        assert before['metrics'][metric]['training_sha256']==after['metrics'][metric]['training_sha256']
        assert before['metrics'][metric]['correction']==after['metrics'][metric]['correction']
        assert after['metrics'][metric]['holdout_samples']==15
    assert CalibrationHistory(path).evaluate(SCOPE)==after


@pytest.mark.parametrize('count',[0,19,20,29])
def test_minimum_chronological_counts(tmp_path,count):
    history=CalibrationHistory(tmp_path/'calibration.db');fill(history,count)
    assert all(r['status']=='insufficient' for r in history.evaluate(SCOPE)['metrics'].values())


@pytest.mark.parametrize('outcome',['failed','timed_out','cancelled','unavailable','interrupted'])
def test_censored_attempts_cannot_disappear_from_qualification(tmp_path,outcome):
    history=CalibrationHistory(tmp_path/'calibration.db');fill(history)
    history.record('f'*32,SCOPE,values(),{},outcome=outcome)
    result=history.evaluate(SCOPE)
    assert result['counts'][outcome]==1 and result['samples']==31
    assert all(r['status']=='rejected' and 'censored_outcomes' in r['reason_codes'] for r in result['metrics'].values())


def test_missing_scope_cannot_borrow_other_metric_evidence(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db');fill(history)
    history.record('f'*32,SCOPE,values(),{'direct_ram_gib':1.2})
    rows=history.evaluate(SCOPE)['metrics']
    assert rows['direct_ram_gib']['status']=='eligible'
    assert rows['direct_cpu_seconds']['status']==rows['job_cpu_seconds']['status']=='rejected'
    assert history.evaluate('b'*64)['samples']==0


def test_saturated_training_margin_cannot_qualify(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    fill(history,train=values(3.,160.),holdout=values(4.,180.))
    assert all(r['status']=='rejected' and 'correction_cap_saturated' in r['reason_codes'] for r in history.evaluate(SCOPE)['metrics'].values())


def test_no_improvement_is_rejected(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    fill(history,train=values(),holdout=values())
    assert all(r['status']=='rejected' and 'no_strict_mae_improvement' in r['reason_codes'] for r in history.evaluate(SCOPE)['metrics'].values())


def test_overprediction_budget_is_a_separate_rejection(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    fill(history,train=values(1.9,150.),holdout=values(.1,1.))
    assert all('excess_extra_overprediction' in r['reason_codes'] for r in history.evaluate(SCOPE)['metrics'].values())


def test_duplicate_is_idempotent_but_reused_identity_is_refused(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    assert history.record('1'*32,SCOPE,values(),values())['recorded']
    before=history.evaluate(SCOPE)
    assert history.record('1'*32,SCOPE,values(),values())['reason']=='duplicate'
    assert history.evaluate(SCOPE)==before
    with pytest.raises(ValueError):history.record('1'*32,SCOPE,values(),values(2.))
    with pytest.raises(ValueError):history.record('1'*32,'b'*64,values(),values())


def test_pending_survives_restart_and_only_exact_terminal_can_replace(tmp_path):
    path=tmp_path/'calibration.db';history=CalibrationHistory(path);fill(history)
    assert history.begin('f'*32,SCOPE,values())['recorded']
    history=CalibrationHistory(path)
    pending=history.evaluate(SCOPE)
    assert pending['pending_runs']==1 and pending['counts']['interrupted']==1
    assert all(r['status']=='rejected' for r in pending['metrics'].values())
    with pytest.raises(ValueError):history.record('f'*32,SCOPE,values(2.),values(2.1))
    with pytest.raises(ValueError):history.record('f'*32,'b'*64,values(),values(1.2,120.))
    assert history.begin('f'*32,SCOPE,values())['reason']=='duplicate'
    assert history.record('f'*32,SCOPE,values(),values(1.2,120.))['reason']=='completed_pending'
    result=history.evaluate(SCOPE)
    assert result['pending_runs']==0 and result['counts']['completed']==31
    assert all(r['status']=='eligible' for r in result['metrics'].values())
    with pytest.raises(ValueError):history.begin('f'*32,SCOPE,values())


def test_late_pending_completion_cannot_rewrite_frozen_training(tmp_path):
    path=tmp_path/'calibration.db';history=CalibrationHistory(path)
    history.begin('f'*32,SCOPE,values())
    fill(history)
    before=history.evaluate(SCOPE)
    history=CalibrationHistory(path)
    history.record('f'*32,SCOPE,values(),values(1.9,150.))
    after=history.evaluate(SCOPE)
    for metric in METRICS:
        assert after['metrics'][metric]['training_runs_sha256']==before['metrics'][metric]['training_runs_sha256']
        assert after['metrics'][metric]['training_sha256']==before['metrics'][metric]['training_sha256']
        assert after['metrics'][metric]['correction']==before['metrics'][metric]['correction']
        assert after['metrics'][metric]['holdout_samples']==11


def test_capacity_retains_training_and_requires_new_version(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db');fill(history,count=100)
    before=history.evaluate(SCOPE)
    assert before['context_full'] and all(r['status']=='rejected' for r in before['metrics'].values())
    assert history.record('f'*32,SCOPE,values(),values())['reason']=='context_full'
    assert history.evaluate(SCOPE)==before
    for index in range(1,8):history.record(f'{100+index:032x}',f'{index:064x}',values(),values())
    assert history.record('e'*32,'f'*64,values(),values())['reason']=='scope_capacity'
    assert history.evaluate(SCOPE)==before


@pytest.mark.parametrize('bad',[True,-1,float('nan'),float('inf'),1e13,'1',[],{}])
def test_invalid_numbers_never_enter_evidence(tmp_path,bad):
    history=CalibrationHistory(tmp_path/'calibration.db')
    with pytest.raises(ValueError):history.record('1'*32,SCOPE,values(ram=bad),values())
    assert history.evaluate(SCOPE)['samples']==0


def test_unknown_metric_and_identity_rejected(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    with pytest.raises(ValueError):history.record('1'*32,SCOPE,{'gpu':1},{})
    with pytest.raises(ValueError):history.record('private/path',SCOPE,{}, {})
    with pytest.raises(ValueError):history.evaluate('human-readable-device')


def test_backwards_clock_does_not_reorder_training(tmp_path):
    now=[100.];history=CalibrationHistory(tmp_path/'calibration.db',clock=lambda:now[0])
    history.begin('1'*32,SCOPE,values());now[0]=99
    with pytest.raises(ValueError):history.begin('2'*32,SCOPE,values())
    # Completing an already ordered reservation does not rewrite its timestamp.
    assert history.record('1'*32,SCOPE,values(),values())['recorded']


def test_corrupt_values_fail_closed(tmp_path):
    path=tmp_path/'calibration.db';history=CalibrationHistory(path);fill(history,count=1)
    with sqlite3.connect(path) as db:db.execute("UPDATE samples SET payload='{}'")
    with pytest.raises(ValueError):history.evaluate(SCOPE)


def test_zero_forecasts_use_absolute_residual_without_division(tmp_path):
    history=CalibrationHistory(tmp_path/'calibration.db')
    for index in range(30):
        history.record(f'{index:032x}',SCOPE,values(0,0),values(.1 if index<20 else .2,1 if index<20 else 2))
    assert all(r['status']=='eligible' for r in history.evaluate(SCOPE)['metrics'].values())
