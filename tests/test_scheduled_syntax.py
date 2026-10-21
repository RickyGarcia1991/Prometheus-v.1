from types import SimpleNamespace
from contextlib import contextmanager
from copy import deepcopy
import threading

import pytest

from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.remote_job_protocol import validate_receipt
from prometheus_assistant.scheduled_syntax import ScheduledSyntax, assess
from prometheus_assistant.syntax_waiting_store import WaitingStore

from prometheus_assistant.scheduled_syntax import choose_destination


def candidate(device, ram, cpu=20, decision='ready', **overrides):
    target=dict(account_id='local' if device=='dell' else 'account',device_id=device,
        device_name=device,worker_digest='b'*64,configuration_digest='c'*64,online=True)
    report=dict(authenticated=True,decision=decision,reasons=[],observed_at=100,expires_at=130,
        observations=dict(available_ram_gib=ram,free_disk_gib=10,cpu_percent=cpu,plugged_in=True,battery_percent=100))
    report.update(overrides)
    return SimpleNamespace(target=lambda:dict(target),admission=lambda request=None:dict(report))


def test_automatic_choice_uses_eligible_headroom_not_input_order():
    dell=candidate('dell',3);surface=candidate('surface',6)
    picked=choose_destination([dell,surface],now=100)
    assert picked['target']['device_id']=='surface'
    assert picked['placement']['selected']=='surface'
    assert choose_destination([surface,dell],now=100)['target']==picked['target']


def test_ready_local_wins_over_busy_surface_and_stale_reading():
    for remote in (candidate('surface',10,decision='defer'),candidate('surface',10,expires_at=100)):
        assert choose_destination([remote,candidate('dell',4)],now=100)['target']['device_id']=='dell'


def test_remote_lightweight_reserves_larger_local_headroom_and_waits_remotely():
    for decision in ('ready','defer'):
        picked=choose_destination([candidate('dell',12),candidate('surface',3,decision=decision)],
            now=100,strategy='remote-lightweight-v1')
        assert picked['target']['device_id']=='surface'
        assert picked['resource_admission']['decision']==decision
        assert picked['placement']['strategy']=='remote-lightweight-v1'
        assert len(picked['placement']['candidates'])==1


def test_remote_lightweight_does_not_fall_back_without_eligible_remote():
    with pytest.raises(ValueError,match='No eligible'):
        choose_destination([candidate('dell',12),candidate('surface',8,decision='reject')],
            now=100,strategy='remote-lightweight-v1')


def test_unknown_placement_is_rejected_before_query():
    with pytest.raises(ValueError,match='strategy'):
        choose_destination([],now=100,strategy='anything')


def test_all_busy_can_be_reviewed_for_bounded_waiting():
    picked=choose_destination([candidate('dell',1,decision='defer'),candidate('surface',.6,decision='defer')],now=100)
    assert picked['resource_admission']['decision']=='defer'
    assert 'wait' in picked['placement']['reason'].lower()


def test_resources_are_assessed_after_all_queries():
    local=candidate('dell',4,observed_at=101,expires_at=131)
    remote=candidate('surface',3,observed_at=132,expires_at=162)
    result=choose_destination([local,remote],now=100,clock=lambda:132)
    assert result['target']['device_id']=='surface'
    assert result['resource_admission']['decision']=='ready'


@pytest.mark.parametrize('report',[None,{}, {'observations':None}, {'observations':[]},
    {'authenticated':True,'observed_at':10**1000,'expires_at':130,'observations':{'available_ram_gib':10**1000}},
    {'authenticated':True,'observed_at':100,'expires_at':130,'observations':{'cpu_percent':float('nan')}}])
def test_unknown_or_malformed_resources_cannot_be_ready(report):
    assert assess(candidate('dell',4).target(),report,now=100)['decision']=='defer'


@pytest.fixture
def controller(tmp_path):
    f=SimpleNamespace(now=100.,owner='c'*64,valid=True,stopped=False,executions=[],recoveries=[],ram=4.,cpu=20.)
    f.target=candidate('dell',4).target()
    f.files=[dict(path='a.py',content='raise RuntimeError("must not execute")\r\n')]
    @contextmanager
    def grant():
        if not f.valid:raise ValueError('Locked')
        yield f.owner
    def admission(req=None):
        return dict(authenticated=True,decision='ready',reasons=[],observed_at=f.now,expires_at=f.now+30,
            observations=dict(available_ram_gib=f.ram,free_disk_gib=10,cpu_percent=f.cpu,plugged_in=True))
    def receipt(req):
        return {**{key:req[key] for key in ('schema_version','job_id','device_id','worker_digest','request_digest')},
            'state':'completed','checks':[dict(path=row['path'],passed=True) for row in req['files']]}
    def execute(req):
        f.executions.append(deepcopy(req))
        return receipt(req)
    def recover(req):
        f.recoveries.append(deepcopy(req))
        result=receipt(req)
        return dict(state='completed',receipt=result,evidence=validate_receipt(req,result))
    f.adapter=SimpleNamespace(target=lambda:deepcopy(f.target),admission=admission,execute=execute,recover=recover)
    f.queue=WaitingStore(tmp_path/'waiting.db',f.owner)
    f.grant=grant;f.receipt=receipt
    def open_controller():
        return ScheduledSyntax(ActionApprovals(clock=lambda:f.now),WaitingStore(f.queue.path,'c'*64),
            adapters=[f.adapter],grant_owner=grant,binding='d'*64,clock=lambda:f.now,cancelled=lambda:f.stopped)
    f.reopen=open_controller;f.controller=open_controller()
    f.prepare=lambda:f.controller.prepare(f.files,'a'*32,session='e'*32)
    f.decide=lambda row,choice='approve':f.controller.decide(session='e'*32,approval_id=row['id'],fingerprint=row['fingerprint'],decision=choice)
    return f


def test_restart_runs_exact_saved_snapshot_once_after_headroom_returns(controller):
    f=controller;f.ram=.1
    review=f.prepare();assert review['review']['resource_admission']['decision']=='defer'
    assert f.decide(review)['state']=='waiting'
    assert f.controller.run_one()['state']=='waiting'
    f.files[0]['content']='different source now';review['payload']['request']['files'][0]['content']='tampered'
    f.controller=f.reopen();f.now+=31;f.ram=4
    result=f.controller.run_one()
    assert result['state']=='completed' and result['result']['evidence']['status']=='passed'
    assert f.executions[0]['files'][0]['content']=='raise RuntimeError("must not execute")\r\n'
    assert f.controller.run_one() is None
    assert f.controller.run_one('a'*32,recover=True)==result
    assert len(f.executions)==1 and not f.recoveries
    assert 'content' not in str(result)


def test_rejection_does_not_store_source_or_execute(controller):
    f=controller
    assert f.decide(f.prepare(),'reject')['state']=='rejected'
    assert not f.queue.path.exists() and not f.executions


@pytest.mark.parametrize('phase',['prepare','approve','run'])
def test_wrong_owner_and_locked_session_never_execute(controller,phase):
    f=controller
    row=None if phase=='prepare' else f.prepare()
    if phase=='run':f.decide(row)
    f.owner='f'*64
    with pytest.raises(ValueError,match='owner'):
        if phase=='prepare':f.prepare()
        elif phase=='approve':f.decide(row)
        else:f.controller.run_one()
    f.owner='c'*64;f.valid=False
    with pytest.raises(ValueError,match='Locked'):
        if phase=='prepare':f.prepare()
        elif phase=='approve':f.decide(row)
        else:f.controller.run_one()
    assert not f.executions


@pytest.mark.parametrize('field,value',[('worker_digest','f'*64),('configuration_digest','f'*64),('account_id','changed')])
def test_changed_destination_requires_new_review_without_execution(controller,field,value):
    f=controller;f.decide(f.prepare());f.target[field]=value
    result=f.controller.run_one()
    assert result['state']=='cancelled' and 'identity changed' in result['reasons'][0]
    assert not f.executions


def test_better_new_destination_never_replaces_approved_one(controller):
    f=controller;f.decide(f.prepare());f.ram=.1
    f.controller.adapters.append(candidate('surface',20))
    assert f.controller.run_one()['state']=='waiting'
    assert f.queue.get('a'*32)['target']['device_id']=='dell' and not f.executions


@pytest.mark.parametrize('change',['expiry','clock_rollback','cancel','policy','stop'])
def test_waiting_expiry_policy_and_stop_are_inert(controller,monkeypatch,change):
    f=controller;f.decide(f.prepare())
    if change=='expiry':f.now=3700
    elif change=='clock_rollback':f.now=99
    elif change=='cancel':f.queue.cancel('a'*32,now=f.now)
    elif change=='policy':monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','workspace.tests')
    else:f.stopped=True
    f.controller.run_one()
    assert f.queue.get('a'*32)['state'] in {'cancelled','expired'} and not f.executions


def test_identity_change_during_resource_probe_cancels_before_claim(controller):
    f=controller;f.decide(f.prepare());probe=f.adapter.admission
    def changed(req):
        f.target['worker_digest']='f'*64
        return probe(req)
    f.adapter.admission=changed
    assert f.controller.run_one()['state']=='cancelled' and not f.executions


def test_cancel_during_probe_wins_before_claim(controller):
    f=controller;f.decide(f.prepare());probe=f.adapter.admission
    def cancelled(req):
        f.queue.cancel(req['job_id'],now=f.now)
        return probe(req)
    f.adapter.admission=cancelled
    assert f.controller.run_one()['state']=='cancelled' and not f.executions


@pytest.mark.parametrize('failure',['crash','bad_receipt','persist'])
def test_uncertain_execution_never_repeats_but_can_recover_exact_receipt(controller,failure):
    f=controller;f.decide(f.prepare())
    if failure=='crash':
        f.queue.claim('a'*32,now=f.now)
    elif failure=='bad_receipt':
        def execute(req):f.executions.append(req);return {'state':'completed'}
        f.adapter.execute=execute;assert f.controller.run_one()['state']=='unconfirmed'
    else:
        def fail(*args,**kwargs):raise OSError('disk unavailable')
        f.controller.queue.finish=fail
        with pytest.raises(OSError):f.controller.run_one()
    count=len(f.executions);f.controller=f.reopen()
    assert f.controller.run_one() is None
    assert f.controller.run_one('a'*32,recover=True)['state']=='completed'
    assert len(f.executions)==count and len(f.recoveries)==1


def test_two_controllers_racing_after_probe_execute_once(controller):
    f=controller;f.decide(f.prepare());other=f.reopen();barrier=threading.Barrier(2);probe=f.adapter.admission
    def concurrent(req):barrier.wait(timeout=5);return probe(req)
    f.adapter.admission=concurrent
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(c.run_one) for c in (f.controller,other)]
        results=[future.result(timeout=10) for future in futures]
    assert len(f.executions)==1 and f.queue.get('a'*32)['state']=='completed'
    assert all(row['state'] in {'completed','unconfirmed'} for row in results)


def test_offline_destination_waits_without_claim(controller):
    f=controller;f.decide(f.prepare());f.target['online']=False
    result=f.controller.run_one()
    assert result['state']=='waiting' and 'offline' in result['reasons'][0] and not f.executions


def test_authorization_recheck_happens_before_persistent_claim(controller):
    f=controller;f.decide(f.prepare())
    def denied(req):raise ValueError('scope denied')
    f.adapter.authorize=denied
    with pytest.raises(ValueError,match='denied'):f.controller.run_one()
    assert f.queue.get('a'*32)['state']=='waiting' and not f.executions


def test_slow_authorization_cannot_use_expired_resource_readings(controller):
    f=controller;f.decide(f.prepare())
    def slow(req):f.now+=31
    f.adapter.authorize=slow
    assert f.controller.run_one()['state']=='waiting' and not f.executions


@pytest.mark.parametrize('change',['time','cancel','policy','identity','expiry'])
def test_authority_change_during_claim_prevents_dispatch_and_replay(controller,monkeypatch,change):
    f=controller;f.decide(f.prepare());claim=f.controller.queue.claim
    def delayed(*args,**kwargs):
        row=claim(*args,**kwargs)
        if change=='time':f.now+=31
        elif change=='cancel':f.stopped=True
        elif change=='policy':monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','workspace.tests')
        elif change=='identity':f.target['worker_digest']='f'*64
        else:f.now=3700
        return row
    f.controller.queue.claim=delayed
    assert f.controller.run_one()['state']=='unconfirmed' and not f.executions
    assert f.reopen().run_one() is None
