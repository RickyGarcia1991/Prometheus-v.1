from prometheus_assistant.decision_center import build_snapshot
import copy
import pytest

def snap(rows):
    return build_snapshot(decisions=rows,jobs=[],activity=[],resources={'mode':'Balanced'},now=1000)

def test_old_approval_unknown_and_denial_no_execution():
    result=snap([{'decision':'approve','kind':'runtime_config'}, {'decision':'reject','kind':'integration'}])
    assert {r['state'] for r in result['items'] if r['source']=='decisions'}=={'unknown','no_execution'}

def test_missing_and_malformed_decision_source_visible():
    assert snap(None)['sources']['decisions']['state']=='unavailable'
    result=snap([{'decision':'approve','kind':'runtime_config','capability_outcomes':'bad'}])
    assert result['sources']['decisions']['state']=='partial'
    assert any(r['source']=='decisions' and r['state']=='unknown' for r in result['items'])

def test_bounded_decision_summary_never_leaks_payload():
    row={'decision':'approve','kind':'runtime_config','feedback':'PRIVATE'}
    result=snap([row]*51)
    assert result['sources']['decisions']['truncated']
    assert 'PRIVATE' not in str(result)
    assert sum(r['count'] for r in result['items'] if r['source']=='decisions' and r['state']=='unknown')==50

def project_event(phase,status,**kw):
    return dict(event_id=({'started':'1','result':'2','undo_started':'3','undo_result':'4'}[phase])*32,phase=phase,status=status,observed_at=100,project_hash='a'*64,edit_id=kw.pop('edit_id',''),checks=kw.pop('checks',None),rollback=kw.pop('rollback','unknown'),evidence_sha256=kw.pop('evidence_sha256',None),**kw)

def test_failed_then_manual_restore_is_resolved_but_interrupted_undo_unknown():
    events=[project_event('started','started'),project_event('result','failed',edit_id='b'*32,rollback='restored',evidence_sha256='c'*64)]
    row={'decision':'approve','kind':'project_edit','outcomes':events}
    assert any(i['state']=='historical_failure' for i in snap([row])['items'])
    events.append(project_event('undo_started','started',edit_id='b'*32))
    assert any(i['state']=='restoration_unknown' for i in snap([row])['items'])
    events.append(project_event('undo_result','restored',edit_id='b'*32,rollback='restored',evidence_sha256='c'*64))
    assert any(i['state']=='restored' and i['severity']=='info' for i in snap([row])['items'])
    assert not any(i['state']=='historical_failure' for i in snap([row])['items'])

def capability(status,**kw):
    return dict(event_id=('1' if status=='started' else '2')*32,phase='started' if status=='started' else 'observed',observed_at=100,status=status,job_id='a'*32,request_digest='b'*64,checks=kw.get('checks'),evidence_sha256=None if status in {'started','unknown'} else 'c'*64)

def test_native_capability_truth_and_contradictory_pass():
    checks=dict(planned=1,executed=1,passed=1,failed=0,cancelled=0,timed_out=0)
    row=dict(decision='approve',kind='scheduled_syntax',capability_outcomes=[capability('started'),capability('checks_passed',checks=checks)])
    assert any(i['state']=='verified' for i in snap([row])['items'])
    checks['failed']=1
    result=snap([row]);assert result['sources']['decisions']['state']=='partial'
    assert any(i['state']=='unknown' for i in result['items'])


def test_every_capability_disposition_is_historical_not_operational():
    for status,expected in [('waiting','waiting'),('unknown','unknown'),('unconfirmed','unknown'),('tool_failed','historical_failure'),('cancelled','no_execution'),('expired','no_execution')]:
        row=dict(decision='approve',kind='scheduled_syntax',capability_outcomes=[capability('started'),capability(status)])
        result=snap([row])
        assert any(i['source']=='decisions' and i['kind']=='historical_evidence' and i['state']==expected for i in result['items'])

def test_configuration_and_access_observations_do_not_claim_behavior():
    for kind,status in [('runtime_config','settings_saved'),('idle_config','settings_saved'),('integration_access','access_granted'),('integration','adapter_returned')]:
        events=[capability('started'),capability(status)]
        for event in events:event.update(job_id='',request_digest='')
        result=snap([dict(decision='approve',kind=kind,capability_outcomes=events)])
        assert any(i['state']=='recorded_effect' and i['severity']=='info' for i in result['items'])
        assert not any(i['state']=='verified' for i in result['items'])


def lifecycle(state='conflicting', source='worker', reason='native_identity_conflict'):
    return dict(schema_version=1,state=state,source=source,freshness='historical',
        reference='d'*64,reason=reason,linked_decisions=2,
        related_decisions=[dict(id='e'*32,fingerprint='f'*64)],truncated=False,read_only=True)


def passing_worker():
    counts=dict(planned=1,executed=1,passed=1,failed=0,cancelled=0,timed_out=0)
    return dict(decision='approve',kind='remote_syntax',capability_outcomes=[
        capability('started'),capability('checks_passed',checks=counts)])


def test_conflict_overrides_individual_pass_but_preserves_unrelated_pass_and_raw_data():
    conflict=passing_worker();conflict['evidence_lifecycle']=lifecycle()
    rows=[conflict,passing_worker()];before=copy.deepcopy(rows)
    result=snap(rows)
    items=[r for r in result['items'] if r['source']=='decisions']
    assert {(r['state'],r['severity'],r['count']) for r in items}=={
        ('conflicting','warning',1),('verified','info',1)}
    assert result['sources']['decisions']['state']=='available' and rows==before
    assert 'd'*64 not in str(result) and 'e'*32 not in str(result)


@pytest.mark.parametrize('change',[
    {'state':'made_up'}, {'read_only':False}, {'freshness':'current'},
    {'linked_decisions':True}, {'source':'capability'}, {'reason':'private text'},
    {'extra':'private source'}, {'reference':None},
])
def test_invalid_lifecycle_cannot_leave_a_green_summary(change):
    row=passing_worker();row['evidence_lifecycle']={**lifecycle(),**change}
    result=snap([row])
    assert result['sources']['decisions']['state']=='partial'
    assert not any(r['state']=='verified' for r in result['items'])
    assert any(r['state']=='unknown' for r in result['items'])
    assert 'private' not in str(result)


def test_conflict_projection_does_not_bypass_native_validation():
    row=passing_worker();row['evidence_lifecycle']=lifecycle()
    row['capability_outcomes'][-1]['checks']['failed']=1
    result=snap([row])
    assert result['sources']['decisions']['state']=='partial'
    assert not any(r['state'] in {'conflicting','verified'} for r in result['items'])


def test_project_undo_cannot_resolve_conflicting_original_results():
    events=[project_event('started','started'),project_event('result','failed',edit_id='b'*32,
        rollback='restored',evidence_sha256='c'*64),project_event('undo_started','started',edit_id='b'*32),
        project_event('undo_result','restored',edit_id='b'*32,rollback='restored',evidence_sha256='c'*64)]
    row=dict(decision='approve',kind='project_edit',outcomes=events,evidence_lifecycle=lifecycle(source='project'))
    result=snap([row])
    assert any(r['state']=='conflicting' and r['severity']=='warning' for r in result['items'])
    assert not any(r['state']=='restored' for r in result['items'])


def test_approved_without_events_cannot_adopt_a_related_conflict():
    row=dict(decision='approve',kind='remote_syntax',evidence_lifecycle=lifecycle())
    result=snap([row]);assert result['sources']['decisions']['state']=='partial'
    assert not any(r['state']=='conflicting' for r in result['items'])


def test_waiting_observer_can_show_conflicting_family_without_claiming_completion():
    row=dict(decision='approve',kind='remote_syntax',capability_outcomes=[capability('started'),capability('waiting')],
        evidence_lifecycle=lifecycle())
    result=snap([row])
    assert result['sources']['decisions']['state']=='available'
    assert any(r['state']=='conflicting' for r in result['items'])
    assert not any(r['state']=='verified' for r in result['items'])


def test_deferred_lifecycle_does_not_claim_execution():
    row=dict(decision='approve',kind='remote_syntax',capability_outcomes=[capability('started'),capability('deferred')],
        evidence_lifecycle=lifecycle(state='pending',reason='execution_pending'))
    result=snap([row])
    assert result['sources']['decisions']['state']=='available'
    assert any(r['state']=='no_execution' for r in result['items'])
