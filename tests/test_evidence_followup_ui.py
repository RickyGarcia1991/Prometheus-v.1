"""Authenticated follow-up requests bind exact history and explicit saved files."""
from copy import deepcopy
import pytest
from prometheus_assistant import ui_server as ui
from test_scheduled_syntax_ui import fixture


def request():
    return dict(mode='evidence_followup_prepare', project='C:/generated-project',
        remote_files='a.py', followup_interpretation_id='1'*32,
        followup_interpretation_fingerprint='2'*64, followup_state_sha256='3'*64,
        followup_snapshot_sha256='4'*64, followup_fact_sha256='5'*64,
        followup_reason='Check the saved code associated with this interpretation.',
        followup_operation='python-syntax-check', followup_placement='remote-lightweight-v1')


def test_exact_followup_request_is_a_supported_authenticated_mode():
    value=ui.validate_job(request())
    assert value['mode']=='evidence_followup_prepare'
    assert value['remote_files']=='a.py'
    assert value['followup_state_sha256']=='3'*64


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(followup_interpretation_id='x'),
    lambda r:r.update(followup_snapshot_sha256='x'),
    lambda r:r.update(followup_operation='arbitrary-shell'),
    lambda r:r.update(followup_placement='anywhere'),
    lambda r:r.update(remote_files='\n'.join('a.py' for _ in range(9))),
    lambda r:r.update(followup_reason=''),
    lambda r:r.update(followup_reason='x'*2001),
    lambda r:r.update(command='run-this'),
    lambda r:r.update(prompt='act from this text'),
    lambda r:r.update(followup_job_id='9'*32),
    lambda r:r.pop('followup_fact_sha256'),
])
def test_followup_request_rejects_ambiguous_or_unreviewed_fields(mutation):
    value=deepcopy(request());mutation(value)
    with pytest.raises(ValueError):ui.validate_job(value)


@pytest.mark.parametrize('mode',['evidence_followup_cancel','evidence_followup_recover'])
def test_followup_saved_job_controls_require_only_exact_job(mode):
    assert ui.validate_job(dict(mode=mode,followup_job_id='a'*32))['followup_job_id']=='a'*32
    for patch in ({'followup_job_id':'bad'},{'remote_files':'a.py'},{'project':'C:/elsewhere'},
                  {'followup_reason':'new reason'},{'followup_operation':'python-syntax-check'}):
        with pytest.raises(ValueError):ui.validate_job(dict(mode=mode,followup_job_id='a'*32)|patch)


def test_followup_fields_cannot_bleed_into_another_mode():
    for mode in ('chat','scheduled_syntax_prepare','interpretation_change_prepare'):
        with pytest.raises(ValueError):ui.validate_job(request()|{'mode':mode,'prompt':'hello'})
    with pytest.raises(ValueError):ui.validate_job(dict(mode='evidence_followup_tick',followup_job_id='a'*32))


def seed(f, source='worker'):
    from test_evidence_resolution import family, prepare, decide
    original=family(f.app.action_approvals, source)
    row=prepare(f.app.action_approvals, original)
    decide(f.app.action_approvals, row)
    root=f.app.action_approvals._interpretation_change_inputs(f.owner,row['id'],row['fingerprint'])[0]
    body=request() | dict(project=str(f.project),remote_files='hello.py',
        followup_interpretation_id=root['interpretation_id'],
        followup_interpretation_fingerprint=root['interpretation_fingerprint'],
        followup_state_sha256=root['state_sha256'],
        followup_snapshot_sha256=root['effective_payload']['snapshot_sha256'],
        followup_fact_sha256=root['effective_payload']['fact_sha256'])
    return body, original


@pytest.mark.parametrize('source',['worker','project','application'])
def test_followup_action_keeps_original_facts_and_has_distinct_result(fixture,source):
    f=fixture;body,original=seed(f,source)
    before=f.app.action_approvals.history(owner=f.owner)
    row=f.run(body)['details']['approval']
    assert row['kind']=='evidence_followup' and f.calls==[]
    result=f.decide(row)['details']['evidence_followup']
    assert result['state']=='completed'
    assert len([c for c in f.calls if c[0]=='surface'])==1
    after=f.app.action_approvals.history(owner=f.owner)
    current=next(r for r in after if r['id']==original['id'])
    prior=next(r for r in before if r['id']==original['id'])
    for field in ('outcomes','capability_outcomes','application_outcomes','evidence_conflict','interpretation_lifecycle'):
        assert current[field]==prior[field]
    from prometheus_assistant import evidence_followup_ui as adapter
    saved=adapter.status(f.app,grant_owner=f.grant,binding=f.binding)
    assert saved['read_only'] and any(j['job_id']==result['job_id'] for j in saved['jobs'])
    assert (f.project/'hello.py').read_bytes()==b'answer = 42\n'


def test_wait_restart_rechecks_saved_files_and_never_uses_ordinary_queue(fixture):
    f=fixture;body,_=seed(f)
    row=f.run(body)['details']['approval'];f.surface_ram=.1
    job=f.decide(row)['details']['evidence_followup'];assert job['state']=='waiting' and not f.calls
    assert f.store().list()==[]
    f.restart();f.surface_ram=4;f.now+=31
    (f.project/'hello.py').write_text('changed = 3\n',encoding='utf-8')
    f.run(dict(mode='evidence_followup_tick'))
    from prometheus_assistant import evidence_followup_ui as adapter
    saved=adapter.status(f.app,grant_owner=f.grant,binding=f.binding)['jobs']
    assert next(j for j in saved if j['job_id']==job['job_id'])['state']=='cancelled'
    assert not f.calls


def test_followup_owner_session_policy_and_approval_must_still_match(fixture,monkeypatch):
    f=fixture;body,_=seed(f);row=f.run(body)['details']['approval']
    for patch in ({'approval_fingerprint':'e'*64},{'session':'e'*32},{'approval_decision':'request_changes','approval_feedback':'replace'}):
        result=f.submit(dict(mode='approval_decision',session=row['session'],approval_id=row['id'],
            approval_fingerprint=row['fingerprint'],approval_decision='approve') | patch)
        assert result['state']=='error' and not f.calls
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','ui.evidence_followup_prepare')
    result=f.submit(dict(mode='approval_decision',session=row['session'],approval_id=row['id'],
        approval_fingerprint=row['fingerprint'],approval_decision='approve'))
    assert result['state']=='error' and not f.calls


def test_idle_turns_alternate_ordinary_and_followup_queues(monkeypatch):
    from prometheus_assistant import evidence_followup_ui as adapter
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(ui.remote_jobs_ui.scheduled_syntax_ui,'idle',lambda app:calls.append('ordinary') or True)
    monkeypatch.setattr(adapter,'idle',lambda app:calls.append('followup') or True)
    app=SimpleNamespace()
    for _ in range(4):ui.Interface.idle_maintenance(app)
    assert calls==['ordinary','followup','ordinary','followup']


@pytest.mark.parametrize('result',[
    {'state':'failed'},
    {'state':'completed','result':{'evidence':{'status':'failed'}}},
    {'state':'completed','result':{'evidence':{'status':'incomplete'}}},
])
def test_failed_followup_does_not_display_completed_activity(result):
    from prometheus_assistant.ui_activity import outcome
    state,label=outcome('approval_decision',{'state':'done','result':{'details':{'evidence_followup':result}}})
    assert state=='error' and 'passed' not in label.lower()
