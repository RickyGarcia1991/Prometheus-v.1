import http.client
import json
import secrets
import sqlite3
import threading
import time
from contextlib import closing
import pytest
from prometheus_assistant.ui_server import Interface, Server
from prometheus_assistant.scoped_grants import GrantStore
from prometheus_assistant.tool_registry import ToolRegistry,ToolSpec,ArgSpec


@pytest.fixture
def endpoint(tmp_path,monkeypatch):
    from prometheus_assistant import builtin_tools
    effects=[]
    registry=ToolRegistry([ToolSpec('project','tests','fixture execution',lambda r:effects.append(dict(r.arguments)) or 'fixture complete',{'target':ArgSpec()},command_execution=True)])
    monkeypatch.setattr(builtin_tools,'build_builtin_registry',lambda *a,**k:registry)
    app=Interface(tmp_path/'memory.db')
    app.grants=GrantStore(tmp_path/'authority/grants.db',host=lambda:'fixture-host')
    server=Server(('127.0.0.1',0),app)
    gate=server.owner_access;gate.required=gate.ready=True
    gate.path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(gate.path)) as db,db:
        db.execute('CREATE TABLE credentials(id BLOB PRIMARY KEY,data BLOB,counter INTEGER)')
        db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value BLOB)')
        db.execute('INSERT INTO credentials VALUES (?,?,?)',(b'synthetic',b'public-key-fixture',0))
        db.execute('INSERT INTO settings VALUES (?,?)',('user_id',b'synthetic-owner'))
    cookie='fixture-cookie';gate.sessions[cookie]=gate.clock()+120
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(route,body=None,*,owner=True,token=True,origin=None):
        headers={'Host':server.owner_origin.removeprefix('http://'),'Origin':origin or server.owner_origin,'Content-Type':'application/json'}
        if token:headers['X-Prometheus-Token']=app.token
        if owner:headers['Cookie']='prometheus_owner='+cookie
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            connection.request('GET' if body is None else 'POST',route,None if body is None else json.dumps(body),headers)
            result=connection.getresponse();return result.status,json.loads(result.read())
        finally:connection.close()
    yield app,gate,effects,request
    server.shutdown();server.server_close();thread.join(3);app.close()


def create(request):
    body={'worker':'project','tool':'tests','arguments':{'target':'tests'},'seconds':60,'uses':1}
    status,review=request('/api/permissions/grants/review',body);assert status==200,review
    status,grant=request('/api/permissions/grants/issue',{'review_id':review['review_id'],'scope_sha256':review['scope_sha256']});assert status==200,grant
    return grant


def test_http_exact_review_and_job_dispatch_keep_all_gates(endpoint):
    app,gate,effects,request=endpoint
    body={'worker':'project','tool':'tests','arguments':{'target':'tests'},'seconds':60,'uses':1}
    for kwargs,code in [({'owner':False},401),({'token':False},403),({'origin':'https://foreign.example'},403)]:
        assert request('/api/permissions/grants/review',body,**kwargs)[0]==code
    assert request('/api/permissions/grants/review',{**body,'owner':'spoof'})[0]==400
    grant=create(request)
    assert not effects
    job={'mode':'core','worker':'project','tool':'tests','arguments':{'target':'tests'},'grant_id':grant['id'],'invocation_id':secrets.token_hex(16)}
    assert request('/api/jobs',{**job,'approved':True})[0]==400
    status,queued=request('/api/jobs',job);assert status==202
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        _,row=request('/api/job/'+queued['id'])
        if row['state']!='running':break
        time.sleep(.01)
    assert row['state']=='done',row
    assert effects==[{'target':'tests'}]
    assert row['result']['reply']=='fixture complete'
    # Replay through the actual queue cannot execute a second effect.
    status,queued=request('/api/jobs',job);assert status==202
    while time.monotonic()<deadline:
        _,row=request('/api/job/'+queued['id'])
        if row['state']!='running':break
        time.sleep(.01)
    assert row['state']=='error'
    assert len(effects)==1


def test_unconfigured_owner_cannot_mint_and_lock_prevents_grant_use(endpoint):
    app,gate,effects,request=endpoint
    gate.required=False
    assert request('/api/permissions/grants')[0]==400
    gate.required=True
    grant=create(request)
    assert request('/api/permissions/grants/revoke',{'id':grant['id']})[0]==200
    assert request('/api/owner/lock',{})[0]==200
    assert request('/api/permissions/grants')[0]==401
    assert effects==[]
