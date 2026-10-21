"""Applicability is a read-only comparison, never a new execution or a repair."""
from copy import deepcopy
import json
import sqlite3

import pytest

from test_evidence_followups import f
from test_evidence_interpretation_lifecycle import change, choose, add_native
from test_evidence_resolution import files as disk_files, make_app, O, B


def finished(f):
    row=f.prepare();f.decide(row);result=f.controller.run_one()
    return row,result


def assessment(f,job_id,**kwargs):
    from prometheus_assistant.evidence_followup_assessment import assess
    return assess(f.store,job_id,f.workspace,project=str(f.root),**kwargs)


def test_matching_completed_result_has_bounded_bindings_and_performs_no_writes(f):
    from prometheus_assistant.evidence_resolution import digest
    row,result=finished(f);before=disk_files(f.root.parent);pending=deepcopy(f.app._pending)
    check=assessment(f,result['job_id'])
    assert check['job_id']==result['job_id'] and check['approval_id']==row['id'] and check['fingerprint']==row['fingerprint']
    assert check['request_digest']==row['payload']['request']['request_digest']
    assert check['result_sha256']==digest(result['result'])
    assert check['historical']==dict(state='completed',result_state='completed',check_status='passed',terminal=True)
    assert check['applicability']=='matching_inputs'
    assert all(v==dict(status='matching_inputs',reason='matching_inputs') for v in check['checks'].values())
    assert check['read_only'] and check['scope']=='point_in_time_inputs_only' and check['observed_at']>0
    assert not any(check[k] for k in ('authorizes_execution','resolves_conflicts','verifies_runtime_correctness'))
    encoded=json.dumps(check)
    assert len(encoded)<4096 and str(f.root) not in encoded and 'answer = 42' not in encoded and 'a.py' not in encoded
    assert disk_files(f.root.parent)==before and f.app._pending==pending
    assert len(f.executions)==1 and not f.recoveries


@pytest.mark.parametrize('operation,decision',[('revise','approve'),('withdraw','approve'),('revise','reject')])
def test_changed_interpretation_keeps_original_terminal_result(f,operation,decision):
    _,result=finished(f);choose(f.app,change(f.app,f.original,operation),decision)
    before=disk_files(f.root.parent);check=assessment(f,result['job_id'])
    assert check['applicability']=='stale' and check['historical']['check_status']=='passed'
    assert check['checks']['interpretation']==dict(status='stale',reason='interpretation_withdrawn' if operation=='withdraw' else 'interpretation_changed')
    assert check['checks']['source']['status']=='matching_inputs'
    assert disk_files(f.root.parent)==before and f.store.public_job(result['job_id'])==result


def test_changed_native_evidence_is_distinct_from_unchanged_interpretation(f):
    _,result=finished(f);add_native(make_app(f.root.parent),f.target,'worker')
    before=disk_files(f.root.parent);check=assessment(f,result['job_id'])
    assert check['applicability']=='stale' and check['checks']['evidence']==dict(status='stale',reason='evidence_changed')
    assert check['checks']['interpretation']['status']=='matching_inputs'
    assert disk_files(f.root.parent)==before


@pytest.mark.parametrize('change',['text','missing','invalid-utf8','too-large'])
def test_source_change_or_unavailability_does_not_change_historical_result(f,change):
    _,result=finished(f);path=f.root/'a.py'
    if change=='text':path.write_bytes(b'answer = 43\n')
    elif change=='missing':path.unlink()
    elif change=='invalid-utf8':path.write_bytes(b'\xff\x00')
    else:path.write_bytes(b'x'*(4*1024**2))
    before=disk_files(f.root.parent);check=assessment(f,result['job_id'])
    status='stale' if change=='text' else 'unavailable'
    assert check['applicability']==status
    assert check['checks']['source']==dict(status=status,reason='source_changed' if change=='text' else 'source_unavailable')
    assert check['historical']['check_status']=='passed' and disk_files(f.root.parent)==before


def test_source_matching_is_reviewed_text_not_unrecorded_bom_identity(f):
    _,result=finished(f);(f.root/'a.py').write_bytes(b'\xef\xbb\xbfanswer = 42\n')
    assert assessment(f,result['job_id'])['checks']['source']['status']=='matching_inputs'


def test_other_open_project_is_refused_before_any_workspace_or_source_read(f,tmp_path):
    from prometheus_assistant.evidence_followup_assessment import assess
    _,result=finished(f);called=[];before=disk_files(f.root.parent)
    def factory(project):called.append(project);raise AssertionError('Foreign project read')
    with pytest.raises(ValueError,match='currently open project'):
        assess(f.store,result['job_id'],factory,project=str(tmp_path/'other'))
    assert not called and disk_files(f.root.parent)==before


def test_workspace_resolving_to_another_root_is_refused_without_file_read(f,tmp_path):
    from prometheus_assistant.evidence_followup_assessment import assess
    from types import SimpleNamespace
    _,result=finished(f)
    with pytest.raises(ValueError,match='currently open project'):
        assess(f.store,result['job_id'],lambda p:SimpleNamespace(root=tmp_path/'other'),project=str(f.root))


@pytest.mark.parametrize('state',['waiting','rejected','unconfirmed','cancelled'])
def test_no_terminal_result_never_has_positive_applicability_or_replays(f,state):
    row=f.prepare();f.decide(row,'reject' if state=='rejected' else 'approve');job=row['payload']['request']['job_id']
    if state=='unconfirmed':f.store.queue.claim(job,now=f.now)
    elif state=='cancelled':f.store.cancel(job,now=f.now)
    before=disk_files(f.root.parent);check=assessment(f,job)
    assert check['applicability']=='unavailable' and not check['historical']['terminal']
    assert check['historical']['state']==state and check['historical']['check_status']=='unavailable'
    assert not f.executions and not f.recoveries and disk_files(f.root.parent)==before
    if state=='rejected':assert not f.store.queue.path.exists()


def test_failed_syntax_stays_failed_even_when_inputs_match(f):
    f.passed=False;_,result=finished(f);check=assessment(f,result['job_id'])
    assert check['applicability']=='matching_inputs' and check['historical']['terminal']
    assert check['historical']['state']=='completed' and check['historical']['check_status']=='failed'
    assert f.store.public_job(result['job_id'])==result


@pytest.mark.parametrize('status',['findings','tool_failed'])
def test_failed_lint_and_tool_receipts_stay_failed(f,status):
    from prometheus_assistant.remote_job_protocol import LINT_OPERATION
    f.adapter.lint_tool=lambda:dict(version='0.14.0',sha256='d'*64)
    def execute(req):
        f.executions.append(deepcopy(req))
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest','operation','tool','profile')},
            'state':'failed' if status=='tool_failed' else 'completed','status':status,
            'checks':[dict(path='a.py',passed=False)],
            'diagnostics':[dict(path='a.py',code='F821',message='Generated finding',line=1,column=1,end_line=1,end_column=7)] if status=='findings' else [],
            'source_unchanged':True,'reason':'tool_unavailable' if status=='tool_failed' else ''}
    f.adapter.execute=execute;row=f.prepare(operation=LINT_OPERATION);f.decide(row);result=f.controller.run_one()
    before=disk_files(f.root.parent);check=assessment(f,result['job_id'])
    assert check['applicability']=='matching_inputs' and check['historical']['check_status']=='failed'
    assert check['historical']['state']==result['state'] and check['historical']['terminal']
    assert disk_files(f.root.parent)==before and len(f.executions)==1


@pytest.mark.parametrize('boundary',['interpretation','evidence','source','result'])
def test_concurrent_input_or_result_change_is_unavailable(f,boundary,monkeypatch):
    from prometheus_assistant.coding_workspace import CodingWorkspace
    row,result=finished(f) if boundary!='result' else (f.prepare(),None)
    if boundary=='result':f.decide(row);result=f.store.public_job(row['payload']['request']['job_id'])
    read=CodingWorkspace.read_file;calls=[]
    def changing(workspace,path):
        value=read(workspace,path);calls.append(path)
        if len(calls)==1:
            if boundary=='interpretation':choose(f.app,change(f.app,f.original,'withdraw'))
            elif boundary=='evidence':add_native(make_app(f.root.parent),f.target,'worker')
            elif boundary=='source':(f.root/'a.py').write_bytes(b'changed = True\n')
            else:f.store.queue.claim(result['job_id'],now=f.now)
        return value
    monkeypatch.setattr(CodingWorkspace,'read_file',changing)
    check=assessment(f,result['job_id'])
    assert check['applicability']=='unavailable'
    assert all(c==dict(status='unavailable',reason='inputs_changed_during_assessment') for c in check['checks'].values())
    assert len(calls)==2 and not f.recoveries


@pytest.mark.parametrize('store_name',['followups','lifecycle','canonical','queue','receipt'])
def test_corrupt_history_is_rejected_never_reported_as_matching(f,store_name):
    _,result=finished(f)
    if store_name=='followups':
        with sqlite3.connect(f.store.path) as db:db.execute("UPDATE followups SET payload='{}'")
    elif store_name=='lifecycle':
        choose(f.app,change(f.app,f.original))
        with sqlite3.connect(f.app.interpretation_lifecycle().path) as db:db.execute('PRAGMA user_version=99')
    elif store_name=='canonical':
        with sqlite3.connect(f.app.decision_history.path) as db:db.execute('PRAGMA user_version=99')
    elif store_name=='queue':
        with sqlite3.connect(f.store.queue.path) as db:db.execute('PRAGMA user_version=99')
    else:
        with sqlite3.connect(f.store.queue.path) as db:
            outcome=json.loads(db.execute('SELECT result FROM jobs').fetchone()[0]);outcome['evidence']['status']='failed'
            db.execute('UPDATE jobs SET result=?',(json.dumps(outcome,sort_keys=True,separators=(',',':'),ensure_ascii=False),))
    before=disk_files(f.root.parent)
    with pytest.raises(ValueError):assessment(f,result['job_id'])
    assert disk_files(f.root.parent)==before and len(f.executions)==1


def test_missing_job_or_foreign_owner_cannot_read_or_create_history(f):
    from prometheus_assistant.evidence_followups import EvidenceFollowups
    from prometheus_assistant.evidence_followup_assessment import assess
    before=disk_files(f.root.parent)
    with pytest.raises(ValueError):assessment(f,'f'*32)
    assert disk_files(f.root.parent)==before and not f.store.path.exists() and not f.store.queue.path.exists()
    _,result=finished(f);foreign=EvidenceFollowups(f.app,'f'*64);before=disk_files(f.root.parent)
    with pytest.raises(ValueError):assess(foreign,result['job_id'],f.workspace,project=str(f.root))
    assert disk_files(f.root.parent)==before and not foreign.queue.path.exists()


def test_assessment_never_expires_pending_review_or_uses_active_scheduler_methods(f,monkeypatch):
    from prometheus_assistant.evidence_followups import FollowupScheduler
    _,result=finished(f);pending=f.prepare();before=disk_files(f.root.parent);ram=deepcopy(f.app._pending)
    def forbidden(*a,**k):raise AssertionError('Read-only assessment invoked an active boundary')
    for name in ('_expire','consume','review','pending'):monkeypatch.setattr(f.app,name,forbidden)
    for name in ('claim','finish','cancel','enqueue'):monkeypatch.setattr(f.store.queue,name,forbidden)
    for name in ('prepare','run_one','decide'):monkeypatch.setattr(FollowupScheduler,name,forbidden)
    f.adapter.execute=f.adapter.recover=f.adapter.admission=forbidden
    assert assessment(f,result['job_id'])['applicability']=='matching_inputs'
    assert disk_files(f.root.parent)==before and f.app._pending==ram and pending['id'] in repr(ram)


def test_older_unfinished_change_does_not_hide_a_newer_valid_current_head(f,monkeypatch):
    from prometheus_assistant.evidence_interpretation_lifecycle import EvidenceInterpretationLifecycle
    finish=EvidenceInterpretationLifecycle.finish
    def failed(*a,**k):raise OSError('Generated completion interruption')
    monkeypatch.setattr(EvidenceInterpretationLifecycle,'finish',failed)
    with pytest.raises(ValueError,match='unconfirmed'):choose(f.app,change(f.app,f.original))
    monkeypatch.setattr(EvidenceInterpretationLifecycle,'finish',finish)
    current=choose(f.app,change(f.app,f.original))['interpretation_change']
    assert current['completion_unconfirmed']
    _,result=finished(f)
    assert assessment(f,result['job_id'])['applicability']=='matching_inputs'


def test_complete_lifecycle_fold_does_not_depend_on_truncated_history(f,monkeypatch):
    import prometheus_assistant.evidence_interpretation_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle,'MAX_ITEMS',1)
    for n in range(3):choose(f.app,change(f.app,f.original,reason='Retained rejected change '+str(n)),'reject')
    _,result=finished(f)
    assert assessment(f,result['job_id'])['applicability']=='matching_inputs'


@pytest.mark.parametrize('missing',['interpretation','evidence'])
def test_missing_current_basis_is_unavailable_not_a_clean_result(f,missing):
    _,result=finished(f)
    path=f.app.evidence_resolutions().path if missing=='interpretation' else f.app.capability_outcomes().path
    path.unlink();before=disk_files(f.root.parent);check=assessment(f,result['job_id'])
    assert check['applicability']=='unavailable'
    assert check['checks'][missing]==dict(status='unavailable',reason=missing+'_unavailable')
    assert check['historical']['check_status']=='passed' and disk_files(f.root.parent)==before


def test_workspace_unavailable_returns_fixed_reason_without_error_details(f):
    from prometheus_assistant.evidence_followup_assessment import assess
    _,result=finished(f)
    def unavailable(project):raise OSError('PRIVATE /secret/source.py account-token')
    check=assess(f.store,result['job_id'],unavailable,project=str(f.root))
    assert check['checks']['source']==dict(status='unavailable',reason='source_unavailable')
    assert 'PRIVATE' not in json.dumps(check) and 'account-token' not in json.dumps(check)


def test_only_exact_reviewed_files_are_read_twice_with_shared_receipt_snapshots(f,monkeypatch):
    from prometheus_assistant.coding_workspace import CodingWorkspace
    files=[]
    for n in range(8):
        name=f'a{n}.py';content=f'value = {n}\n';(f.root/name).write_bytes(content.encode('utf-8'));files.append(dict(path=name,content=content))
    row=f.prepare(files=files);f.decide(row);result=f.controller.run_one()
    read=CodingWorkspace.read_file;read_decisions=f.app.decision_history._all_rows;source_calls=[];canonical_calls=[]
    def observed(workspace,path):source_calls.append(path);return read(workspace,path)
    def decisions():canonical_calls.append(True);return read_decisions()
    monkeypatch.setattr(CodingWorkspace,'read_file',observed);monkeypatch.setattr(f.app.decision_history,'_all_rows',decisions)
    assert assessment(f,result['job_id'])['applicability']=='matching_inputs'
    assert source_calls==[r['path'] for r in files]*2 and len(canonical_calls)==2


@pytest.mark.parametrize('job_id',[None,'','x'*32,'f'*64])
def test_invalid_job_identity_cannot_read_sources(f,job_id):
    before=disk_files(f.root.parent)
    with pytest.raises(ValueError):assessment(f,job_id)
    assert disk_files(f.root.parent)==before and not f.store.path.exists()
