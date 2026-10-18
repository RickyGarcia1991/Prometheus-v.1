import sqlite3
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant import decision_revisions

O='a'*64;B='b'*64;S='c'*32;P='d'*64

def review(a,**extra):
    return a.review(owner=O,binding=B,session=S,kind='project_edit',payload={'private':'source'},
                    review={},policy_revision='p',**extra)

def decide(a,r,decision='request_changes'):
    return a.consume(owner=O,binding=B,session=S,approval_id=r['id'],fingerprint=r['fingerprint'],
                     policy_revision='p',decision=decision,feedback='Smaller' if decision=='request_changes' else '')

def setup(tmp_path,clock=lambda:100):
    a=ActionApprovals(history_root=tmp_path,clock=clock);r=review(a);decide(a,r)
    context=a.revision_context(owner=O,binding=B,session=S,parent_row=r,project_hash=P)
    return a,r,context

def test_link_restart_legacy_bytes_and_child_decision(tmp_path):
    a,parent,ctx=setup(tmp_path);before=a.decision_history.path.read_bytes()
    child=review(a,revision_token=ctx['revision_token'],project_hash=P)
    assert a.decision_history.path.read_bytes()==before
    assert a.history(owner=O)[0]['revisions'][0]['child_state']=='unrecorded'
    reopened=ActionApprovals(history_root=tmp_path)
    assert reopened.pending(owner=O,binding=B) is None
    assert reopened.history(owner=O)[0]['revisions'][0]['child_id']==child['id']
    decide(a,child,'approve')
    rows=a.history(owner=O)
    assert all(r['revisions'][0]['child_state']=='recorded' for r in rows)
    assert a.history(owner='e'*64)==[]
    assert 'private' not in a._revisions().path.read_bytes().decode('latin1')

@pytest.mark.parametrize('change',[{'owner':'e'*64},{'binding':'e'*64},{'session':'e'*32},{'project_hash':'e'*64}])
def test_context_identity_mismatch(tmp_path,change):
    a,r,c=setup(tmp_path)
    args=dict(owner=O,binding=B,session=S,kind='project_edit',payload={},review={},policy_revision='p',revision_token=c['revision_token'],project_hash=P)
    with pytest.raises(ValueError):a.review(**(args|change))
    assert a.pending(owner=O,binding=B) is None
    assert review(a,revision_token=c['revision_token'],project_hash=P)

def test_expiry_revocation_and_restart(tmp_path):
    clock=[100];a,r,c=setup(tmp_path,lambda:clock[0]);clock[0]=701
    with pytest.raises(ValueError):review(a,revision_token=c['revision_token'],project_hash=P)
    c=a.revision_context(owner=O,binding=B,session=S,parent_row=r,project_hash=P)
    a.revoke_binding(B)
    with pytest.raises(ValueError):review(a,revision_token=c['revision_token'],project_hash=P)

def test_failure_before_publication_keeps_intent(tmp_path,monkeypatch):
    a,r,c=setup(tmp_path);original=decision_revisions.DecisionRevisions.record
    monkeypatch.setattr(decision_revisions.DecisionRevisions,'record',lambda *args:(_ for _ in ()).throw(ValueError('disk')))
    with pytest.raises(ValueError):review(a,revision_token=c['revision_token'],project_hash=P)
    assert a.pending(owner=O,binding=B) is None
    monkeypatch.setattr(decision_revisions.DecisionRevisions,'record',original)
    review(a,revision_token=c['revision_token'],project_hash=P)

def test_capacity_duplicate_conflict_and_corruption(tmp_path,monkeypatch):
    a,r,c=setup(tmp_path);review(a,revision_token=c['revision_token'],project_hash=P)
    store=a._revisions();row=store.read()[0]
    assert store.record(row) is False
    with pytest.raises(ValueError):store.record(row|{'created_at':row['created_at']+1})
    monkeypatch.setattr(decision_revisions,'MAX_LINKS',1)
    with pytest.raises(ValueError):store.record(row|{'child_id':'f'*32})
    with sqlite3.connect(store.path) as db:db.execute("UPDATE revisions SET payload='{}'")
    with pytest.raises(ValueError):a.history(owner=O)

def test_parent_must_be_saved_exact_change_request(tmp_path):
    a=ActionApprovals(history_root=tmp_path);r=review(a)
    with pytest.raises(ValueError):a.revision_context(owner=O,binding=B,session=S,parent_row=r,project_hash=P)
    decide(a,r,'approve')
    with pytest.raises(ValueError):a.revision_context(owner=O,binding=B,session=S,parent_row=r,project_hash=P)

def test_read_only_missing_store_and_legacy(tmp_path):
    a=ActionApprovals(history_root=tmp_path)
    assert a.history(owner=O)==[] and not a._revisions().path.exists()
    old=ActionApprovals();r=review(old);decide(old,r)
    with pytest.raises(ValueError):old.revision_context(owner=O,binding=B,session=S,parent_row=r,project_hash=P)

def test_early_validation_readonly_and_consumed_token(tmp_path):
    a,r,c=setup(tmp_path);args=dict(owner=O,binding=B,session=S,project_hash=P,revision_token=c['revision_token'])
    assert a.validate_revision_context(**args)=={'origin_approval':r['id']}
    assert not a._revisions().path.exists()
    review(a,revision_token=c['revision_token'],project_hash=P)
    with pytest.raises(ValueError):a.validate_revision_context(**args)

def test_thread_conflict_and_native_reopen(tmp_path):
    a,r,c=setup(tmp_path);child=review(a,revision_token=c['revision_token'],project_hash=P)
    store=a._revisions();row=store.read()[0]
    def write(n):
        try:return store.record(row|{'created_at':row['created_at']+n})
        except ValueError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(write,[0,1]))==[False,'conflict']
    code="from prometheus_assistant.action_approvals import ActionApprovals; import sys; a=ActionApprovals(history_root=sys.argv[1]); assert a.history(owner='a'*64)[0]['revisions'][0]['child_state']=='unrecorded'; assert a.pending(owner='a'*64,binding='b'*64) is None"
    code='import sys; sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]/'src'))+');'+code
    subprocess.run([sys.executable,'-B','-c',code,str(tmp_path)],check=True,capture_output=True,timeout=15)

def test_chain_bound_and_backward_clock(tmp_path,monkeypatch):
    clock=[100];a,r,c=setup(tmp_path,lambda:clock[0]);clock[0]=99
    with pytest.raises(ValueError):a.validate_revision_context(owner=O,binding=B,session=S,project_hash=P,revision_token=c['revision_token'])
    clock[0]=100;monkeypatch.setattr(decision_revisions,'MAX_CHAIN',2)
    child=review(a,revision_token=c['revision_token'],project_hash=P);decide(a,child)
    c=a.revision_context(owner=O,binding=B,session=S,parent_row=child,project_hash=P)
    grand=review(a,revision_token=c['revision_token'],project_hash=P);decide(a,grand)
    c=a.revision_context(owner=O,binding=B,session=S,parent_row=grand,project_hash=P)
    with pytest.raises(ValueError):review(a,revision_token=c['revision_token'],project_hash=P)
    assert a.pending(owner=O,binding=B) is None

def test_real_process_duplicate_writes(tmp_path):
    import json
    a,r,c=setup(tmp_path);review(a,revision_token=c['revision_token'],project_hash=P)
    row=a._revisions().read()[0]
    code='import sys,json; sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]/'src'))+'); from prometheus_assistant.action_approvals import ActionApprovals; a=ActionApprovals(history_root=sys.argv[1]); assert a._revisions().record(json.loads(sys.argv[2])) is False'
    children=[subprocess.Popen([sys.executable,'-B','-c',code,str(tmp_path),json.dumps(row)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    for child in children:
        out,error=child.communicate(timeout=15)
        assert child.returncode==0,error

def test_link_path_rejected(tmp_path):
    a,r,c=setup(tmp_path);target=tmp_path/'elsewhere';target.mkdir()
    try:(tmp_path/'RevisionLinks').symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ValueError):review(a,revision_token=c['revision_token'],project_hash=P)
    assert a.pending(owner=O,binding=B) is None

def test_dashboard_batches_receipts_and_preserves_corruption_checks(tmp_path,monkeypatch):
    from prometheus_assistant.decision_history import DecisionHistory
    a,parent,ctx=setup(tmp_path)
    for _ in range(5):
        child=review(a,revision_token=ctx['revision_token'],project_hash=P)
        decide(a,child)
        ctx=a.revision_context(owner=O,binding=B,session=S,parent_row=child,project_hash=P)
    original=DecisionHistory._rows;calls=[]
    def counted(db):
        calls.append(1);return original(db)
    monkeypatch.setattr(DecisionHistory,'_rows',staticmethod(counted))
    monkeypatch.setattr(DecisionHistory,'lookup',lambda *args:pytest.fail('Per-edge receipt lookup'))
    rows=a.history(owner=O)
    assert len(rows)==6 and len(calls)==2
    assert all(link['child_state']=='recorded' for row in rows for link in row['revisions'])
    with sqlite3.connect(a.decision_history.path) as db:
        db.execute("UPDATE decisions SET payload='{}' WHERE id=?",(parent['id'],))
    with pytest.raises(ValueError):a._revisions().read()
