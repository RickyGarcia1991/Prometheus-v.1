"""Historical facts remain visible without becoming current proof or authority."""
import builtins
import copy
import hashlib
import json
import sqlite3
import pytest

from prometheus_assistant import evidence_lifecycle as mod
from prometheus_assistant.shared_capability_evidence import for_owner as shared

OWNER = 'a' * 64
OTHER = 'b' * 64
PROJECT = 'c' * 64
EDIT = 'd' * 32
JOB = 'e' * 32
REQUEST = 'f' * 64
COUNTS = dict(planned=1, executed=1, passed=1, failed=0, cancelled=0, timed_out=0)


def receipt(n=1, *, kind='remote_syntax', owner=OWNER, decision='approve'):
    return dict(schema_version=1, id=f'{n:032x}', owner=owner, fingerprint=f'{n:064x}',
                session='1' * 32, kind=kind, decision=decision,
                feedback='Change it' if decision == 'request_changes' else '', decided_at=100 + n)


def link(row, event):
    return row['owner'], row['id'], row['fingerprint'], event


def worker(row, *, status='checks_passed', job=JOB, request=REQUEST, digest='2' * 64):
    start = dict(event_id=f'{int(row["id"],16)*100:032x}', phase='started', observed_at=1,
                 status='started', job_id=job, request_digest=request, checks=None, evidence_sha256=None)
    events = [link(row, start)]
    if status == 'started':
        return events
    counts = dict(COUNTS, passed=0, failed=1) if status == 'checks_failed' else dict(COUNTS) if status == 'checks_passed' else None
    result = dict(start, event_id=f'{int(row["id"],16)*100+1:032x}', phase='observed',
                  observed_at=2, status=status, checks=counts,
                  evidence_sha256=None if status == 'unknown' else digest)
    return events + [link(row, result)]


def project(row, *, status='passed', project_hash=PROJECT, edit=EDIT,
            rollback='not_attempted', undo=None, digest='2' * 64):
    start = dict(event_id=f'{int(row["id"],16)*100:032x}', phase='started', observed_at=1,
                 project_hash=project_hash, status='started', edit_id='', checks=None,
                 rollback='unknown', evidence_sha256=None)
    events = [link(row, start)]
    if status == 'started':
        return events
    counts = dict(COUNTS, passed=0, failed=1) if status == 'failed' else dict(COUNTS)
    if status == 'unknown':
        edit, digest, counts, rollback = '', None, None, 'unknown'
    result = dict(start, event_id=f'{int(row["id"],16)*100+1:032x}', phase='result',
                  observed_at=2, status=status, edit_id=edit, checks=counts,
                  rollback=rollback, evidence_sha256=digest)
    events.append(link(row, result))
    if undo:
        undo_start = dict(start, event_id=f'{int(row["id"],16)*100+2:032x}',
                          phase='undo_started', edit_id=edit, observed_at=3)
        events.append(link(row, undo_start))
        if undo != 'started':
            events.append(link(row, dict(undo_start, event_id=f'{int(row["id"],16)*100+3:032x}',
                phase='undo_result', observed_at=4, status=undo, evidence_sha256='3' * 64,
                rollback='restored' if undo == 'restored' else 'needs_attention')))
    return events


def run(rows, projects=None, workers=None, owner=OWNER):
    return mod.for_owner(owner, decisions=rows, project_events=projects or [], capability_events=workers or [])


def item(result, row):
    return result[row['id'], row['fingerprint']]


@pytest.mark.parametrize('kind,source', [('project_edit','project'),('project_task','project'),
    ('remote_syntax','worker'),('scheduled_syntax','worker'),('runtime_config','capability'),
    ('idle_config','capability'),('integration','capability'),('integration_access','capability'),('other','none')])
def test_missing_start_never_confirms_execution(kind, source):
    row = receipt(kind=kind)
    value = item(run([row]), row)
    assert value == dict(schema_version=1, state='missing', source=source, freshness='historical',
        reference=None, reason='no_recorded_start', linked_decisions=0, related_decisions=[], truncated=False, read_only=True)


@pytest.mark.parametrize('decision', ['reject', 'request_changes'])
def test_nonapproval_never_authorizes_or_inherits_evidence(decision):
    row = receipt(decision=decision)
    value = item(run([row]), row)
    assert (value['state'], value['source'], value['reason']) == ('not_applicable','none','decision_not_approved')
    with pytest.raises(ValueError, match='approved'): run([row], workers=worker(row))


@pytest.mark.parametrize('status,state,reason', [('started','pending','execution_pending'),
    ('waiting','pending','execution_pending'),('deferred','pending','execution_pending'),
    ('unknown','unconfirmed','outcome_unconfirmed'),('unconfirmed','unconfirmed','outcome_unconfirmed'),
    ('checks_passed','recorded','native_result_recorded'),('checks_failed','recorded','native_result_recorded'),
    ('tool_failed','recorded','native_result_recorded'),('cancelled','recorded','native_result_recorded'),
    ('expired','recorded','native_result_recorded')])
def test_worker_states_are_historical_not_success_judgments(status, state, reason):
    row = receipt(); events = worker(row,status=status); before = copy.deepcopy(events)
    value = item(run([row],workers=events),row)
    assert (value['state'],value['reason'],value['source']) == (state,reason,'worker')
    assert value['reference'] and value['linked_decisions'] == 1 and value['related_decisions'] == []
    assert value['freshness'] == 'historical' and events == before


@pytest.mark.parametrize('kind,status', [('runtime_config','settings_saved'),('idle_config','settings_saved'),
    ('integration','adapter_returned'),('integration_access','access_granted')])
def test_immediate_effect_is_not_shared_or_current_verification(kind,status):
    rows=[receipt(n,kind=kind) for n in (1,2)]
    events=sum((worker(row,status=status,job='',request='') for row in rows),[])
    values=run(rows,workers=events)
    assert all(v['state']=='recorded' and v['source']=='capability' and v['reference'] is None for v in values.values())
    assert shared(OWNER,events)=={}


@pytest.mark.parametrize('undo,state,reason', [(None,'recorded','native_result_recorded'),
    ('started','unconfirmed','restoration_pending'),('restored','restored','project_restored'),
    ('restore_failed','unconfirmed','restoration_unconfirmed')])
def test_project_manual_restoration_is_local_and_preserves_original_checks(undo,state,reason):
    a,b=receipt(kind='project_edit'),receipt(2,kind='project_task')
    events=project(a,undo=undo)+project(b);before=copy.deepcopy(events)
    values=run([a,b],projects=events)
    assert (item(values,a)['state'],item(values,a)['reason'])==(state,reason)
    assert item(values,b)['state']=='recorded'
    assert item(values,a)['reference']==item(values,b)['reference']
    groups=shared(OWNER,[],project_events=events)
    assert all(v[0]['kind']=='project_result' and v[0]['status']=='passed' for v in groups.values())
    assert events==before and events[1][3]['checks']['passed']==1


@pytest.mark.parametrize('rollback,state,reason', [('restored','recorded','native_result_recorded'),
    ('needs_attention','unconfirmed','restoration_unconfirmed')])
def test_automatic_rollback_does_not_rename_failed_native_result(rollback,state,reason):
    row=receipt(kind='project_edit');events=project(row,status='failed',rollback=rollback)
    value=item(run([row],projects=events),row)
    assert (value['state'],value['reason'])==(state,reason)
    assert events[1][3]['status']=='failed' and events[1][3]['checks']['failed']==1


@pytest.mark.parametrize('project_kind', [False,True])
@pytest.mark.parametrize('difference', ['status','digest'])
def test_conflict_locality_no_winner_and_unrelated_records_remain(project_kind,difference):
    rows=[receipt(n,kind='project_edit' if project_kind else 'remote_syntax') for n in (1,2,3)]
    make=project if project_kind else worker
    args={'status':'failed' if project_kind else 'checks_failed'} if difference=='status' else {'digest':'4'*64}
    events=make(rows[0])+make(rows[1],**args)+make(rows[2],**({'edit':'5'*32} if project_kind else {'job':'5'*32}))
    before=copy.deepcopy(events)
    values=run(rows,projects=events if project_kind else [],workers=[] if project_kind else events)
    for row in rows[:2]:
        value=item(values,row)
        assert value['state']=='conflicting' and value['reason']=='native_identity_conflict'
        assert value['linked_decisions']==2 and value['related_decisions'][0]['id']!=row['id']
    assert item(values,rows[0])['reference']==item(values,rows[1])['reference']
    assert item(values,rows[2])['state']=='recorded' and events==before
    assert shared(OWNER,[] if project_kind else events,project_events=events if project_kind else [])=={}


def test_worker_pending_member_does_not_adopt_the_shared_terminal_result():
    rows=[receipt(n) for n in (1,2,3)]
    events=worker(rows[0])+worker(rows[1])+worker(rows[2],status='waiting')
    values=run(rows,workers=events)
    assert item(values,rows[2])['state']=='pending'
    assert all(v['linked_decisions']==3 for v in values.values())
    groups=shared(OWNER,events)
    assert set(groups)=={(r['id'],r['fingerprint']) for r in rows[:2]}
    assert all(v[0]['linked_decisions']==2 for v in groups.values())


def test_pending_member_reports_family_conflict_without_claiming_own_result():
    rows=[receipt(n) for n in (1,2,3)]
    events=worker(rows[0])+worker(rows[1],status='checks_failed')+worker(rows[2],status='waiting')
    before=copy.deepcopy(events)
    values=run(rows,workers=events)
    assert all(v['state']=='conflicting' and v['linked_decisions']==3 for v in values.values())
    assert shared(OWNER,events)=={} and events==before
    assert events[-1][3]['status']=='waiting' and events[-1][3]['checks'] is None


def test_different_owner_job_request_project_and_empty_edit_never_join():
    a,b,c,d=[receipt(n) for n in range(1,5)]
    foreign=receipt(5,owner=OTHER)
    events=worker(a)+worker(b,job='5'*32)+worker(c,request='5'*64)+worker(d,job='5'*32,request='5'*64)+worker(foreign,status='checks_failed')
    values=run([a,b,c,d,foreign],workers=events)
    assert len({v['reference'] for v in values.values()})==4
    assert all(v['linked_decisions']==1 for v in values.values())
    assert foreign['id'] not in str(values) and OTHER not in str(values)
    rows=[receipt(n,kind='project_edit') for n in range(6,10)]
    pe=project(rows[0])+project(rows[1],project_hash='6'*64)+project(rows[2],edit='')+project(rows[3],edit='')
    values=run(rows,projects=pe)
    assert item(values,rows[0])['reference']!=item(values,rows[1])['reference']
    assert all(item(values,r)['reference'] is None for r in rows[2:])
    assert shared(OWNER,[],project_events=pe)=={}


def test_project_worker_namespaces_never_join_even_with_equal_identifiers_and_digest():
    p,w=receipt(kind='project_edit'),receipt(2)
    values=run([p,w],projects=project(p,project_hash=REQUEST,edit=JOB),workers=worker(w))
    assert item(values,p)['reference']!=item(values,w)['reference']
    assert all(v['linked_decisions']==1 for v in values.values())


def test_full_collection_detects_conflict_outside_latest_fifty_and_bounds_refs():
    rows=[receipt(n) for n in range(1,56)]
    events=sum((worker(row,status='checks_failed' if row is rows[0] else 'checks_passed') for row in rows),[])
    values=run(rows,workers=events)
    visible=[item(values,row) for row in rows[-50:]]
    assert all(v['state']=='conflicting' and v['linked_decisions']==55 and len(v['related_decisions'])==16 and v['truncated'] for v in visible)
    assert all(row['id'] not in [r['id'] for r in item(values,row)['related_decisions']] for row in rows)
    assert shared(OWNER,events)=={}


def test_other_owner_data_filtered_before_facts_and_no_io_or_mutation(monkeypatch):
    row=receipt();decisions=[row,{'owner':OTHER,'private':'do not validate or disclose'}]
    events=worker(row)+[(OTHER,'wrong','wrong',{'private':'not mine'})]
    before=copy.deepcopy((decisions,events))
    def deny(*a,**kw):raise AssertionError('projection attempted I/O')
    with monkeypatch.context() as m:
        m.setattr(builtins,'open',deny);m.setattr(sqlite3,'connect',deny)
        values=run(decisions,workers=events)
    assert (decisions,events)==before and len(values)==1 and 'private' not in str(values)


def test_observation_timestamps_do_not_create_expiry_or_choose_conflict_winner():
    a,b=receipt(),receipt(2);events=worker(a)+worker(b,status='checks_failed')
    expected=run([a,b],workers=events)
    events[0][3]['observed_at']=253402300799;events[1][3]['observed_at']=0
    assert run([a,b],workers=events)==expected


@pytest.mark.parametrize('fault', ['missing_start','counts','duplicate_event','fingerprint','unapproved','mixed_kind','capacity','duplicate_receipt'])
def test_malformed_records_fail_closed_instead_of_becoming_conflicts(fault,monkeypatch):
    row=receipt();rows=[row];events=worker(row)
    if fault=='missing_start':events=events[1:]
    elif fault=='counts':events[-1][3]['checks']['passed']=0
    elif fault=='duplicate_event':events[-1][3]['event_id']=events[0][3]['event_id']
    elif fault=='fingerprint':events[-1]=(OWNER,row['id'],'9'*64,events[-1][3])
    elif fault=='unapproved':rows=[]
    elif fault=='mixed_kind':row['kind']='project_edit'
    elif fault=='capacity':monkeypatch.setattr(mod,'CAPABILITY_MAX',1)
    elif fault=='duplicate_receipt':rows=[row,copy.deepcopy(row)]
    with pytest.raises(ValueError):run(rows,workers=events)


@pytest.mark.parametrize('patch', [dict(schema_version=True),dict(state='current'),dict(freshness='fresh'),
    dict(read_only=False),dict(source='none'),dict(reason='project_restored'),dict(linked_decisions=True),
    dict(reference=None),dict(truncated=True),dict(related_decisions=[{'id':'x','fingerprint':'y'}]),
    dict(state='conflicting',reason='native_identity_conflict'),dict(extra='private')])
def test_projection_validator_rejects_false_claims(patch):
    row=receipt();value=item(run([row],workers=worker(row)),row)
    with pytest.raises(ValueError):mod.validate_projection(dict(value,**patch))


def test_consistent_worker_provenance_reference_remains_stable():
    rows=[receipt(n) for n in (1,2)];events=sum((worker(r) for r in rows),[])
    facts=json.dumps({k:events[1][3][k] for k in ('status','checks','evidence_sha256')},sort_keys=True,separators=(',',':'))
    expected=hashlib.sha256(json.dumps(['owner-worker-evidence-v1',OWNER,JOB,REQUEST,facts],separators=(',',':')).encode()).hexdigest()
    assert all(v[0]['reference']==expected for v in shared(OWNER,events).values())
    assert all(v['reference']==expected for v in run(rows,workers=events).values())
