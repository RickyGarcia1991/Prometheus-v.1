import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from prometheus_assistant.remote_job_protocol import normalize_request
from prometheus_assistant.remote_job_store import JobStore


def request():
    return normalize_request(dict(schema_version=1,job_id='a'*32,device_id='surface',worker_digest='b'*64,
        operation='python-syntax-check',timeout_seconds=5,
        files=[dict(path='a.py',content='private_source_never_store = 42')]))


def receipt(req, state='completed', passed=True):
    return {**{key:req[key] for key in ('schema_version','job_id','device_id','worker_digest','request_digest')},
            'state':state,'checks':[dict(path='a.py',passed=passed)]}


def test_missing_lookup_and_finish_do_not_create(tmp_path):
    path=tmp_path/'missing'/'jobs.db'; store=JobStore(path); req=request()
    assert store.lookup(req) is None
    with pytest.raises(ValueError): store.finish(req,receipt(req))
    assert not path.parent.exists()


def test_reopen_claim_unconfirmed_and_no_source_storage(tmp_path):
    path=tmp_path/'jobs.db'; req=request()
    assert JobStore(path).claim(req)==dict(claimed=True,state='claimed',receipt=None)
    assert JobStore(path).claim(req)==dict(claimed=False,state='unconfirmed',receipt=None)
    assert JobStore(path).lookup(req)['state']=='unconfirmed'
    assert b'private_source_never_store' not in path.read_bytes()


@pytest.mark.parametrize('state,passed',[('completed',True),('completed',False),('failed',False)])
def test_terminal_replay_idempotence_and_conflicts(tmp_path,state,passed):
    req=request(); store=JobStore(tmp_path/'jobs.db'); store.claim(req)
    row=receipt(req,state,passed)
    result=store.finish(req,row)
    assert result==store.finish(req,copy.deepcopy(row))==store.lookup(req)==store.claim(req)
    assert result['state']==state and result['claimed'] is False
    row['checks'][0]['passed']=not passed
    with pytest.raises(ValueError,match='replaced'): store.finish(req,row)
    assert store.lookup(req)==result


def test_same_id_changed_request_and_unclaimed_id_rejected(tmp_path):
    req=request(); store=JobStore(tmp_path/'jobs.db'); store.claim(req)
    changed=copy.deepcopy(req); del changed['request_digest']; changed['files'][0]['content']='pass'
    for action in (store.claim,store.lookup):
        with pytest.raises(ValueError): action(changed)
    changed=normalize_request(changed)
    with pytest.raises(ValueError): store.finish(changed,receipt(changed))
    changed['job_id']='c'*32; del changed['request_digest']; changed=normalize_request(changed)
    with pytest.raises(ValueError): store.finish(changed,receipt(changed))


@pytest.mark.parametrize('mutation',['missing','digest','duplicate','running','deferred','extra','bool'])
def test_malformed_receipts_preserve_unconfirmed(tmp_path,mutation):
    req=request(); store=JobStore(tmp_path/'jobs.db'); store.claim(req); row=receipt(req)
    if mutation=='missing': row['checks']=[]
    elif mutation=='digest': row['request_digest']='c'*64
    elif mutation=='duplicate': row['checks']*=2
    elif mutation in ('running','deferred'): row['state']=mutation; row['checks'][0]['passed']=False
    elif mutation=='extra': row['extra']='untrusted'
    elif mutation=='bool': row['checks'][0]['passed']=1
    with pytest.raises(ValueError): store.finish(req,row)
    assert store.lookup(req)['state']=='unconfirmed'


@pytest.mark.parametrize('corruption',['bytes','version','receipt','schema'])
def test_corruption_never_resets_claim(tmp_path,corruption):
    path=tmp_path/'jobs.db'; store=JobStore(path); req=request(); store.claim(req)
    if corruption=='bytes': path.write_bytes(b'not sqlite')
    else:
        with sqlite3.connect(path) as db:
            if corruption=='version': db.execute('PRAGMA user_version=999')
            elif corruption=='receipt': db.execute("UPDATE jobs SET receipt='{}'")
            else: db.execute('DROP TABLE jobs')
    before=path.read_bytes()
    for action in (store.lookup,store.claim):
        with pytest.raises(ValueError): action(req)
    assert path.read_bytes()==before


def test_absolute_path_and_bounds(tmp_path,monkeypatch):
    from prometheus_assistant import remote_job_store as module
    with pytest.raises(ValueError): JobStore('relative.db')
    path=tmp_path/'jobs.db'; store=JobStore(path); store.claim(request())
    monkeypatch.setattr(module,'MAX_CLAIMS',1)
    new=request(); del new['request_digest']; new['job_id']='c'*32
    with pytest.raises(ValueError,match='capacity'): store.claim(new)
    monkeypatch.setattr(module,'MAX_DATABASE_BYTES',10)
    with pytest.raises(ValueError): store.lookup(request())


def test_receipt_capacity_failure_preserves_claim(tmp_path,monkeypatch):
    from prometheus_assistant import remote_job_store as module
    req=request(); store=JobStore(tmp_path/'jobs.db'); store.claim(req)
    monkeypatch.setattr(module,'MAX_RECEIPT_BYTES',32)
    with pytest.raises(ValueError,match='limit'): store.finish(req,receipt(req))
    assert store.lookup(req)['state']=='unconfirmed'


def test_terminal_failure_cannot_be_replaced_with_success(tmp_path):
    req=request(); store=JobStore(tmp_path/'jobs.db'); store.claim(req)
    failure=store.finish(req,receipt(req,'failed',False))
    with pytest.raises(ValueError,match='replaced'): store.finish(req,receipt(req))
    assert store.claim(req)==failure


def test_database_growth_cap_rolls_back_ungranted_claim(tmp_path,monkeypatch):
    from prometheus_assistant import remote_job_store as module
    path=tmp_path/'jobs.db'; store=JobStore(path); store.claim(request())
    monkeypatch.setattr(module,'MAX_DATABASE_BYTES',path.stat().st_size)
    rejected=None
    for number in range(1,100):
        req=request(); del req['request_digest']; req['job_id']=f'{number:032x}'
        try: store.claim(req)
        except ValueError:
            rejected=req
            break
    assert rejected is not None
    assert store.lookup(rejected) is None
    assert path.stat().st_size <= module.MAX_DATABASE_BYTES


def test_linked_sidecar_or_parent_rejected(tmp_path):
    target=tmp_path/'target'; target.mkdir(); link=tmp_path/'link'
    try: link.symlink_to(target,target_is_directory=True)
    except OSError: pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(ValueError): JobStore(link/'jobs.db')
    sidecar=tmp_path/'jobs.db-journal'; sidecar.symlink_to(target/'journal')
    with pytest.raises(ValueError): JobStore(tmp_path/'jobs.db')


@pytest.mark.parametrize('existing',[False,True])
def test_independent_process_claims_have_one_winner_and_crash_leaves_unconfirmed(tmp_path,existing):
    path=tmp_path/'jobs.db'; req=request()
    if existing:
        other=copy.deepcopy(req); del other['request_digest']; other['job_id']='c'*32
        JobStore(path).claim(other)
    code='''import json,os,sys
from prometheus_assistant.remote_job_store import JobStore
req=json.loads(sys.stdin.readline())
result=JobStore(sys.argv[1]).claim(req)
print(json.dumps(result),flush=True)
os._exit(0)
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    processes=[]
    try:
        for _ in range(2):
            processes.append(subprocess.Popen([sys.executable,'-B','-c',code,str(path)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        for p in processes: p.stdin.write((json.dumps(req)+'\n').encode()); p.stdin.flush()
        results=[]
        for p in processes:
            output,error=p.communicate(timeout=10)
            assert p.returncode==0,error
            results.append(json.loads(output))
        assert sum(row['claimed'] for row in results)==1
        assert JobStore(path).lookup(req)==dict(claimed=False,state='unconfirmed',receipt=None)
    finally:
        for p in processes:
            if p.poll() is None: p.kill()
            p.wait(timeout=5)
