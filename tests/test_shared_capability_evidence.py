"""Shared provenance is a bounded projection, never authority or hash adoption."""
import copy
import pytest

from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.capability_outcomes import CapabilityOutcome
from prometheus_assistant.shared_capability_evidence import for_owner, MAX_RELATED

O='a'*64; B='b'*64; S='c'*32; JOB='d'*32; REQUEST='e'*64
COUNTS=dict(planned=1,executed=1,passed=1,failed=0,cancelled=0,timed_out=0)


def approved(app, *, owner=O, kind='remote_syntax', marker='private original'):
    row=app.review(owner=owner,binding=B,session=S,kind=kind,payload={'private':marker},review={},policy_revision='p')
    app.consume(owner=owner,binding=B,session=S,approval_id=row['id'],fingerprint=row['fingerprint'],
                policy_revision='p',decision='approve')
    return row


def complete(app, *, owner=O, job=JOB, request=REQUEST, status='checks_passed', marker='private original'):
    row=approved(app,owner=owner,marker=marker)
    outcome=CapabilityOutcome(app,owner,row,job_id=job,request_digest=request)
    outcome.start()
    if status=='checks_passed':outcome.observe(status,{'confirmed':True,**COUNTS},COUNTS)
    elif status=='checks_failed':
        counts=dict(COUNTS,passed=0,failed=1);outcome.observe(status,{'confirmed':True,**counts},counts)
    elif status=='tool_failed':outcome.observe(status,{'confirmed':True,'tool_failed':True})
    else:outcome.observe(status,{'confirmed':False})
    return row


@pytest.mark.parametrize('status',['checks_passed','checks_failed','tool_failed'])
def test_same_owner_exact_execution_is_shared_readonly(tmp_path,status):
    app=ActionApprovals(history_root=tmp_path)
    a=complete(app,status=status);b=complete(app,status=status,marker='private second')
    before={path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    history={row['id']:row for row in app.history(owner=O)}
    first=history[a['id']]['shared_evidence'][0];second=history[b['id']]['shared_evidence'][0]
    assert first['reference']==second['reference']
    assert first==dict(reference=first['reference'],kind='worker_result',status=status,linked_decisions=2,
                       related_decisions=[dict(id=b['id'],fingerprint=b['fingerprint'])],truncated=False,read_only=True)
    assert 'private' not in str(first) and O not in str(first) and JOB not in str(first) and REQUEST not in str(first)
    assert {path:path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}==before
    reopened=ActionApprovals(history_root=tmp_path)
    assert {row['id']:row['shared_evidence'] for row in reopened.history(owner=O)}=={
        key:row['shared_evidence'] for key,row in history.items()}
    assert reopened.peek(owner=O,binding=B) is None


@pytest.mark.parametrize('change',[{'owner':'f'*64},{'job':'f'*32},{'request':'f'*64}])
def test_equal_evidence_digest_cannot_cross_execution_or_owner(tmp_path,change):
    app=ActionApprovals(history_root=tmp_path)
    complete(app);complete(app,**change)
    events=app.capability_outcomes().read()
    assert for_owner(O,events)=={}
    assert all(row['shared_evidence']==[] for row in app.history(owner=O))


def test_other_owner_neither_counts_nor_references_are_disclosed(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    mine=[complete(app,marker=str(n)) for n in range(2)]
    other=[complete(app,owner='f'*64,marker=str(n)) for n in range(3)]
    history=app.history(owner=O)
    assert all(row['shared_evidence'][0]['linked_decisions']==2 for row in history)
    assert all(row['id'] not in str(history) for row in other)
    theirs=app.history(owner='f'*64)
    assert all(row['shared_evidence'][0]['linked_decisions']==3 for row in theirs)
    assert history[0]['shared_evidence'][0]['reference']!=theirs[0]['shared_evidence'][0]['reference']


def test_pending_and_unknown_observations_do_not_claim_shared_result(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    for _ in range(2):
        row=approved(app);outcome=CapabilityOutcome(app,O,row,job_id=JOB,request_digest=REQUEST)
        outcome.start();outcome.observe('waiting',{'waiting':True});outcome.observe('unconfirmed',{'confirmed':False})
    assert for_owner(O,app.capability_outcomes().read())=={}


def test_settings_common_digest_is_not_a_shared_worker_execution(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    for _ in range(2):
        row=approved(app,kind='runtime_config');outcome=CapabilityOutcome(app,O,row)
        outcome.start();outcome.observe('settings_saved',{'saved':True})
    assert all(row['shared_evidence']==[] for row in app.history(owner=O))


def test_contradictory_exact_native_result_is_visible_failure(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    complete(app);complete(app,status='checks_failed')
    with pytest.raises(ValueError,match='contradictory'):app.history(owner=O)


def test_related_refs_bounded_without_hiding_total(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    for n in range(MAX_RELATED+3):complete(app,marker=str(n))
    rows=app.history(owner=O)
    for row in rows:
        shared=row['shared_evidence'][0]
        assert shared['linked_decisions']==MAX_RELATED+3 and shared['truncated'] is True
        assert len(shared['related_decisions'])==MAX_RELATED
        assert all(ref['id']!=row['id'] for ref in shared['related_decisions'])


def test_projection_rejects_missing_start_malformed_counts_and_missing_binding(tmp_path):
    app=ActionApprovals(history_root=tmp_path);complete(app)
    events=app.capability_outcomes().read()
    with pytest.raises(ValueError):for_owner(O,events[1:])
    for field,value in [('checks',dict(COUNTS,passed=0)),('request_digest','wrong')]:
        changed=copy.deepcopy(events);changed[-1][3][field]=value
        with pytest.raises(ValueError):for_owner(O,changed)


def test_revision_link_does_not_adopt_parent_or_related_evidence(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    complete(app);complete(app,marker='private second')
    parent=app.review(owner=O,binding=B,session=S,kind='remote_syntax',payload={},review={},policy_revision='p',capability_subject='9'*64)
    app.consume(owner=O,binding=B,session=S,approval_id=parent['id'],fingerprint=parent['fingerprint'],
                policy_revision='p',decision='request_changes',feedback='Different files')
    context=app.capability_revision_context(owner=O,binding=B,session=S,kind='remote_syntax',subject_hash='9'*64,parent_row=parent)
    child=app.review(owner=O,binding=B,session=S,kind='remote_syntax',payload={},review={},policy_revision='p',
                     capability_subject='9'*64,capability_revision_token=context['capability_revision_token'])
    app.consume(owner=O,binding=B,session=S,approval_id=child['id'],fingerprint=child['fingerprint'],policy_revision='p',decision='approve')
    history={row['id']:row for row in app.history(owner=O)}
    assert history[parent['id']]['shared_evidence']==history[child['id']]['shared_evidence']==[]
    assert history[parent['id']]['capability_outcomes']==history[child['id']]['capability_outcomes']==[]
