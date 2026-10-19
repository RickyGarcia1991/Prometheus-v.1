"""Owner interpretations never rewrite observations or restore execution authority."""
import copy
import json
from pathlib import Path
import sqlite3
import uuid
import pytest
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.evidence_resolution import EvidenceResolutions, canonical, proposal
from test_shared_capability_evidence import complete, O, B, S


def files(root):return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def target(app):
    first=complete(app);complete(app,status='checks_failed')
    return next(row for row in app.history(owner=O) if row['id']==first['id'])


def prepare(app,row,**change):
    conflict=row['evidence_conflict']
    args=dict(owner=O,binding=B,session=S,target_id=row['id'],target_fingerprint=row['fingerprint'],
        snapshot_sha256=conflict['snapshot_sha256'],fact_sha256=conflict['variants'][0]['fact_sha256'],
        reason='Prefer this observation for the investigation; retain the contradiction.',policy_revision='p')
    return app.prepare_evidence_resolution(**(args|change))


def decide(app,pending,decision='approve',**change):
    args=dict(owner=O,binding=B,session=S,approval_id=pending['id'],fingerprint=pending['fingerprint'],
              decision=decision,policy_revision='p')
    return app.decide_evidence_resolution(**(args|change))


def test_owner_interpretation_retains_conflict_and_original_evidence(tmp_path):
    app=ActionApprovals(history_root=tmp_path);original=target(app);before=files(tmp_path)
    pending=prepare(app,original)
    assert pending['kind']=='evidence_resolution' and files(tmp_path)==before
    assert all(pending['review'][key]==value for key,value in pending['payload'].items())
    result=decide(app,pending)
    assert result['approval']['id']==pending['id']
    assert result['resolution']['decision']=='approve'
    assert result['resolution']['recording_state']=='recorded'
    assert result['resolution']['currency']=='matching_inputs'
    saved=next(row for row in app.history(owner=O) if row['id']==original['id'])
    assert saved['evidence_conflict']==original['evidence_conflict']
    assert saved['capability_outcomes']==original['capability_outcomes']
    assert saved['evidence_lifecycle']==original['evidence_lifecycle']
    assert saved['evidence_resolutions']['items']==[result['resolution']]


def family(app,source):
    if source=='worker':return target(app)
    if source=='application':
        from test_application_outcomes import approved, observe
        row=approved(app);app.start_application_outcome(owner=O,row=row)
        observe(app,'active');observe(app,'failed')
    else:
        from test_shared_capability_evidence import approved, COUNTS
        for status in ('passed','failed'):
            row=approved(app,kind='project_edit',marker=status)
            event=dict(event_id=uuid.uuid4().hex,phase='started',observed_at=1,project_hash='3'*64,
                status='started',edit_id='',checks=None,rollback='unknown',evidence_sha256=None)
            app.record_outcome(owner=O,approval_id=row['id'],fingerprint=row['fingerprint'],event=event)
            event.update(event_id=uuid.uuid4().hex,phase='result',observed_at=2,status=status,edit_id='4'*32,
                         checks=COUNTS if status=='passed' else dict(COUNTS,passed=0,failed=1),rollback='not_attempted',evidence_sha256='5'*64)
            app.record_outcome(owner=O,approval_id=row['id'],fingerprint=row['fingerprint'],event=event)
    return next(saved for saved in app.history(owner=O) if saved['id']==row['id'])


def make_app(tmp_path,**kwargs):return ActionApprovals(history_root=tmp_path/'history',receipt_root=tmp_path/'jobs',**kwargs)


@pytest.mark.parametrize('source',['worker','project','application'])
@pytest.mark.parametrize('decision',['approve','reject'])
def test_namespaces_decisions_restart_and_no_native_changes(tmp_path,source,decision):
    app=make_app(tmp_path);row=family(app,source);before=files(tmp_path)
    pending=prepare(app,row);assert files(tmp_path)==before
    result=decide(app,pending,decision)
    assert result['resolution']['source']==source and result['resolution']['decision']==decision
    assert not result['resolution']['interpretation_conflict']
    saved=app.history(owner=O);before_read=files(tmp_path);reopened=make_app(tmp_path)
    assert reopened.history(owner=O)==saved and files(tmp_path)==before_read
    assert reopened.peek(owner=O,binding=B) is None and not reopened._application_starts
    for name,raw in before.items():
        if name.endswith('decisions.sqlite3'):continue
        assert files(tmp_path)[name]==raw
    current=next(r for r in saved if r['id']==row['id'])
    for key in ('outcomes','capability_outcomes','application_outcomes','evidence_lifecycle','evidence_conflict'):
        assert current[key]==row[key]
    receipt=next(r for r in saved if r['id']==pending['id'])
    assert receipt['kind']=='other' and receipt['evidence_resolutions']['items']==[result['resolution']]
    assert receipt['outcomes']==receipt['capability_outcomes']==receipt['application_outcomes']==[]
    with pytest.raises(ValueError):decide(reopened,pending,decision)


@pytest.mark.parametrize('change',[{'owner':'f'*64},{'target_id':'f'*32},{'target_fingerprint':'f'*64},
    {'snapshot_sha256':'f'*64},{'fact_sha256':'f'*64},{'target_id':'bad'},{'session':'bad'},
    {'snapshot_sha256':None},{'reason':''},{'reason':'  '},{'reason':'x'*2001},{'reason':'bad\x00'},
    {'reason':'bad\x1b'},{'policy_revision':''},{'policy_revision':'x'*257}])
def test_prepare_invalid_or_foreign_inputs_write_nothing(tmp_path,change):
    app=make_app(tmp_path);row=target(app);before=files(tmp_path)
    with pytest.raises(ValueError):prepare(app,row,**change)
    assert files(tmp_path)==before and app.peek(owner=O,binding=B) is None


@pytest.mark.parametrize('change',[{'owner':'f'*64},{'binding':'f'*64},{'session':'f'*32},
    {'fingerprint':'f'*64},{'approval_id':'f'*32},{'decision':'request_changes'}])
def test_decide_exact_session_identity_and_supported_decision_required(tmp_path,change):
    app=make_app(tmp_path);pending=prepare(app,target(app));before=files(tmp_path)
    with pytest.raises(ValueError):decide(app,pending,**change)
    assert files(tmp_path)==before and app.peek(owner=O,binding=B)['id']==pending['id']


@pytest.mark.parametrize('decision',['approve','reject'])
def test_policy_change_revokes_pending_without_audit_or_consumption(tmp_path,decision):
    app=make_app(tmp_path);pending=prepare(app,target(app));before=files(tmp_path)
    with pytest.raises(ValueError,match='policy changed'):decide(app,pending,decision,policy_revision='new')
    assert files(tmp_path)==before and app.peek(owner=O,binding=B) is None


def test_stale_approval_refused_but_exact_rejection_can_be_recorded(tmp_path):
    app=make_app(tmp_path);row=target(app);pending=prepare(app,row)
    # Native work can finish independently of the browser's pending proposal.
    second=ActionApprovals(history_root=tmp_path/'history')
    complete(second,marker='later observation')
    before=files(tmp_path)
    with pytest.raises(ValueError,match='changed'):decide(app,pending)
    assert files(tmp_path)==before
    result=decide(app,pending,'reject')
    assert result['resolution']['currency']=='stale' and result['resolution']['decision']=='reject'


def test_later_evidence_marks_recorded_interpretation_stale_without_changing_it(tmp_path):
    app=make_app(tmp_path);row=target(app);saved=decide(app,prepare(app,row))['resolution']
    raw=files(tmp_path);complete(app,marker='later observation')
    for name,data in raw.items():
        if 'EvidenceResolutions' in name:assert files(tmp_path)[name]==data
    current=next(r for r in app.history(owner=O) if r['id']==row['id'])['evidence_resolutions']['items'][0]
    assert current==saved|{'currency':'stale'}


def test_pending_restart_revoke_and_expiry_do_not_create_audit(tmp_path):
    clock=[1];app=make_app(tmp_path,clock=lambda:clock[0],lifetime=1);row=target(app)
    pending=prepare(app,row);before=files(tmp_path)
    with pytest.raises(ValueError):decide(make_app(tmp_path),pending)
    clock[0]=3
    with pytest.raises(ValueError):decide(app,pending)
    assert files(tmp_path)==before
    pending=prepare(app,row);app.revoke_binding(B)
    with pytest.raises(ValueError):decide(app,pending)
    assert files(tmp_path)==before


def test_failure_after_consume_is_durable_unrecorded_and_never_replayed(tmp_path,monkeypatch):
    app=make_app(tmp_path);row=target(app);pending=prepare(app,row)
    def fail(*args):raise OSError('generated disk fault')
    monkeypatch.setattr(EvidenceResolutions,'finish',fail)
    with pytest.raises(ValueError,match='completion is unconfirmed'):decide(app,pending)
    assert app.peek(owner=O,binding=B) is None
    saved=app.history(owner=O);item=next(r for r in saved if r['id']==row['id'])['evidence_resolutions']['items'][0]
    assert item['decision']=='approve' and item['recording_state']=='unrecorded' and not item['interpretation_conflict']
    assert make_app(tmp_path).history(owner=O)==saved
    with pytest.raises(ValueError):decide(app,pending)
    assert app.history(owner=O)==saved


def test_failure_before_consume_retains_unrecorded_attempt_and_retry_is_exact(tmp_path,monkeypatch):
    app=make_app(tmp_path);row=target(app);pending=prepare(app,row);original=app.decision_history.record
    def fail(*args):raise ValueError('generated canonical history fault')
    monkeypatch.setattr(app.decision_history,'record',fail)
    with pytest.raises(ValueError,match='canonical'):decide(app,pending)
    history=app.history(owner=O);item=next(r for r in history if r['id']==row['id'])['evidence_resolutions']['items'][0]
    assert item['decision']==item['recording_state']=='unrecorded'
    assert app.peek(owner=O,binding=B)['id']==pending['id']
    assert make_app(tmp_path).peek(owner=O,binding=B) is None
    proposal_bytes=app.evidence_resolutions().read()[0][0]
    monkeypatch.setattr(app.decision_history,'record',original)
    assert decide(app,pending)['resolution']['recording_state']=='recorded'
    assert app.evidence_resolutions().read()[0][0]==proposal_bytes


def test_link_failure_prevents_consume(tmp_path,monkeypatch):
    app=make_app(tmp_path);pending=prepare(app,target(app));before=files(tmp_path)
    def fail(*args):raise ValueError('generated link failure')
    monkeypatch.setattr(EvidenceResolutions,'link',fail)
    with pytest.raises(ValueError,match='link failure'):decide(app,pending)
    assert files(tmp_path)==before and app.peek(owner=O,binding=B)['id']==pending['id']


def test_inputs_changed_during_link_rechecked_before_consumption(tmp_path,monkeypatch):
    app=make_app(tmp_path);row=target(app);pending=prepare(app,row);original=EvidenceResolutions.link
    def link_then_change(store,value):
        result=original(store,value)
        complete(ActionApprovals(history_root=tmp_path/'history'),marker='concurrent observation')
        return result
    monkeypatch.setattr(EvidenceResolutions,'link',link_then_change)
    with pytest.raises(ValueError,match='changed'):decide(app,pending)
    saved=app.history(owner=O)
    assert pending['id'] not in {r['id'] for r in saved}
    item=next(r for r in saved if r['id']==row['id'])['evidence_resolutions']['items'][0]
    assert item['recording_state']=='unrecorded' and item['currency']=='stale'


def test_same_snapshot_contradictory_approvals_are_all_explicit(tmp_path):
    app=make_app(tmp_path);row=target(app);first=decide(app,prepare(app,row))['resolution']
    different=row['evidence_conflict']['variants'][1]['fact_sha256']
    second=decide(app,prepare(app,row,fact_sha256=different,reason='Alternative owner interpretation.'))['resolution']
    items=next(r for r in app.history(owner=O) if r['id']==row['id'])['evidence_resolutions']['items']
    assert len(items)==2 and all(i['interpretation_conflict'] for i in items)
    assert {i['fact_sha256'] for i in items}=={first['fact_sha256'],second['fact_sha256']}
    assert all(i['currency']=='matching_inputs' for i in items)


def test_rejected_alternative_is_not_an_accepted_interpretation_conflict(tmp_path):
    app=make_app(tmp_path);row=target(app);decide(app,prepare(app,row))
    different=row['evidence_conflict']['variants'][1]['fact_sha256']
    decide(app,prepare(app,row,fact_sha256=different),'reject')
    assert all(not i['interpretation_conflict'] for i in next(r for r in app.history(owner=O)
        if r['id']==row['id'])['evidence_resolutions']['items'])


def test_only_completed_approvals_claim_competing_interpretations(tmp_path,monkeypatch):
    app=make_app(tmp_path);row=target(app)
    decide(app,prepare(app,row))
    different=row['evidence_conflict']['variants'][1]['fact_sha256']
    decide(app,prepare(app,row,fact_sha256=different))
    rejected=decide(app,prepare(app,row),'reject')['approval']['id']
    pending=prepare(app,row)
    def fail(*args,**kwargs):raise OSError('simulated completion write failure')
    monkeypatch.setattr(EvidenceResolutions,'finish',fail)
    with pytest.raises(ValueError,match='unconfirmed'):decide(app,pending)
    items=next(r for r in app.history(owner=O) if r['id']==row['id'])['evidence_resolutions']['items']
    assert len(items)==4
    for item in items:
        assert item['interpretation_conflict']==(item['approval_id'] not in {rejected,pending['id']})


def test_full_ledger_is_used_outside_recent_fifty_rows(tmp_path):
    from test_shared_capability_evidence import approved
    app=make_app(tmp_path);row=target(app)
    for n in range(51):approved(app,kind='runtime_config',marker=str(n))
    assert row['id'] not in {r['id'] for r in app.history(owner=O)}
    result=decide(app,prepare(app,row))
    assert result['resolution']['target_id']==row['id'] and result['resolution']['currency']=='matching_inputs'


def test_foreign_owner_history_has_no_interpretation_metadata(tmp_path):
    app=make_app(tmp_path);row=target(app);pending=prepare(app,row,reason='private owner reason');decide(app,pending)
    complete(app,owner='f'*64);other=app.history(owner='f'*64)
    assert 'private owner reason' not in json.dumps(other) and pending['id'] not in json.dumps(other)
    assert all(r['evidence_resolutions']['total']==0 for r in other)


def test_projection_bounds_retains_total_and_no_history_writes(tmp_path,monkeypatch):
    import prometheus_assistant.evidence_resolution as module
    app=make_app(tmp_path);row=target(app)
    monkeypatch.setattr(module,'MAX_ITEMS',2)
    for i in range(3):decide(app,prepare(app,row,reason=f'Interpretation {i}'))
    before=files(tmp_path);saved=next(r for r in app.history(owner=O) if r['id']==row['id'])
    assert saved['evidence_resolutions']['total']==3 and saved['evidence_resolutions']['truncated']
    assert len(saved['evidence_resolutions']['items'])==2 and files(tmp_path)==before


def test_full_sidecar_does_not_evict_or_consume(tmp_path,monkeypatch):
    import prometheus_assistant.evidence_resolution as module
    app=make_app(tmp_path);row=target(app);monkeypatch.setattr(module,'MAX_PROPOSALS',1)
    decide(app,prepare(app,row));pending=prepare(app,row);before=files(tmp_path)
    with pytest.raises(ValueError,match='capacity full'):decide(app,pending)
    assert files(tmp_path)==before and app.peek(owner=O,binding=B)['id']==pending['id']


@pytest.mark.parametrize('corrupt',['payload','schema','oversize'])
def test_corrupt_store_is_explicitly_unavailable_not_empty_success(tmp_path,corrupt):
    app=make_app(tmp_path);decide(app,prepare(app,target(app)));path=app.evidence_resolutions().path
    with sqlite3.connect(path) as db:
        if corrupt=='payload':db.execute("UPDATE resolutions SET payload='{}' WHERE phase='proposal'")
        elif corrupt=='schema':db.execute('PRAGMA user_version=99')
        else:db.execute('UPDATE resolutions SET payload=? WHERE phase=?',('x'*20000,'proposal'))
    before=files(tmp_path)
    with pytest.raises(ValueError):app.history(owner=O)
    assert files(tmp_path)==before


def test_linked_sidecar_refused_without_touching_target(tmp_path,monkeypatch):
    app=make_app(tmp_path);row=target(app);path=app.evidence_resolutions().path
    original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda self:self==path or original(self))
    before=files(tmp_path)
    with pytest.raises(ValueError,match='Linked'):prepare(app,row)
    assert files(tmp_path)==before


def test_changed_pending_document_fingerprint_rejected_before_consumption(tmp_path):
    app=make_app(tmp_path);pending=prepare(app,target(app));before=files(tmp_path)
    app._pending[O,B]['payload']['reason']='Altered after review'
    with pytest.raises(ValueError,match='fingerprint'):decide(app,pending)
    assert files(tmp_path)==before


def test_canonical_receipts_remain_legacy_compatible(tmp_path):
    from prometheus_assistant.decision_history import DecisionHistory, _event
    app=make_app(tmp_path);row=target(app);pending=prepare(app,row);decide(app,pending)
    records=DecisionHistory(tmp_path/'history')._read_rows()
    receipt=next(r for r in records if r['id']==pending['id'])
    assert receipt['kind']=='other' and receipt['feedback']==''
    assert all(_event(r) for r in records)
    with sqlite3.connect(app.decision_history.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==1
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}=={'decisions'}


def test_bounded_unicode_reason_roundtrips_without_creating_execution_receipts(tmp_path):
    app=make_app(tmp_path);row=target(app);reason='\U0001f50d'*2000
    result=decide(app,prepare(app,row,reason=reason))
    assert result['resolution']['reason']==reason
    assert not (tmp_path/'jobs').exists() and not app._application_starts


@pytest.mark.parametrize('source',['project','worker','application'])
def test_stale_current_snapshot_rejected_for_each_namespace(tmp_path,source):
    app=make_app(tmp_path);row=family(app,source);pending=prepare(app,row)
    other=make_app(tmp_path)
    if source=='application':
        from test_application_outcomes import observe
        observe(other,'recovery_required')
    elif source=='worker':complete(other,marker='new native observation')
    else:
        event=next(r for r in app.history(owner=O) if r['id']==row['id'])['outcomes'][-1]
        event=copy.deepcopy(event);event['event_id']=uuid.uuid4().hex;event['observed_at']+=1
        event.update(phase='undo_started',status='started',checks=None,rollback='unknown',evidence_sha256=None)
        app.record_outcome(owner=O,approval_id=row['id'],fingerprint=row['fingerprint'],event=event)
    before=files(tmp_path)
    with pytest.raises(ValueError,match='changed'):decide(app,pending)
    assert files(tmp_path)==before


@pytest.mark.parametrize('change',[
    {'source':'application'},{'reference':'f'*64},{'target_id':'f'*32},
    {'target_fingerprint':'f'*64},{'snapshot_sha256':'f'*64},
    {'selected_fact':{'status':'active','checks':None,'rollback':None,'evidence_sha256':'f'*64}},
    {'reason':'different reason'},
])
def test_exact_review_document_and_native_fact_tampering_is_rejected(tmp_path,change):
    app=make_app(tmp_path);pending=prepare(app,target(app));before=files(tmp_path)
    app._pending[O,B]['payload'].update(change)
    with pytest.raises(ValueError):decide(app,pending)
    assert files(tmp_path)==before


@pytest.mark.parametrize('change',[None,[],{'kind':'other'}, {'schema_version':True}])
def test_invalid_direct_sidecar_proposals_cannot_create_storage(tmp_path,change):
    app=make_app(tmp_path);pending=prepare(app,target(app));value=proposal(O,pending,1)
    value=change if not isinstance(change,dict) else value|change
    before=files(tmp_path)
    with pytest.raises(ValueError):app.evidence_resolutions().link(value)
    assert files(tmp_path)==before


@pytest.mark.parametrize('mutation',['document_payload','document_review','foreign_owner','result_decision','result_identity'])
def test_malformed_saved_bindings_fail_closed_without_history_mutations(tmp_path,mutation):
    app=make_app(tmp_path);decide(app,prepare(app,target(app)));path=app.evidence_resolutions().path
    phase='decision' if mutation.startswith('result') else 'proposal'
    with sqlite3.connect(path) as db:
        value=json.loads(db.execute('SELECT payload FROM resolutions WHERE phase=?',(phase,)).fetchone()[0])
        if mutation=='document_payload':value['document']['payload']=[]
        elif mutation=='document_review':value['document']['review']['reason']='not reviewed'
        elif mutation=='foreign_owner':value['owner']='f'*64
        elif mutation=='result_decision':value['decision']=[]
        else:value['fingerprint']='f'*64
        db.execute('UPDATE resolutions SET payload=? WHERE phase=?',(canonical(value),phase))
    before=files(tmp_path)
    with pytest.raises(ValueError):app.history(owner=O)
    assert files(tmp_path)==before


def test_nonconflicting_decision_cannot_be_interpreted(tmp_path):
    app=make_app(tmp_path);row=complete(app);before=files(tmp_path)
    with pytest.raises(ValueError,match='conflict'):
        app.prepare_evidence_resolution(owner=O,binding=B,session=S,target_id=row['id'],target_fingerprint=row['fingerprint'],
            snapshot_sha256='f'*64,fact_sha256='e'*64,reason='No conflict exists',policy_revision='p')
    assert files(tmp_path)==before


def test_completion_is_append_only_and_idempotent_for_exact_record(tmp_path):
    app=make_app(tmp_path);pending=prepare(app,target(app));decide(app,pending);store=app.evidence_resolutions()
    p,r=store.read()[0];before=files(tmp_path)
    assert store.link(p) is False and store.finish(r) is False
    with pytest.raises(ValueError):store.finish(r|{'decision':'reject'})
    assert files(tmp_path)==before
