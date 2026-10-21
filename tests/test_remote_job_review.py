from contextlib import contextmanager
import copy
import json
from types import SimpleNamespace

import pytest

from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.remote_job_protocol import normalize_request
from prometheus_assistant.remote_job_review import RemoteJobReview
from prometheus_assistant.remote_job_store import JobStore


def request():
    return dict(schema_version=1,job_id='a'*32,device_id='surface',worker_digest='b'*64,
        operation='python-syntax-check',timeout_seconds=5,files=[dict(path='a.py',content='x=1\r\n')])


@pytest.fixture
def setup(tmp_path):
    f=SimpleNamespace(owner='c'*64,valid=True,active=False,calls=[],now=100)
    f.target=dict(account_id='account',device_id='surface',device_name='Surface',worker_digest='b'*64,online=True)
    f.approvals=ActionApprovals(clock=lambda:f.now)
    f.ledger=JobStore(tmp_path/'jobs.db')
    @contextmanager
    def grant():
        if not f.valid: raise ValueError('Expired owner')
        f.active=True
        try: yield f.owner
        finally: f.active=False
    def target():
        assert f.active; f.calls.append('target'); return copy.deepcopy(f.target)
    def signer(data):
        assert f.active; f.calls.append('sign'); f.signed=json.loads(data); return b'x'*256
    def dispatch(envelope):
        assert f.active; f.calls.append('dispatch')
        req=envelope['payload']['request']
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
                'state':'completed','checks':[dict(path='a.py',passed=True)]}
    f.controller=RemoteJobReview(f.approvals,f.ledger,grant_owner=grant,binding='d'*64,
                                target=target,signer=signer,dispatch=dispatch,clock=lambda:f.now,
                                admission=lambda req:dict(decision='ready',reasons=[],authenticated=True,observed_at=100,expires_at=130))
    f.prepare=lambda: f.controller.prepare(request(),session='e'*32)
    def decide(row,decision='approve',**changes):
        args=dict(session='e'*32,approval_id=row['id'],fingerprint=row['fingerprint'],decision=decision)
        return f.controller.decide(**{**args,**changes})
    f.decide=decide
    return f


def test_prepare_detaches_exact_review_and_denial_inert(setup):
    f=setup; row=f.prepare()
    assert f.calls==['target','target'] and not f.ledger.path.exists()
    assert row['review']['files'][0]['content']=='x=1\r\n'
    f.controller.target=lambda:pytest.fail('Denial inspected target')
    result=f.decide(row,'reject')
    assert result['state']=='rejected' and f.calls==['target','target'] and not f.ledger.path.exists()


def test_exact_source_signing_and_terminal_replay(setup):
    f=setup; row=f.prepare(); row['payload']['request']['files'][0]['content']='tampered display copy'
    first=f.decide(row)
    assert first['evidence']['status']=='passed'
    assert f.signed['request']==normalize_request(request())
    assert f.signed['expires_at']-f.signed['issued_at']==300
    second=f.decide(f.prepare())
    assert second['replayed'] and second['receipt']==first['receipt']
    assert f.calls.count('dispatch')==f.calls.count('sign')==1


@pytest.mark.parametrize('failure',['session','fingerprint','owner','expired','grant','policy'])
def test_invalid_approval_cannot_claim_or_sign(setup,monkeypatch,failure):
    f=setup; row=f.prepare(); changes={}
    if failure=='session': changes['session']='f'*32
    elif failure=='fingerprint': changes['fingerprint']='f'*64
    elif failure=='owner': f.owner='f'*64
    elif failure=='expired': f.now=1000
    elif failure=='grant': f.valid=False
    else: monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','remote.dispatch')
    with pytest.raises(ValueError): f.decide(row,**changes)
    assert f.calls==['target','target'] and not f.ledger.path.exists()


@pytest.mark.parametrize('field,value',[('device_id','other'),('worker_digest','f'*64),
    ('account_id','other'),('online',False)])
def test_target_changes_invalidate_consumed_review(setup,field,value):
    f=setup; row=f.prepare(); f.target[field]=value
    with pytest.raises(ValueError): f.decide(row)
    assert 'sign' not in f.calls and not f.ledger.path.exists()
    with pytest.raises(ValueError): f.decide(row)


@pytest.mark.parametrize('failure',['sign','dispatch','receipt','persist'])
def test_post_claim_uncertainty_cannot_retry(setup,failure):
    f=setup; row=f.prepare()
    def fail(*_): raise OSError('private internal error')
    if failure=='sign': f.controller.signer=fail
    elif failure=='dispatch': f.controller.dispatch=fail
    elif failure=='receipt': f.controller.dispatch=lambda _:dict(state='completed')
    else: f.ledger.finish=fail
    result=f.decide(row)
    assert result['state']=='unconfirmed' and 'private internal' not in str(result)
    f.controller.signer=lambda _:pytest.fail('Uncertain job was signed again')
    again=f.decide(f.prepare())
    assert again['state']=='unconfirmed' and again['replayed']


def test_other_pending_kind_not_consumed(setup):
    f=setup
    row=f.approvals.review(owner=f.owner,binding='d'*64,session='e'*32,kind='project_edit',
                           payload={},review={},policy_revision='old')
    with pytest.raises(ValueError): f.decide(row)
    assert f.approvals.pending(owner=f.owner,binding='d'*64)['id']==row['id']


def test_scoped_policy_denial_before_claim(setup,monkeypatch):
    from prometheus_assistant.application_policy import scope_digest
    f=setup
    scope=dict(account='account',device='surface',tool='prometheus.syntax',
               request_digest=normalize_request(request())['request_digest'])
    monkeypatch.setenv('PROMETHEUS_DENY_SCOPES','remote.dispatch:'+scope_digest(scope))
    row=f.prepare()
    with pytest.raises(ValueError): f.decide(row)
    assert not f.ledger.path.exists() and 'sign' not in f.calls


def test_pending_conflict_and_preparation_target_mismatch_are_inert(setup):
    f=setup; row=f.prepare()
    with pytest.raises(ValueError): f.prepare()
    assert f.approvals.pending(owner=f.owner,binding='d'*64)['id']==row['id']
    f.decide(row,'reject'); f.target['device_id']='other'
    with pytest.raises(ValueError): f.prepare()
    assert not f.ledger.path.exists() and 'sign' not in f.calls


def test_failed_syntax_persists_without_green_evidence(setup):
    f=setup; real=f.controller.dispatch
    def failure(envelope):
        result=real(envelope); result['checks'][0]['passed']=False; return result
    f.controller.dispatch=failure
    result=f.decide(f.prepare())
    assert result['state']=='completed' and result['evidence']['status']=='failed'
    assert f.ledger.lookup(request())['receipt']==result['receipt']


def test_changed_receipt_digest_remains_unconfirmed(setup):
    f=setup; real=f.controller.dispatch
    def wrong(envelope):
        result=real(envelope); result['request_digest']='f'*64; return result
    f.controller.dispatch=wrong
    assert f.decide(f.prepare())['state']=='unconfirmed'
    assert f.ledger.lookup(request())['state']=='unconfirmed'


def test_resource_change_after_review_defers_without_claim(setup):
    f=setup
    f.controller.admission=lambda req:dict(decision='ready',reasons=[],authenticated=True,observed_at=100,expires_at=130)
    row=f.prepare()
    assert row['review']['resource_admission']['decision']=='ready'
    f.controller.admission=lambda req:dict(decision='defer',reasons=['Surface switched to battery power.'],authenticated=True,observed_at=100,expires_at=130)
    result=f.decide(row)
    assert result['state']=='deferred'
    assert result['reasons']==['Surface switched to battery power.']
    assert result['approval_resolved']==row['id']
    assert not f.ledger.path.exists() and 'sign' not in f.calls and 'dispatch' not in f.calls


def test_resource_unavailable_before_review_has_no_approval_or_claim(setup):
    f=setup
    def unavailable(req): raise ValueError('Untrusted advertisement')
    f.controller.admission=unavailable
    with pytest.raises(ValueError): f.prepare()
    assert f.approvals.pending(owner=f.owner,binding='d'*64) is None
    assert not f.ledger.path.exists() and 'sign' not in f.calls


def test_resource_query_failure_after_review_has_no_claim(setup):
    f=setup
    f.controller.admission=lambda req:dict(decision='ready',reasons=[],authenticated=True,observed_at=100,expires_at=130)
    row=f.prepare()
    def unavailable(req): raise OSError('Private provider details')
    f.controller.admission=unavailable
    result=f.decide(row)
    assert result['state']=='deferred' and 'Private provider' not in str(result)
    assert not f.ledger.path.exists() and 'sign' not in f.calls


def test_observation_expires_while_reacquiring_owner_before_claim(setup):
    f=setup;row=f.prepare();original=f.controller.grant_owner;count=0
    @contextmanager
    def delayed_owner():
        nonlocal count
        count+=1
        if count==2:f.now=131
        with original() as owner:yield owner
    f.controller.grant_owner=delayed_owner
    result=f.decide(row)
    assert result['state']=='deferred' and 'expired' in result['reasons'][0]
    assert not f.ledger.path.exists() and 'sign' not in f.calls
