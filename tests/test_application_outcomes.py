"""Generated sidecars only; no Interface, promotion host or process execution."""
from copy import deepcopy
import json
import sqlite3
import pytest

from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.application_outcomes import ApplicationOutcomes, summary, validate
from prometheus_assistant.decision_center import build_snapshot

OWNER, BINDING, SESSION, JOB, CANDIDATE = 'a'*64, 'b'*64, 'c'*32, 'd'*32, 'e'*64


def approvals(root):
    return ActionApprovals(receipt_root=root/'jobs', history_root=root/'history')


def approved(app, *, owner=OWNER, job=JOB, candidate=CANDIDATE, decision='approve'):
    row = app.review(owner=owner,binding=BINDING,session=SESSION,kind='self_improve',
        payload={'job_id':job,'approval_fingerprint':candidate},review={'action':'generated fixture'},policy_revision='test')
    consumed = app.consume(owner=owner,binding=BINDING,session=SESSION,approval_id=row['id'],
        fingerprint=row['fingerprint'],policy_revision='test',decision=decision)
    if decision=='approve':app.record_approved(owner=owner,job_id=job,candidate_fingerprint=candidate)
    return consumed


def observe(app, status, **extra):
    return app.application_outcomes().observe_native(owner=OWNER,job_id=JOB,candidate_fingerprint=CANDIDATE,
        result=dict(job_id=JOB,approval_fingerprint=CANDIDATE,state=status,**extra))


def latest(app):return app.history(owner=OWNER)[0]


def files(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_exact_fresh_start_pending_restart_active_read_only_history(tmp_path,monkeypatch):
    app=approvals(tmp_path);row=approved(app)
    assert app.start_application_outcome(owner=OWNER,row=row)
    assert observe(app,'promotion_pending')
    reopened=approvals(tmp_path)
    assert reopened.peek(owner=OWNER,binding=BINDING) is None
    assert latest(reopened)['evidence_lifecycle']['state']=='pending'
    assert observe(reopened,'active')
    before=files(tmp_path)
    monkeypatch.setattr(ApplicationOutcomes,'observe_native',lambda *a,**k:pytest.fail('History refreshed native result'))
    saved=latest(reopened)
    assert saved['evidence_lifecycle']['state']=='recorded'
    assert saved['evidence_lifecycle']['source']=='application'
    assert saved['evidence_lifecycle']['freshness']=='historical'
    assert saved['evidence_lifecycle']['linked_decisions']==1
    assert saved['capability_outcomes']==saved['outcomes']==[]
    assert [e['status'] for e in saved['application_outcomes']]==['started','promotion_pending','active']
    assert reopened.history(owner='f'*64)==[] and files(tmp_path)==before


@pytest.mark.parametrize('states,expected',[
    ([],('unconfirmed','outcome_unconfirmed')),
    (['failed'],('recorded','native_result_recorded')),
    (['failed','rolled_back'],('restored','application_restored')),
    (['rolled_back'],('restored','application_restored')),
    (['failed','recovery_required'],('unconfirmed','restoration_unconfirmed')),
    (['active','failed'],('conflicting','native_identity_conflict')),
    (['rolled_back','active'],('conflicting','native_identity_conflict')),
    (['rolled_back','recovery_required'],('conflicting','native_identity_conflict')),
    (['active','promotion_pending'],('recorded','native_result_recorded')),
    (['active','new_future_phase'],('recorded','native_result_recorded')),
])
def test_historical_summary_preserves_original_facts_and_does_not_choose_winner(tmp_path,states,expected):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app))
    for state in states:observe(app,state)
    row=latest(approvals(tmp_path));projection=row['evidence_lifecycle']
    assert (projection['state'],projection['reason'])==expected
    assert [e['status'] for e in row['application_outcomes']]==['started']+[s if s!='new_future_phase' else 'unconfirmed' for s in states]
    snap=build_snapshot(decisions=[row],jobs=[],activity=[],resources={'mode':'Balanced'},now=100)
    entries=[i for i in snap['items'] if i['source']=='decisions']
    assert snap['sources']['decisions']['state']=='available'
    assert not any(i['state']=='verified' for i in entries)
    if expected[0]=='conflicting':assert any(i['state']=='application_conflicting' and i['severity']=='critical' for i in entries)
    if states==['failed']:assert any(i['state']=='application_failed' and i['severity']=='warning' for i in entries)
    if expected[0]=='restored':
        assert any(i['state']=='application_restored' for i in entries)
        assert not any('manual restoration' in i['label'].lower() for i in entries)
    if states==['failed','recovery_required']:assert any(i['state']=='application_recovery_required' and i['severity']=='warning' for i in entries)


def test_duplicate_refresh_and_concurrent_read_are_idempotent(tmp_path):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app))
    assert observe(app,'active',error='PRIVATE ERROR',release='PRIVATE PATH')
    before=files(tmp_path)
    assert observe(approvals(tmp_path),'active') is False
    assert files(tmp_path)==before
    assert 'PRIVATE' not in json.dumps(latest(app))


def test_legacy_missing_link_never_backfilled_and_cannot_start_after_restart(tmp_path):
    app=approvals(tmp_path);row=approved(app)
    reopened=approvals(tmp_path)
    assert observe(reopened,'active') is False
    assert latest(reopened)['evidence_lifecycle']['state']=='missing'
    with pytest.raises(ValueError,match='Fresh consumed'):reopened.start_application_outcome(owner=OWNER,row=row)
    assert not reopened.application_outcomes().path.exists()


@pytest.mark.parametrize('change',[{'fingerprint':'f'*64},{'kind':'project_edit'},{'session':'f'*32},{'payload':{'job_id':'f'*32,'approval_fingerprint':CANDIDATE}}])
def test_changed_consumed_row_cannot_create_link(tmp_path,change):
    app=approvals(tmp_path);row=approved(app)
    with pytest.raises(ValueError):app.start_application_outcome(owner=OWNER,row={**row,**change})
    assert not app.application_outcomes().path.exists()


def test_start_requires_saved_exact_approval_and_receipt_and_is_not_replay_authority(tmp_path):
    app=approvals(tmp_path);row=approved(app)
    with pytest.raises(ValueError):app.application_outcomes().start(owner=OWNER,row=row,approved_job={'owner':OWNER})
    assert app.start_application_outcome(owner=OWNER,row=row)
    with pytest.raises(ValueError,match='Fresh consumed'):app.start_application_outcome(owner=OWNER,row=row)
    with pytest.raises(ValueError):app.consume(owner=OWNER,binding=BINDING,session=SESSION,approval_id=row['id'],fingerprint=row['fingerprint'],policy_revision='test',decision='approve')
    assert latest(app)['evidence_lifecycle']['state']=='unconfirmed'


def test_rejected_decision_and_wrong_owner_cannot_start(tmp_path):
    app=approvals(tmp_path);row=approved(app,decision='reject')
    with pytest.raises(ValueError):app.start_application_outcome(owner=OWNER,row=row)
    row=approved(app)
    with pytest.raises(ValueError):app.start_application_outcome(owner='f'*64,row=row)
    assert app.start_application_outcome(owner=OWNER,row=row)
    assert app.application_outcomes().observe_native(owner='f'*64,job_id=JOB,candidate_fingerprint=CANDIDATE,
        result={'job_id':JOB,'approval_fingerprint':CANDIDATE,'state':'active'}) is False


@pytest.mark.parametrize('patch',[{'job_id':'f'*32},{'approval_fingerprint':'f'*64},{'job_id':None}])
def test_native_identity_mismatch_refused_without_overwriting_evidence(tmp_path,patch):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app))
    before=files(tmp_path)
    result={'job_id':JOB,'approval_fingerprint':CANDIDATE,'state':'active',**patch}
    with pytest.raises(ValueError):app.application_outcomes().observe_native(owner=OWNER,job_id=JOB,candidate_fingerprint=CANDIDATE,result=result)
    assert files(tmp_path)==before


@pytest.mark.parametrize('result',[{'job_id':JOB,'state':'active'},{'job_id':JOB,'approval_fingerprint':CANDIDATE,'state':'new_state'},{'job_id':JOB,'approval_fingerprint':CANDIDATE,'state':[]}])
def test_missing_candidate_or_unknown_state_is_unconfirmed(tmp_path,result):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app))
    assert app.application_outcomes().observe_native(owner=OWNER,job_id=JOB,candidate_fingerprint=CANDIDATE,result=result)
    assert latest(app)['evidence_lifecycle']['state']=='unconfirmed'
    assert latest(app)['application_outcomes'][-1]['evidence_sha256'] is None


def test_ambiguous_job_cannot_acquire_second_decision_or_candidate(tmp_path):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app))
    row=approved(app)
    with pytest.raises(ValueError,match='ambiguous'):app.start_application_outcome(owner=OWNER,row=row)
    rows=app.history(owner=OWNER)
    assert len(rows)==2 and sum(bool(r['application_outcomes']) for r in rows)==1


def test_malformed_stored_observation_fails_closed(tmp_path):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app))
    with sqlite3.connect(app.application_outcomes().path) as db:
        db.execute("UPDATE outcomes SET payload='{}'")
    with pytest.raises(ValueError):latest(app)


def test_native_digest_and_sequence_are_validated(tmp_path):
    app=approvals(tmp_path);app.start_application_outcome(owner=OWNER,row=approved(app));observe(app,'active')
    events=latest(app)['application_outcomes']
    bad=deepcopy(events[-1]);bad['status']='failed'
    with pytest.raises(ValueError):validate(bad)
    with pytest.raises(ValueError):summary([events[-1]])
    with pytest.raises(ValueError):summary(events+[events[-1]])


def test_legacy_stores_unchanged_when_recording_application_observations(tmp_path):
    app=approvals(tmp_path);row=approved(app)
    original=app.decision_history.path.read_bytes()
    app.start_application_outcome(owner=OWNER,row=row);observe(app,'active')
    assert app.decision_history.path.read_bytes()==original
    assert app.decision_history.history(owner=OWNER)[0]['id']==row['id']
    assert not app.capability_outcomes().path.exists()


def test_failed_start_is_consumed_and_never_recreated_by_read(tmp_path,monkeypatch):
    app=approvals(tmp_path);row=approved(app)
    monkeypatch.setattr(ApplicationOutcomes,'start',lambda *a,**k:(_ for _ in ()).throw(ValueError('generated disk failure')))
    with pytest.raises(ValueError,match='disk'):app.start_application_outcome(owner=OWNER,row=row)
    with pytest.raises(ValueError,match='Fresh consumed'):app.start_application_outcome(owner=OWNER,row=row)
    assert latest(app)['application_outcomes']==[]
