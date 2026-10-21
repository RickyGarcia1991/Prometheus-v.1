"""Real Interface adapters preserve native effects independently of audit writes."""
from types import SimpleNamespace
import pytest
from test_runtime_settings_ui import fixture as runtime_fixture, decide as runtime_decide
from test_integration_ui import setup as integration_fixture, request as integration_request
from test_project_tasks_ui import setup as project_fixture, decide as project_decide
from test_remote_jobs_ui import fixture as remote_fixture
from test_scheduled_syntax_ui import fixture as scheduled_fixture
from prometheus_assistant.capability_outcomes import CapabilityOutcomes
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant import integration_ui, project_tasks_ui, scheduled_syntax_ui
from prometheus_assistant.ollama import GenerationCancelled

OWNER='a'*64

def events(app,row):
    receipt=next(r for r in app.action_approvals.history(owner=OWNER) if r['id']==row['id'])
    return receipt['capability_outcomes']

def fail_phase(monkeypatch,phase):
    original=CapabilityOutcomes.record
    def record(self,*args,**kwargs):
        event=kwargs.get('event',args[3] if len(args)>3 else None)
        if event['phase']==phase:raise OSError('fixture audit unavailable')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(CapabilityOutcomes,'record',record)


def test_runtime_settings_native_history_and_restart(runtime_fixture):
    f=runtime_fixture;p=f.run(dict(mode='runtime_config',runtime_backend='cpu'));row=p['details']['approval']
    result=runtime_decide(f,p)
    assert result['details']['runtime_settings']['backend']=='cpu'
    assert [e['status'] for e in events(f.app,row)]==['started','settings_saved']
    old=f.app.action_approvals;f.app.action_approvals=ActionApprovals(history_root=old.decision_history.root)
    assert events(f.app,row)[-1]['status']=='settings_saved'
    assert f.app.action_approvals.peek(owner=OWNER,binding='b'*64) is None

@pytest.mark.parametrize('phase',['started','observed'])
def test_runtime_audit_failure_respects_effect_boundary(runtime_fixture,monkeypatch,phase):
    f=runtime_fixture;p=f.run(dict(mode='runtime_config',runtime_backend='cpu'));fail_phase(monkeypatch,phase)
    if phase=='started':
        with pytest.raises(OSError):runtime_decide(f,p)
        assert f.app._runtime_preferences.snapshot()['backend']=='automatic'
    else:
        result=runtime_decide(f,p)
        assert result['details']['runtime_settings']['backend']=='cpu'
        assert result['details']['decision_outcome']['recorded'] is False


def test_idle_configuration_saved_without_research(project_fixture):
    f=project_fixture;p=f.run(dict(mode='idle_config',enabled=True,interval_minutes=15,prompt='Fixture topic'))
    row=p['details']['approval'];result=project_decide(f,p)
    assert project_tasks_ui.research_store(f.app).status()['settings']['enabled']
    assert [e['status'] for e in events(f.app,row)]==['started','settings_saved']
    assert not f.calls


def test_integration_access_is_grant_not_provider_execution(integration_fixture,tmp_path):
    from prometheus_assistant.scoped_grants import GrantStore
    app,calls,_,_,run=integration_fixture
    app.grants=GrantStore(tmp_path/'grants.sqlite3',host=lambda:'test-host')
    p=run(dict(mode='chat',prompt='Give AgentMemory full access to project home until I revoke it'));row=p['details']['approval']
    result=run(dict(mode='chat',prompt='yes',session=p['session']))
    assert result['details']['task_grant']['state']=='active' and not calls
    assert [e['status'] for e in events(app,row)]==['started','access_granted']

@pytest.mark.parametrize('case',['returned','cancel_after_return','error'])
def test_provider_observation_precedes_response_cancellation(integration_fixture,monkeypatch,case):
    app,calls,_,_,run=integration_fixture;p=run(integration_request());row=p['details']['approval']
    original=integration_ui.provider('agentmemory')
    def execute(*a,**kw):
        calls.append('native')
        if case=='error':raise RuntimeError('fixture provider failed')
        if case=='cancel_after_return':app.cancel_event.set()
        return {'reply':'native response'}
    monkeypatch.setattr(integration_ui,'provider',lambda name:SimpleNamespace(canonical_arguments=original.canonical_arguments,execute=execute))
    if case=='returned':assert run(dict(mode='chat',prompt='yes',session=p['session']))['details']['decision_outcome']['recorded']
    else:
        with pytest.raises(GenerationCancelled if case=='cancel_after_return' else RuntimeError):run(dict(mode='chat',prompt='yes',session=p['session']))
    assert calls==['native']
    assert events(app,row)[-1]['status']==('unknown' if case=='error' else 'adapter_returned')
    assert run(dict(mode='chat',prompt='yes',session=p['session'])) is None

@pytest.mark.parametrize('phase',['started','observed'])
def test_provider_audit_failure_no_retry_or_result_loss(integration_fixture,monkeypatch,phase):
    app,calls,_,_,run=integration_fixture;p=run(integration_request());row=p['details']['approval'];fail_phase(monkeypatch,phase)
    if phase=='started':
        with pytest.raises(OSError):run(dict(mode='chat',prompt='yes',session=p['session']))
        assert not calls
    else:
        result=run(dict(mode='chat',prompt='yes',session=p['session']))
        assert result['details']['integration']['result']['reply']=='Memory operation completed.'
        assert result['details']['decision_outcome']['recorded'] is False and len(calls)==1
    assert run(dict(mode='chat',prompt='yes',session=p['session'])) is None


def test_remote_exact_result_recovery_and_reopen_without_dispatch(remote_fixture):
    f=remote_fixture;row=f.prepare()['details']['approval'];native=f.decide(row)['details']['remote_syntax']
    assert [e['status'] for e in events(f.app,row)]==['started','checks_passed']
    before=events(f.app,row);root=f.app.action_approvals.decision_history.root
    f.app.action_approvals=ActionApprovals(history_root=root)
    for _ in range(2):f.run(dict(mode='remote_syntax_recover',remote_job_id=native['job_id']))
    assert events(f.app,row)==before and f.calls==['sign','dispatch']
    from prometheus_assistant.capability_outcome_hooks import reconcile_worker
    wrong=dict(row['payload']['request'],job_id='f'*32);wrong.pop('request_digest')
    assert reconcile_worker(f.app.action_approvals,OWNER,wrong,native) is None
    assert events(f.app,row)==before


def test_scheduled_wait_resume_restart_terminal_without_rerun(scheduled_fixture):
    f=scheduled_fixture;row=f.prepare();f.local_ram=1
    result=f.decide(row)['details']['scheduled_syntax'];ident=result['job_id']
    assert result['state']=='waiting' and not f.calls
    assert [e['status'] for e in events(f.app,row)]==['started','waiting']
    for _ in range(3):f.status()
    assert len(events(f.app,row))==2
    f.restart();f.local_ram=8;f.now+=31
    result=f.run(dict(mode='scheduled_syntax_tick'))
    assert f.store().get(ident)['state']=='completed' and len(f.calls)==1
    assert events(f.app,row)[-1]['status']=='checks_passed'
    before=events(f.app,row);f.restart();f.status()
    assert events(f.app,row)==before and len(f.calls)==1


def test_legacy_no_history_integration_is_unchanged(integration_fixture):
    app,calls,_,_,run=integration_fixture;app.action_approvals=ActionApprovals()
    p=run(integration_request());result=run(dict(mode='chat',prompt='yes',session=p['session']))
    assert len(calls)==1 and 'decision_outcome' not in result['details']


@pytest.mark.parametrize('phase',['started','observed'])
def test_remote_audit_boundary_preserves_claim_and_result(remote_fixture,monkeypatch,phase):
    f=remote_fixture;row=f.prepare()['details']['approval'];fail_phase(monkeypatch,phase)
    if phase=='started':
        with pytest.raises(OSError):f.decide(row)
        assert f.calls==[]
    else:
        native=f.decide(row)['details']['remote_syntax']
        assert native['state']=='completed' and native['evidence']['status']=='passed'
        assert native['decision_outcome']['recorded'] is False
        assert f.calls==['sign','dispatch']


def test_remote_unconfirmed_receipt_recovery_never_redispatches(remote_fixture):
    f=remote_fixture;row=f.prepare()['details']['approval']
    def interrupted(envelope):
        f.calls.append('dispatch')
        raise OSError('connection lost after submission')
    f.app._remote_worker_client.dispatch=interrupted
    native=f.decide(row)['details']['remote_syntax']
    assert native['state']=='unconfirmed'
    assert events(f.app,row)[-1]['status']=='unconfirmed'
    req=row['payload']['request']
    receipt={**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},'state':'completed','checks':[{'path':r['path'],'passed':True} for r in req['files']]}
    def recover(request,ledger):return ledger.finish(request,receipt)
    f.app._remote_worker_client.recover=recover
    result=f.run(dict(mode='remote_syntax_recover',remote_job_id=req['job_id']))['details']['remote_syntax']
    assert result['state']=='completed' and events(f.app,row)[-1]['status']=='checks_passed'
    assert f.calls==['sign','dispatch']
    f.run(dict(mode='remote_syntax_recover',remote_job_id=req['job_id']))
    assert len(events(f.app,row))==3 and f.calls==['sign','dispatch']


def test_scheduled_start_failure_does_not_enqueue(scheduled_fixture,monkeypatch):
    f=scheduled_fixture;row=f.prepare();fail_phase(monkeypatch,'started')
    result=f.submit(dict(mode='approval_decision',approval_id=row['id'],approval_fingerprint=row['fingerprint'],approval_decision='approve',session=row['session']))
    assert result['state']=='error' and not f.calls
    assert f.store().list()==[]


def test_scheduled_waiting_audit_failure_preserves_approved_queue(scheduled_fixture,monkeypatch):
    f=scheduled_fixture;row=f.prepare();f.local_ram=1;fail_phase(monkeypatch,'observed')
    native=f.decide(row)['details']['scheduled_syntax']
    assert native['state']=='waiting' and native['decision_outcome']['recorded'] is False
    assert f.store().get(native['job_id'])['state']=='waiting' and not f.calls


@pytest.mark.parametrize('phase',['started','observed'])
def test_access_grant_audit_failure_boundary(integration_fixture,tmp_path,monkeypatch,phase):
    from prometheus_assistant.scoped_grants import GrantStore
    app,calls,_,_,run=integration_fixture;app.grants=GrantStore(tmp_path/'grant-boundary.sqlite3',host=lambda:'test-host')
    p=run(dict(mode='chat',prompt='Give AgentMemory full access to project home until I revoke it'));fail_phase(monkeypatch,phase)
    if phase=='started':
        with pytest.raises(OSError):run(dict(mode='chat',prompt='yes',session=p['session']))
        assert not app.grants.inspect_tasks(owner=OWNER)
    else:
        result=run(dict(mode='chat',prompt='yes',session=p['session']))
        assert result['details']['task_grant']['state']=='active'
        assert result['details']['decision_outcome']['recorded'] is False
        assert app.grants.inspect_tasks(owner=OWNER)
    assert not calls

@pytest.mark.parametrize('phase',['started','observed'])
def test_idle_save_audit_failure_boundary(project_fixture,monkeypatch,phase):
    f=project_fixture;p=f.run(dict(mode='idle_config',enabled=True,interval_minutes=15,prompt='Fixture topic'));fail_phase(monkeypatch,phase)
    if phase=='started':
        with pytest.raises(OSError):project_decide(f,p)
        assert not project_tasks_ui.research_store(f.app).status()['settings']['enabled']
    else:
        result=project_decide(f,p)
        assert project_tasks_ui.research_store(f.app).status()['settings']['enabled']
        assert result['details']['decision_outcome']['recorded'] is False
    assert not f.calls

def test_scheduled_status_expiry_reconciles_once_without_dispatch(scheduled_fixture,monkeypatch):
    f=scheduled_fixture;row=f.prepare();f.local_ram=1
    native=f.decide(row)['details']['scheduled_syntax'];job_id=native['job_id']
    assert native['state']=='waiting' and not f.calls
    queued=f.store().get(job_id);f.now=queued['expires_at']+1
    def forbidden(*args,**kwargs):pytest.fail('Expiry/history must not dispatch, recover or enqueue')
    from prometheus_assistant.syntax_waiting_store import WaitingStore
    monkeypatch.setattr(WaitingStore,'enqueue',forbidden)
    f.app._local_syntax_adapter.execute=forbidden
    f.app._local_syntax_adapter.recover=forbidden
    f.app._remote_worker_client.dispatch=forbidden
    f.app._remote_worker_client.recover=forbidden
    status=f.status()
    assert status['jobs'][0]['state']=='expired'
    assert [e['status'] for e in events(f.app,row)]==['started','waiting','expired']
    before=events(f.app,row)
    for _ in range(3):assert f.status()['jobs'][0]['state']=='expired'
    assert events(f.app,row)==before and not f.calls
    # History projection reads receipt stores only; unlike queue status it must
    # not even run expiry/reconciliation hooks.
    monkeypatch.setattr(WaitingStore,'expire',forbidden)
    monkeypatch.setattr(scheduled_syntax_ui,'observed_job',forbidden)
    assert events(f.app,row)==before

