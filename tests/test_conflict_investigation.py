"""Conflicting history must expose actionable facts without rewriting them."""
import copy
import json
import pytest
from prometheus_assistant.action_approvals import ActionApprovals
from test_shared_capability_evidence import complete, approved, O, B
from test_evidence_lifecycle import receipt, worker, project, OWNER
from test_application_outcomes import approvals as app_approvals, approved as app_approved, observe


def files(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_history_exposes_conflicting_facts_beyond_page_and_survives_reopen(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    first=complete(app)
    for n in range(51):approved(app,kind='runtime_config',marker=str(n))
    second=complete(app,status='checks_failed')
    before=files(tmp_path)
    row=app.history(owner=O,limit=1)[0]
    detail=row['evidence_conflict']
    assert detail['state']=='unresolved' and detail['read_only']
    assert detail['requires_new_review'] and detail['suggested_action']=='review_worker_check'
    assert detail['observation_count']==detail['variant_count']==2
    assert {v['status'] for v in detail['variants']}=={'checks_passed','checks_failed'}
    assert {w['id'] for v in detail['variants'] for w in v['witnesses']}=={first['id'],second['id']}
    assert row['capability_outcomes'][-1]['status']=='checks_failed'
    reopened=ActionApprovals(history_root=tmp_path)
    assert reopened.history(owner=O,limit=1)[0]==row and files(tmp_path)==before
    assert reopened.peek(owner=O,binding=B) is None


def projection(rows,projects=None,workers=None,owner=OWNER):
    from prometheus_assistant.evidence_conflicts import for_owner
    return for_owner(owner,decisions=rows,project_events=projects or [],capability_events=workers or [])


def test_snapshot_is_owner_and_family_scoped_and_tracks_later_restoration():
    a,b=receipt(kind='project_edit'),receipt(2,kind='project_task')
    events=project(a,status='failed')+project(b)
    before=copy.deepcopy(events)
    initial=projection([a,b],projects=events)[a['id'],a['fingerprint']]
    assert initial['suggested_action']=='review_project_check'
    assert projection([b,a],projects=project(b)+project(a,status='failed'))[a['id'],a['fingerprint']]==initial
    other=receipt(3,owner='b'*64)
    assert projection([a,b,other],projects=events,workers=worker(other))[a['id'],a['fingerprint']]==initial
    unrelated=receipt(4)
    assert projection([a,b,unrelated],projects=events,workers=worker(unrelated,job='9'*32))[a['id'],a['fingerprint']]==initial
    after=projection([a,b],projects=project(a,status='failed',undo='restored')+project(b))[a['id'],a['fingerprint']]
    assert after['snapshot_sha256']!=initial['snapshot_sha256']
    assert after['variants']==initial['variants'] and events==before


def test_application_witnesses_are_incompatible_not_failure_then_rollback(tmp_path):
    app=app_approvals(tmp_path);row=app_approved(app)
    app.start_application_outcome(owner=O,row=row)
    observe(app,'failed');observe(app,'rolled_back')
    assert app.history(owner=O)[0]['evidence_conflict'] is None
    observe(app,'active')
    detail=app.history(owner=O)[0]['evidence_conflict']
    assert detail['source']=='application' and detail['suggested_action']=='inspect_application_recovery'
    pair={v['status'] for v in detail['variants'][:2]}
    assert 'active' in pair and pair & {'failed','rolled_back','recovery_required'}
    assert detail['variant_count']==3 and detail['state']=='unresolved'


def test_truncation_keeps_incompatible_witnesses_and_binds_omitted_inputs():
    rows=[receipt(n) for n in range(1,25)]
    events=sum((worker(r,status='checks_failed' if n==23 else 'checks_passed',digest=f'{n+1:064x}') for n,r in enumerate(rows)),[])
    value=projection(rows,workers=events)[rows[0]['id'],rows[0]['fingerprint']]
    assert value['variant_count']==24 and value['variants_truncated']
    assert len(value['variants'])==8 and value['observation_count']==24
    assert value['variants'][0]['fact_sha256']!=value['variants'][1]['fact_sha256']
    changed=copy.deepcopy(events);changed[-1][3]['evidence_sha256']='9'*64
    updated=projection(rows,workers=changed)[rows[0]['id'],rows[0]['fingerprint']]
    assert updated['snapshot_sha256']!=value['snapshot_sha256']


def test_variant_witnesses_are_bounded_and_include_pending_family_in_snapshot():
    rows=[receipt(n) for n in range(1,12)]
    events=sum((worker(r,status='checks_failed' if n==10 else 'checks_passed') for n,r in enumerate(rows)),[])
    value=projection(rows,workers=events)[rows[0]['id'],rows[0]['fingerprint']]
    common=next(v for v in value['variants'] if v['status']=='checks_passed')
    assert common['observation_count']==10 and len(common['witnesses'])==4 and common['witnesses_truncated']
    pending=receipt(12)
    updated=projection(rows+[pending],workers=events+worker(pending,status='started'))[rows[0]['id'],rows[0]['fingerprint']]
    assert updated['snapshot_sha256']!=value['snapshot_sha256'] and updated['variants']==value['variants']


def test_ordinary_results_are_not_conflicts_and_foreign_fields_not_exposed(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    complete(app);complete(app,job='9'*32,status='checks_failed');complete(app,owner='f'*64)
    assert all(r['evidence_conflict'] is None for r in app.history(owner=O))
    complete(app,status='checks_failed')
    details=[r['evidence_conflict'] for r in app.history(owner=O) if r['evidence_conflict']]
    assert details and 'private original' not in json.dumps(details) and O not in json.dumps(details)


def test_malformed_native_input_is_not_relabelled_a_resolvable_conflict():
    a,b=receipt(),receipt(2)
    events=worker(a)+worker(b,status='checks_failed');events[-1][3]['request_digest']='8'*64
    with pytest.raises(ValueError):projection([a,b],workers=events)
