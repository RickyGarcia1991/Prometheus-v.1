"""Follow-ups require fresh owner review, provenance and a single durable claim."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import json
import sqlite3
import uuid

import pytest
from prometheus_assistant.evidence_followups import EvidenceFollowups, FollowupScheduler
from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.remote_job_protocol import validate_receipt, LINT_OPERATION, LINT_PROFILE
from prometheus_assistant.evidence_resolution import proposal
from test_evidence_interpretation_lifecycle import setup_root, item, change, choose, add_native
from test_evidence_resolution import make_app, files as disk_files, O, B, S


@pytest.fixture
def f(tmp_path):
    app,target,original=setup_root(tmp_path)
    root=tmp_path/'project';root.mkdir();(root/'a.py').write_bytes(b'answer = 42\n')
    state=SimpleNamespace(now=100.,owner=O,valid=True,cpu=10.,executions=[],recoveries=[],passed=True,stopped=False,
        root=root,app=app,target=target,original=original,request_files=[dict(path='a.py',content='answer = 42\n')])
    state.destination=dict(account_id='account',device_id='surface',device_name='Surface',worker_digest='b'*64,
                           configuration_digest='c'*64,online=True)
    @contextmanager
    def grant():
        if not state.valid:raise ValueError('Owner is locked.')
        yield state.owner
    state.grant=grant
    def admission(req=None):
        return dict(authenticated=True,decision='ready',reasons=[],observed_at=state.now,expires_at=state.now+30,
            observations=dict(available_ram_gib=4,free_disk_gib=10,cpu_percent=state.cpu,plugged_in=True))
    def receipt(req):
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
            'state':'completed','checks':[dict(path=row['path'],passed=state.passed) for row in req['files']]}
    def execute(req):state.executions.append(deepcopy(req));return receipt(req)
    def recover(req):
        state.recoveries.append(deepcopy(req));raw=receipt(req)
        return dict(state=raw['state'],receipt=raw,evidence=validate_receipt(req,raw))
    state.adapter=SimpleNamespace(target=lambda:deepcopy(state.destination),admission=admission,execute=execute,recover=recover)
    state.workspace=lambda project:CodingWorkspace(project,tmp_path/'project-history')
    def open_controller(app):
        state.app=app;state.store=EvidenceFollowups(app,O)
        state.controller=FollowupScheduler(app,state.store,adapters=[state.adapter],grant_owner=grant,binding=B,
            workspace_factory=state.workspace,clock=lambda:state.now,cancelled=lambda:state.stopped)
    state.open=open_controller;open_controller(app)
    def prepare(**kwargs):
        current=item(state.app,original)
        ctx=dict(interpretation_id=original['id'],interpretation_fingerprint=original['fingerprint'],
            state_sha256=current['state_sha256'],snapshot_sha256=current['effective_payload']['snapshot_sha256'],
            fact_sha256=current['effective_payload']['fact_sha256'],reason='Check the saved source as supplemental evidence.')
        return state.controller.prepare(**(dict(project=str(root),files=state.request_files,job_id=uuid.uuid4().hex,
            session=S,context=ctx)|kwargs))
    state.prepare=prepare
    state.decide=lambda row,decision='approve',**kwargs:state.controller.decide(**(dict(session=S,
        approval_id=row['id'],fingerprint=row['fingerprint'],decision=decision)|kwargs))
    return state


def test_prepare_is_inert_and_exact_review_describes_snapshot(f):
    before=disk_files(f.root.parent);row=f.prepare()
    assert disk_files(f.root.parent)==before
    assert row['kind']=='evidence_followup' and row['review']['followup']==row['payload']['followup']
    assert row['review']['request_digest']==row['payload']['request']['request_digest']
    assert row['review']['operation']=='python-syntax-check'
    assert not f.store.queue.path.exists() and not f.store.path.exists()
    assert f.store.public_jobs()==[]


@pytest.mark.parametrize('projection',['jobs','history'])
def test_followup_projection_reuses_supplied_canonical_snapshot(f,monkeypatch,projection):
    row=f.prepare();f.decide(row)
    decisions=f.app.decision_history._all_rows()
    operation=f.store.public_jobs if projection=='jobs' else f.store.history_projection
    expected=operation()
    def reread():raise AssertionError('Canonical history was read twice.')
    monkeypatch.setattr(f.app.decision_history,'_all_rows',reread)
    assert operation(decisions=decisions)==expected
    # An explicitly empty snapshot must not silently fall back to the newer
    # ledger. The queued record has no approval in that observed snapshot.
    with pytest.raises(ValueError,match='approved provenance'):operation(decisions=[])


@pytest.mark.parametrize('projection',['jobs','history'])
def test_standalone_followup_projection_reads_canonical_history_once(f,monkeypatch,projection):
    row=f.prepare();f.decide(row)
    read=f.app.decision_history._all_rows;calls=[]
    def counted():calls.append(True);return read()
    monkeypatch.setattr(f.app.decision_history,'_all_rows',counted)
    result=f.store.public_jobs() if projection=='jobs' else f.store.history_projection()
    assert result and len(calls)==1


@pytest.mark.parametrize('passed',[True,False])
def test_execution_is_supplemental_exact_once_and_read_only_history(f,passed):
    before=next(r for r in f.app.history(owner=O) if r['id']==f.target['id'])
    f.passed=passed;row=f.prepare();queued=f.decide(row)
    assert queued['state']=='waiting' and queued['decision']=='approve'
    result=f.controller.run_one()
    assert result['state']=='completed' and result['result']['evidence']['status']==('passed' if passed else 'failed')
    assert len(f.executions)==1 and f.controller.run_one() is None
    assert f.controller.run_one(result['job_id'],recover=True)==result and not f.recoveries
    saved=f.app.history(owner=O);native=next(r for r in saved if r['id']==f.target['id'])
    for key in ('outcomes','capability_outcomes','application_outcomes','evidence_lifecycle','evidence_conflict'):
        assert native[key]==before[key]
    assert native['evidence_followups']['items']==[result]
    canonical=next(r for r in saved if r['id']==row['id'])
    assert canonical['kind']=='other' and canonical['capability_outcomes']==[]
    before_read=disk_files(f.root.parent)
    f.open(make_app(f.root.parent))
    assert f.store.public_jobs()==[result] and f.app.history(owner=O)==saved
    assert disk_files(f.root.parent)==before_read
    assert 'answer = 42' not in json.dumps(result) and str(f.root) not in json.dumps(result)


def test_restart_drops_pending_authority_and_does_not_enqueue(f):
    row=f.prepare();f.open(make_app(f.root.parent))
    with pytest.raises(ValueError):f.decide(row)
    assert f.store.public_jobs()==[] and not f.store.queue.path.exists()


def test_rejection_retains_history_without_queue_or_execution(f):
    row=f.prepare();result=f.decide(row,'reject')
    assert result['state']=='rejected' and result['decision']=='reject'
    assert not f.store.queue.path.exists() and not f.executions
    assert f.controller.run_one() is None


@pytest.mark.parametrize('field,value',[('session','f'*32),('approval_id','f'*32),('fingerprint','f'*64),('decision','request_changes')])
def test_wrong_exact_review_does_not_link_or_consume(f,field,value):
    row=f.prepare()
    with pytest.raises(ValueError):f.decide(row,**{field:value})
    assert not f.store.path.exists() and f.app.pending(owner=O,binding=B)


@pytest.mark.parametrize('phase',['prepare','decide','run'])
def test_owner_and_lock_are_required(f,phase):
    row=None if phase=='prepare' else f.prepare()
    if phase=='run':f.decide(row)
    for owner,valid in [('f'*64,True),(O,False)]:
        f.owner=owner;f.valid=valid
        with pytest.raises(ValueError):
            if phase=='prepare':f.prepare()
            elif phase=='decide':f.decide(row)
            else:f.controller.run_one()
    assert not f.executions


@pytest.mark.parametrize('kind',['files','revise','withdraw','native','target','policy','cancelled'])
def test_change_during_admission_prevents_claim(f,kind,monkeypatch):
    row=f.prepare();f.decide(row)
    admission=f.adapter.admission
    def altered(req=None):
        report=admission(req)
        if kind=='files':(f.root/'a.py').write_text('answer = 99\n')
        elif kind in {'revise','withdraw'}:choose(f.app,change(f.app,f.original,kind))
        elif kind=='native':add_native(f.app,f.target,'worker')
        elif kind=='target':f.destination['configuration_digest']='d'*64
        elif kind=='policy':monkeypatch.setattr('prometheus_assistant.scheduled_syntax.revision',lambda:'f'*64)
        else:f.stopped=True
        return report
    f.adapter.admission=altered
    result=f.controller.run_one()
    assert result['state']=='cancelled' and not f.executions
    assert f.store.queue.get(row['payload']['request']['job_id'])['state']=='cancelled'


@pytest.mark.parametrize('kind',['files','revise','withdraw','native'])
def test_changed_before_decision_refuses_authority_but_allows_reject(f,kind):
    row=f.prepare()
    if kind=='files':(f.root/'a.py').write_text('answer = 99\n')
    elif kind in {'revise','withdraw'}:
        other=make_app(f.root.parent);choose(other,change(other,f.original,kind))
    else:add_native(make_app(f.root.parent),f.target,'worker')
    with pytest.raises(ValueError):f.decide(row)
    assert f.app.pending(owner=O,binding=B) and not f.store.path.exists()
    assert f.decide(row,'reject')['state']=='rejected'


def test_approved_waiting_survives_restart_but_needs_unlock(f):
    f.cpu=99;row=f.prepare();f.decide(row)
    assert f.controller.run_one()['state']=='waiting'
    f.open(make_app(f.root.parent));f.now+=31;f.cpu=10;f.valid=False
    with pytest.raises(ValueError):f.controller.run_one()
    assert not f.executions
    f.valid=True;assert f.controller.run_one()['state']=='completed' and len(f.executions)==1


@pytest.mark.parametrize('cut',['link','consume','enqueue'])
def test_interrupt_before_queue_never_reconstructs_execution(f,cut,monkeypatch):
    row=f.prepare()
    def interrupted(*a,**k):raise OSError('Generated interruption')
    if cut=='link':monkeypatch.setattr(f.app,'consume',interrupted)
    elif cut=='consume':monkeypatch.setattr(f.store.queue,'enqueue',interrupted)
    else:
        real=f.store.queue.enqueue
        def persisted(*a,**k):real(*a,**k);raise OSError('Generated crash after enqueue')
        monkeypatch.setattr(f.store.queue,'enqueue',persisted)
    with pytest.raises(OSError):f.decide(row)
    f.open(make_app(f.root.parent))
    if cut=='enqueue':assert f.controller.run_one()['state']=='completed' and len(f.executions)==1
    else:
        assert f.store.public_jobs()[0]['state']=='unconfirmed'
        assert f.controller.run_one() is None and not f.executions and not f.store.queue.path.exists()


def test_crash_after_claim_and_later_withdrawal_queries_only_receipt(f,monkeypatch):
    row=f.prepare();f.decide(row)
    real=f.store.queue.claim
    def interrupted(*a,**k):real(*a,**k);raise SystemExit('Generated crash after durable claim')
    monkeypatch.setattr(f.store.queue,'claim',interrupted)
    with pytest.raises(SystemExit):f.controller.run_one()
    f.open(make_app(f.root.parent));choose(f.app,change(f.app,f.original,'withdraw'))
    assert f.controller.run_one() is None and not f.executions
    result=f.controller.run_one(row['payload']['request']['job_id'],recover=True)
    assert result['state']=='completed' and len(f.recoveries)==1 and not f.executions
    assert f.controller.run_one(result['job_id'],recover=True)==result and len(f.recoveries)==1


def test_missing_or_changed_sidecar_fails_closed_without_queue_write(f):
    row=f.prepare();f.decide(row)
    with sqlite3.connect(f.store.path) as db:db.execute('DELETE FROM followups')
    before=f.store.queue.path.read_bytes()
    with pytest.raises(ValueError,match='provenance'):f.controller.run_one()
    assert f.store.queue.path.read_bytes()==before and not f.executions


def test_empty_valid_lifecycle_store_remains_readable_after_failed_first_claim(f):
    row=f.prepare();f.decide(row);(f.root/'a.py').write_text('changed = True\n')
    assert f.controller.run_one()['state']=='cancelled'
    assert f.app.interpretation_lifecycle().read()==[]
    assert f.app.history(owner=O)

@pytest.mark.parametrize('status',['passed','findings','tool_failed'])
def test_fixed_lint_receipts_retain_tool_identity_and_never_hide_failure(f,status):
    f.adapter.lint_tool=lambda:dict(version='0.14.0',sha256='d'*64)
    def execute(req):
        f.executions.append(deepcopy(req))
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest','operation','tool','profile')},
            'state':'failed' if status=='tool_failed' else 'completed','status':status,
            'checks':[dict(path='a.py',passed=status=='passed')],
            'diagnostics':[dict(path='a.py',code='F821',message='Generated finding',line=1,column=1,end_line=1,end_column=7)] if status=='findings' else [],
            'source_unchanged':True,'reason':'tool_unavailable' if status=='tool_failed' else ''}
    f.adapter.execute=execute
    row=f.prepare(operation=LINT_OPERATION)
    assert row['payload']['request']['tool']==f.adapter.lint_tool() and row['review']['profile']==LINT_PROFILE
    f.decide(row);result=f.controller.run_one()
    assert result['state']==('failed' if status=='tool_failed' else 'completed')
    assert result['result']['evidence']['status']==('passed' if status=='passed' else 'failed')
    assert result['queued'] and result['tool']==f.adapter.lint_tool() and result['profile']==LINT_PROFILE
    assert len(f.executions)==1 and f.controller.run_one() is None


def test_lint_tool_changed_during_resource_query_cancels(f):
    f.adapter.lint_tool=lambda:dict(version='0.14.0',sha256='d'*64)
    row=f.prepare(operation=LINT_OPERATION);f.decide(row)
    original=f.adapter.admission
    def changed(req=None):
        f.adapter.lint_tool=lambda:dict(version='0.14.1',sha256='e'*64)
        return original(req)
    f.adapter.admission=changed
    assert f.controller.run_one()['state']=='cancelled' and not f.executions


def test_tampered_stored_evidence_cannot_masquerade_as_a_passing_receipt(f):
    f.passed=False;row=f.prepare();f.decide(row);result=f.controller.run_one()
    with sqlite3.connect(f.store.queue.path) as db:
        outcome=json.loads(db.execute('SELECT result FROM jobs').fetchone()[0])
        outcome['evidence']['status']='passed'
        db.execute('UPDATE jobs SET result=?',(json.dumps(outcome,sort_keys=True,separators=(',',':'),ensure_ascii=False),))
    with pytest.raises(ValueError,match='evidence differs'):f.store.public_jobs()
    with pytest.raises(ValueError,match='evidence differs'):f.controller.run_one(result['job_id'],recover=True)
    assert len(f.executions)==1 and not f.recoveries


@pytest.mark.parametrize('fault',['unknown_receipt','wrong_job','empty_receipt'])
def test_uncertain_receipt_does_not_become_success_or_rerun(f,fault):
    row=f.prepare();f.decide(row)
    def failed(req):
        f.executions.append(deepcopy(req))
        if fault=='empty_receipt':return {}
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
            'job_id':'f'*32 if fault=='wrong_job' else req['job_id'],
            'state':'running','checks':[dict(path='a.py',passed=True)]}
    f.adapter.execute=failed
    assert f.controller.run_one()['state']=='unconfirmed'
    assert f.controller.run_one() is None and len(f.executions)==1


def test_terminal_claim_is_not_retroactively_invalidated_by_revision(f):
    row=f.prepare();f.decide(row);run=f.adapter.execute
    def changed(req):
        choose(f.app,change(f.app,f.original,'withdraw'))
        return run(req)
    f.adapter.execute=changed
    result=f.controller.run_one()
    assert result['state']=='completed' and item(f.app,f.original)['state']=='withdrawn'
    assert f.controller.run_one(result['job_id'],recover=True)==result and len(f.executions)==1


@pytest.mark.parametrize('bad_context',[{'interpretation_id':'f'*32},{'state_sha256':'f'*64},
    {'snapshot_sha256':'f'*64},{'fact_sha256':'f'*64},{'reason':' '},{'reason':'x'*2001},{'reason':'bad\x00'}])
def test_changed_or_malformed_context_does_not_prepare(f,bad_context):
    current=item(f.app,f.original)
    ctx=dict(interpretation_id=f.original['id'],interpretation_fingerprint=f.original['fingerprint'],
        state_sha256=current['state_sha256'],snapshot_sha256=current['effective_payload']['snapshot_sha256'],
        fact_sha256=current['effective_payload']['fact_sha256'],reason='A fresh bounded check.')
    with pytest.raises(ValueError):f.prepare(context=ctx|bad_context)
    assert not f.store.path.exists() and not f.app.pending(owner=O,binding=B)


def test_wrong_saved_file_or_project_fails_before_review(f,tmp_path):
    for change_args in [dict(files=[dict(path='../a.py',content='answer = 42\n')]),
                        dict(files=[dict(path='a.py',content='changed')]),dict(project=str(tmp_path/'missing'))]:
        with pytest.raises((ValueError,OSError)):f.prepare(**change_args)
    assert not f.store.path.exists() and not f.app.pending(owner=O,binding=B)


def test_corrupt_followup_store_is_explicit_not_empty_history(f):
    row=f.prepare();f.decide(row,'reject')
    with sqlite3.connect(f.store.path) as db:db.execute("UPDATE followups SET payload='{}'")
    with pytest.raises(ValueError):f.app.history(owner=O)
    with pytest.raises(ValueError):f.controller.run_one()
    assert not f.executions


def test_capacity_rejects_new_decision_without_consuming_or_hiding_existing(f,monkeypatch):
    one=f.prepare();f.decide(one,'reject')
    monkeypatch.setattr('prometheus_assistant.evidence_followups.MAX_LINKS',1)
    two=f.prepare()
    with pytest.raises(ValueError,match='full'):f.decide(two)
    assert f.app.pending(owner=O,binding=B)['id']==two['id']
    assert len(f.store.public_jobs())==1 and f.store.public_jobs()[0]['decision']=='reject'


def _claim_process(history_root,queue_path,context,ready,start,results):
    from prometheus_assistant.syntax_waiting_store import WaitingStore
    app=make_app(Path(history_root));ready.put('ready')
    if not start.wait(20):raise RuntimeError('Generated race not started')
    try:
        with app.interpretation_lifecycle().claim_guard(O,context['interpretation_id'],
                context['interpretation_fingerprint'],context['state_sha256']):
            value=WaitingStore(Path(queue_path),O).claim(context['job_id'],now=100)
        results.put(('claimed',value['claimed']))
    except ValueError:results.put(('refused',False))


def _change_process(history_root,original,operation,ready,start,results):
    app=make_app(Path(history_root));pending=change(app,original,operation)
    ready.put('ready')
    if not start.wait(20):raise RuntimeError('Generated race not started')
    try:results.put(('changed',choose(app,pending)['interpretation_change']['state']))
    except ValueError:results.put(('stale',None))


@pytest.mark.parametrize('operation',['revise','withdraw'])
def test_separate_process_change_and_claim_have_one_serialized_order(f,operation):
    import multiprocessing
    row=f.prepare();f.decide(row);p=row['payload']['followup']
    context={k:p[k] for k in ('interpretation_id','interpretation_fingerprint','state_sha256')}
    context['job_id']=row['payload']['request']['job_id']
    ctx=multiprocessing.get_context('spawn');ready=ctx.Queue();results=ctx.Queue();start=ctx.Event()
    workers=[ctx.Process(target=_claim_process,args=(str(f.root.parent),str(f.store.queue.path),context,ready,start,results)),
             ctx.Process(target=_change_process,args=(str(f.root.parent),f.original,operation,ready,start,results))]
    for worker in workers:worker.start()
    assert ready.get(timeout=30)=='ready' and ready.get(timeout=30)=='ready';start.set()
    outputs=[results.get(timeout=30),results.get(timeout=30)]
    for worker in workers:worker.join(30);assert worker.exitcode==0
    assert any(kind=='changed' for kind,_ in outputs)
    claimed=next(flag for kind,flag in outputs if kind in {'claimed','refused'})
    queued=f.store.queue.get(context['job_id'])
    assert queued['state']==('unconfirmed' if claimed else 'waiting')
    assert item(f.app,f.original)['state_sha256']!=p['state_sha256']
    assert not f.executions


def test_separate_process_double_claim_cannot_duplicate(f):
    import multiprocessing
    row=f.prepare();f.decide(row);p=row['payload']['followup']
    context={k:p[k] for k in ('interpretation_id','interpretation_fingerprint','state_sha256')}
    context['job_id']=row['payload']['request']['job_id']
    ctx=multiprocessing.get_context('spawn');ready=ctx.Queue();results=ctx.Queue();start=ctx.Event()
    workers=[ctx.Process(target=_claim_process,args=(str(f.root.parent),str(f.store.queue.path),context,ready,start,results)) for _ in range(2)]
    for worker in workers:worker.start()
    assert ready.get(timeout=30)=='ready' and ready.get(timeout=30)=='ready';start.set()
    outputs=[results.get(timeout=30),results.get(timeout=30)]
    for worker in workers:worker.join(30);assert worker.exitcode==0
    assert sorted(value for _,value in outputs)==[False,True]
    assert f.store.queue.get(context['job_id'])['state']=='unconfirmed' and not f.executions
