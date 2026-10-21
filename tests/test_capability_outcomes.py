from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.capability_outcomes import CapabilityOutcome, CapabilityOutcomes
from prometheus_assistant import capability_outcomes as mod

OWNER='a'*64

def setup(root,kind='runtime_config',decision='approve'):
    app=ActionApprovals(history_root=root)
    row=app.review(owner=OWNER,binding='b'*64,session='c'*32,kind=kind,payload={},review={},policy_revision='p')
    app.consume(owner=OWNER,binding='b'*64,session='c'*32,approval_id=row['id'],fingerprint=row['fingerprint'],policy_revision='p',decision=decision)
    return app,row

def emitter(app,row):
    return CapabilityOutcome(app,OWNER,row,**(dict(job_id='d'*32,request_digest='e'*64) if row['kind'] in mod.ASYNC else {}))

def test_restart_idempotence_and_legacy_bytes(tmp_path):
    app,row=setup(tmp_path);original=(tmp_path/'decisions.sqlite3').read_bytes();e=emitter(app,row)
    assert e.start() is True and e.start() is False
    assert e.observe('settings_saved',{'saved':True})['added']
    assert e.observe('settings_saved',{'saved':True})==dict(recorded=True,added=False,approval_id=row['id'])
    assert e.observe('settings_saved',{'saved':False})['recorded'] is False
    again=CapabilityOutcomes(ActionApprovals(history_root=tmp_path).decision_history)
    assert [r[3]['status'] for r in again.read()]==['started','settings_saved']
    assert (tmp_path/'decisions.sqlite3').read_bytes()==original
    assert not (tmp_path/'OutcomeLinks').exists()

@pytest.mark.parametrize('kind,status,evidence',[('runtime_config','settings_saved',{'saved':True}),('idle_config','settings_saved',{'saved':True}),('integration_access','access_granted',{'granted':True}),('integration','adapter_returned',{'returned':True})])
def test_immediate_kinds(tmp_path,kind,status,evidence):
    app,row=setup(tmp_path,kind);e=emitter(app,row);e.start()
    assert e.observe(status,evidence)['recorded']
    assert not e.observe('access_granted' if kind!='integration_access' else 'settings_saved',{'granted':True})['recorded']

@pytest.mark.parametrize('kind',list(mod.ASYNC))
def test_async_exact_recovery_and_polls(tmp_path,kind):
    app,row=setup(tmp_path,kind);e=emitter(app,row);store=e.store
    assert store.links_for_job(owner=OWNER,job_id='d'*32,request_digest='e'*64)==[]
    e.start()
    for _ in range(25):assert e.observe('waiting',{'waiting':True})['recorded']
    assert len(store.read())==2
    assert e.observe('unconfirmed',{'confirmed':False})['recorded']
    assert store.links_for_job(owner=OWNER,job_id='d'*32,request_digest='e'*64)==[dict(approval_id=row['id'],fingerprint=row['fingerprint'])]
    for owner,job,digest in [('f'*64,'d'*32,'e'*64),(OWNER,'f'*32,'e'*64),(OWNER,'d'*32,'f'*64)]:
        assert store.links_for_job(owner=owner,job_id=job,request_digest=digest)==[]
    recovered=CapabilityOutcome(ActionApprovals(history_root=tmp_path),OWNER,row,job_id='d'*32,request_digest='e'*64)
    assert recovered.start() is False
    counts=dict(planned=1,executed=1,passed=1,failed=0,cancelled=0,timed_out=0)
    assert recovered.observe('checks_passed',{'exit_code':0},counts)['recorded']
    assert not recovered.observe('checks_failed',{'exit_code':1},dict(counts,passed=0,failed=1))['recorded']

@pytest.mark.parametrize('kind,decision',[('runtime_config','reject'),('project_edit','approve'),('other','approve')])
def test_unapproved_kind_refused(tmp_path,kind,decision):
    app,row=setup(tmp_path,kind,decision)
    with pytest.raises(ValueError):emitter(app,row).start()


def test_owner_fingerprint_binding_refusal(tmp_path):
    app,row=setup(tmp_path,'remote_syntax')
    for owner,fp,job,digest in [('f'*64,row['fingerprint'],'d'*32,'e'*64),(OWNER,'f'*64,'d'*32,'e'*64),(OWNER,row['fingerprint'],'','')]:
        with pytest.raises(ValueError):CapabilityOutcome(app,owner,dict(row,fingerprint=fp),job,digest).start()
    app,row=setup(tmp_path,'runtime_config')
    with pytest.raises(ValueError):CapabilityOutcome(app,OWNER,row,'d'*32,'e'*64).start()


def test_unknown_and_checks_require_native_facts(tmp_path):
    app,row=setup(tmp_path,'scheduled_syntax');e=emitter(app,row);e.start()
    assert e.unknown()['recorded']
    assert not e.observe('unknown',{'saved':True})['recorded']
    zero=dict(planned=1,executed=0,passed=0,failed=0,cancelled=0,timed_out=0)
    assert not e.observe('checks_passed',{'exit_code':0},zero)['recorded']
    assert not e.observe('checks_failed',{'exit_code':1},zero)['recorded']
    assert e.observe('checks_failed',{'exit_code':1},dict(zero,executed=1,failed=1))['recorded']


def test_storage_failure_disabled_and_raw_evidence(tmp_path,monkeypatch):
    app,row=setup(tmp_path);e=emitter(app,row)
    def fail(*a,**kw):raise OSError('private storage path')
    with monkeypatch.context() as m:
        m.setattr(e.store,'record',fail)
        with pytest.raises(OSError):e.start()
        assert e.observe('settings_saved',{'saved':True})['recorded'] is False
        assert 'private' not in str(e.unknown())
    e.start()
    assert not e.observe('settings_saved',{'provider':'secret'})['recorded']
    assert not e.observe('settings_saved',{'saved':'secret'})['recorded']
    disabled=CapabilityOutcome(ActionApprovals(),OWNER,row)
    assert disabled.start() is None and disabled.observe('settings_saved') is None and disabled.unknown() is None


def test_corruption_absent_full(tmp_path,monkeypatch):
    app,row=setup(tmp_path);e=emitter(app,row)
    assert e.store.read()==[] and not e.store.root.exists()
    e.start()
    monkeypatch.setattr(mod,'MAX_EVENTS',1)
    assert e.start() is False
    assert not e.observe('settings_saved',{'saved':True})['recorded']
    monkeypatch.setattr(mod,'MAX_EVENTS',65536)
    with closing(sqlite3.connect(e.store.path)) as db,db:db.execute("UPDATE outcomes SET payload='{}'")
    with pytest.raises(ValueError):e.store.read()
    with pytest.raises(ValueError):e.start()


def test_parallel_terminal_writers(tmp_path):
    app,row=setup(tmp_path,'remote_syntax');e=emitter(app,row);e.start()
    code="""import sys,json
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.capability_outcomes import CapabilityOutcome
e=CapabilityOutcome(ActionApprovals(history_root=sys.argv[1]),sys.argv[2],json.loads(sys.argv[3]),'d'*32,'e'*64)
print(e.observe(sys.argv[4],{sys.argv[4]:True})['recorded'])
"""
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    children=[subprocess.Popen([sys.executable,'-c',code,str(tmp_path),OWNER,json.dumps(row),status],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env) for status in ['cancelled','expired']]
    outputs=[p.communicate(timeout=20) for p in children]
    assert sorted(out[0].strip() for out in outputs)==['False','True']
    assert all(p.returncode==0 for p in children)
    assert len(e.store.read())==2


def test_linked_root_refused(tmp_path):
    app,row=setup(tmp_path);target=tmp_path/'target';target.mkdir()
    try:(tmp_path/'CapabilityLinks').symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(ValueError):emitter(app,row)

def test_tool_failure_distinct_from_code_findings(tmp_path):
    app,row=setup(tmp_path,'remote_syntax');e=emitter(app,row);e.start()
    assert not e.observe('tool_failed',{'confirmed':False,'tool_failed':True})['recorded']
    assert e.observe('tool_failed',{'confirmed':True,'tool_failed':True})['recorded']
    assert e.store.read()[-1][3]['checks'] is None


def test_fixed_settings_facts(tmp_path):
    app,row=setup(tmp_path);e=emitter(app,row);e.start()
    assert not e.observe('settings_saved',{'revision':-1})['recorded']
    assert not e.observe('settings_saved',{'cpu':1})['recorded']
    assert e.observe('settings_saved',{'saved':True,'revision':2,'cpu':True})['recorded']


def test_readonly_and_size_guard(tmp_path,monkeypatch):
    app,row=setup(tmp_path);e=emitter(app,row);e.start();real=sqlite3.connect
    def readonly(path,*args,**kwargs):
        if str(path).endswith('CapabilityLinks\\outcomes.sqlite3') or str(path).endswith('CapabilityLinks/outcomes.sqlite3'):
            return real(Path(path).as_uri()+'?mode=ro',uri=True)
        return real(path,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(mod.sqlite3,'connect',readonly)
        assert not e.observe('settings_saved',{'saved':True})['recorded']
    assert len(e.store.read())==1
    with e.store.path.open('ab') as stream:stream.truncate(mod.MAX_DATABASE_BYTES+1)
    with pytest.raises(ValueError):e.store.read()


def test_corrupt_reference_and_kind_fail_explicitly(tmp_path):
    app,row=setup(tmp_path);e=emitter(app,row);e.start()
    with closing(sqlite3.connect(e.store.path)) as db,db:db.execute('UPDATE outcomes SET fingerprint=?',('f'*64,))
    with pytest.raises(ValueError):e.store.read()
    with pytest.raises(ValueError):e.start()

def test_per_decision_capacity_and_event_id_conflict(tmp_path):
    app,row=setup(tmp_path,'scheduled_syntax');e=emitter(app,row);e.start()
    for revision in range(15):assert e.observe('waiting',{'waiting':True,'revision':revision})['recorded']
    assert len(e.store.read())==16
    assert not e.observe('waiting',{'waiting':True,'revision':16})['recorded']
    assert e.observe('waiting',{'waiting':True,'revision':14})['recorded']
    first=e.store.read()[0][3]
    with pytest.raises(ValueError):e.store.record(OWNER,row['id'],row['fingerprint'],dict(first,phase='observed',status='unknown'))


def test_unconfirmed_does_not_invent_start_or_authority(tmp_path):
    app,row=setup(tmp_path,'remote_syntax');e=emitter(app,row)
    assert not e.observe('unconfirmed',{'confirmed':False})['recorded']
    assert e.store.links_for_job(owner=OWNER,job_id='d'*32,request_digest='e'*64)==[]
    assert app.peek(owner=OWNER,binding='b'*64) is None
