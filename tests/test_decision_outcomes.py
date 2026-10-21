import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing
import pytest
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.decision_history import DecisionHistory
from prometheus_assistant import decision_outcomes as mod

OWNER='a'*64

def approved(root,decision='approve',kind='project_edit'):
    app=ActionApprovals(history_root=root)
    p=app.review(owner=OWNER,binding='b'*64,session='c'*32,kind=kind,payload={},review={},policy_revision='p')
    app.consume(owner=OWNER,binding='b'*64,session='c'*32,approval_id=p['id'],fingerprint=p['fingerprint'],policy_revision='p',decision=decision)
    return app,p

def event(phase='started',n=1,**kw):
    row=dict(event_id=f'{n:032x}',phase=phase,observed_at=100+n,project_hash='d'*64,status='started',edit_id='',checks=None,rollback='unknown',evidence_sha256=None)
    if phase=='result':row.update(status='passed',edit_id='e'*32,evidence_sha256='f'*64,rollback='not_attempted',checks=dict(planned=1,executed=1,passed=1,failed=0,cancelled=0,timed_out=0))
    if phase.startswith('undo'):row['edit_id']='e'*32
    if phase=='undo_result':row.update(status='restored',rollback='restored',evidence_sha256='f'*64)
    return dict(row,**kw)

def put(app,p,e,**kw):return app.record_outcome(owner=OWNER,approval_id=p['id'],fingerprint=p['fingerprint'],event=e,**kw)

def test_reopen_and_rollback_readable(tmp_path):
    app,p=approved(tmp_path);before=(tmp_path/'decisions.sqlite3').read_bytes()
    assert put(app,p,event());assert not put(app,p,event())
    assert put(app,p,event('result',2));assert put(app,p,event('undo_started',3));assert put(app,p,event('undo_result',4))
    assert (tmp_path/'decisions.sqlite3').read_bytes()==before
    assert len(DecisionHistory(tmp_path).history(OWNER))==1
    reopened=ActionApprovals(history_root=tmp_path)
    assert len(reopened.history(owner=OWNER)[0]['outcomes'])==4
    assert reopened.outcomes_for_edit(owner=OWNER,project_hash='d'*64,edit_id='e'*32)==[{'approval_id':p['id'],'fingerprint':p['fingerprint']}]
    assert reopened.outcomes_for_edit(owner='b'*64,project_hash='d'*64,edit_id='e'*32)==[]
    assert reopened.peek(owner=OWNER,binding='b'*64) is None

@pytest.mark.parametrize('decision,kind',[('reject','project_edit'),('approve','runtime_config')])
def test_nonapproved_rejected(tmp_path,decision,kind):
    app,p=approved(tmp_path,decision,kind)
    with pytest.raises(ValueError):put(app,p,event())
    assert not (tmp_path/'OutcomeLinks').exists()

def test_owner_fingerprint_missing(tmp_path):
    app,p=approved(tmp_path)
    for owner,ident,fingerprint in [('b'*64,p['id'],p['fingerprint']),(OWNER,'0'*32,p['fingerprint']),(OWNER,p['id'],'0'*64)]:
        with pytest.raises(ValueError):app.record_outcome(owner=owner,approval_id=ident,fingerprint=fingerprint,event=event())

@pytest.mark.parametrize('bad',[{'extra':'raw'}, {'phase':'execute'}, {'checks':{}}, {'observed_at':float('nan')},{'edit_id':'/private/path'},{'evidence_sha256':'f'*64},{'status':'passed'}])
def test_bad_start(tmp_path,bad):
    app,p=approved(tmp_path)
    with pytest.raises(ValueError):put(app,p,event(**bad))

def test_sequences_and_unknown(tmp_path):
    app,p=approved(tmp_path)
    with pytest.raises(ValueError):put(app,p,event('result',2))
    put(app,p,event())
    with pytest.raises(ValueError):put(app,p,event(n=2))
    with pytest.raises(ValueError):put(app,p,event('result',2,project_hash='0'*64))
    with pytest.raises(ValueError):put(app,p,event('result',2,status='unknown'))
    put(app,p,event('result',2,status='unknown',edit_id='',evidence_sha256=None,checks=None,rollback='unknown'))
    with pytest.raises(ValueError):put(app,p,event('undo_started',3))

def test_check_counts(tmp_path):
    app,p=approved(tmp_path);put(app,p,event())
    checks=dict(planned=3,executed=2,passed=1,failed=0,cancelled=0,timed_out=1)
    with pytest.raises(ValueError):put(app,p,event('result',2,checks=dict(checks,executed=3)))
    put(app,p,event('result',2,checks=checks,status='failed'))

def test_capacity_and_conflict(tmp_path,monkeypatch):
    app,p=approved(tmp_path);put(app,p,event())
    with pytest.raises(ValueError):put(app,p,event(observed_at=200))
    monkeypatch.setattr(mod,'MAX_EVENTS',1)
    assert not put(app,p,event())
    with pytest.raises(ValueError):put(app,p,event('result',2))
    monkeypatch.setattr(mod,'MAX_EVENTS',65536)
    put(app,p,event('result',2))
    for n in range(3,17):put(app,p,event('undo_started' if n%2 else 'undo_result',n))
    with pytest.raises(ValueError):put(app,p,event('undo_started',17))

def test_absent_and_corruption(tmp_path):
    app,p=approved(tmp_path)
    assert app.history(owner=OWNER)[0]['outcomes']==[]
    assert not (tmp_path/'OutcomeLinks').exists()
    put(app,p,event());path=tmp_path/'OutcomeLinks/outcomes.sqlite3'
    with closing(sqlite3.connect(path)) as db,db:db.execute("UPDATE outcomes SET payload='{}'")
    with pytest.raises(ValueError):app.history(owner=OWNER)
    with pytest.raises(ValueError):put(app,p,event('result',2))

def test_lookup_beyond_recent(tmp_path):
    app,p=approved(tmp_path)
    for _ in range(51):approved(tmp_path)
    assert p['id'] not in [r['id'] for r in app.history(owner=OWNER)]
    assert put(app,p,event())

def test_native_parallel_terminal_and_reopen(tmp_path):
    app,p=approved(tmp_path);put(app,p,event())
    code="""import json,sys
from prometheus_assistant.action_approvals import ActionApprovals
app=ActionApprovals(history_root=sys.argv[1])
try:
 print(app.record_outcome(owner=sys.argv[2],approval_id=sys.argv[3],fingerprint=sys.argv[4],event=json.loads(sys.argv[5])))
except ValueError:print('conflict')
"""
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    ps=[subprocess.Popen([sys.executable,'-c',code,str(tmp_path),OWNER,p['id'],p['fingerprint'],json.dumps(event('result',n))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env) for n in (2,3)]
    outputs=[p.communicate(timeout=20) for p in ps]
    assert sorted(o[0].strip() for o in outputs)==['True','conflict'];assert all(p.returncode==0 for p in ps)
    assert len(ActionApprovals(history_root=tmp_path).history(owner=OWNER)[0]['outcomes'])==2

def test_symlink_refused(tmp_path):
    app,p=approved(tmp_path);target=tmp_path/'target';target.mkdir()
    try:(tmp_path/'OutcomeLinks').symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ValueError):put(app,p,event())

def test_readonly_storage_failure_and_size(tmp_path,monkeypatch):
    app,p=approved(tmp_path);put(app,p,event())
    real=sqlite3.connect
    def readonly(path,*a,**kw):
        if str(path).endswith('outcomes.sqlite3'):
            return real(Path(path).as_uri()+'?mode=ro',uri=True)
        return real(path,*a,**kw)
    with monkeypatch.context() as m:
        m.setattr(mod.sqlite3,'connect',readonly)
        with pytest.raises(ValueError):put(app,p,event('result',2))
    assert len(app.history(owner=OWNER)[0]['outcomes'])==1
    path=tmp_path/'OutcomeLinks/outcomes.sqlite3'
    with path.open('ab') as stream:stream.truncate(32*1024*1024+1)
    with pytest.raises(ValueError):app.history(owner=OWNER)

def test_corrupt_reference_explicit(tmp_path):
    app,p=approved(tmp_path);put(app,p,event())
    with closing(sqlite3.connect(tmp_path/'OutcomeLinks/outcomes.sqlite3')) as db,db:
        db.execute('UPDATE outcomes SET fingerprint=?',('0'*64,))
    with pytest.raises(ValueError):app.history(owner=OWNER)

@pytest.mark.parametrize('bad',[{'checks':None},{'rollback':'restored'},{'checks':dict(planned=0,executed=0,passed=0,failed=0,cancelled=0,timed_out=0)},{'checks':dict(planned=2,executed=1,passed=1,failed=0,cancelled=0,timed_out=0)},{'checks':dict(planned=1,executed=1,passed=0,failed=1,cancelled=0,timed_out=0)}])
def test_passed_cannot_contradict_checks(tmp_path,bad):
    app,p=approved(tmp_path);put(app,p,event())
    with pytest.raises(ValueError):put(app,p,event('result',2,**bad))
