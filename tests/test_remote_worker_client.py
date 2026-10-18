import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant.remote_worker_client import RemoteWorkerClient,OwnerLedger,configuration
from prometheus_assistant.remote_job_envelope import seal_request
from prometheus_assistant.remote_job_protocol import normalize_request
from prometheus_assistant.remote_job_wire import seal_receipt


def sign(data): return hashlib.sha256(data).digest()*8
def verify(data,signature): return signature==sign(data)


@pytest.fixture
def setup(tmp_path):
    f=SimpleNamespace(root=tmp_path,calls=[],hook=lambda _:None)
    f.config=dict(schema_version=1,enabled=True,account_id='account',device_id='surface',device_name='Surface',
        worker_digest='b'*64,linux_root='/home/user/worker',signing_library=str(tmp_path/'sign.ps1'),
        signing_library_sha256='c'*64,controller_certificate_sha256='d'*64,
        receipt_certificate_sha256=hashlib.sha256(b'cert').hexdigest())
    f.save=lambda:(tmp_path/'worker-target.json').write_text(json.dumps(f.config))
    f.save(); (tmp_path/'receipt.cer').write_bytes(b'cert')
    f.req=normalize_request(dict(schema_version=1,job_id='a'*32,device_id='surface',worker_digest='b'*64,
        operation='python-syntax-check',timeout_seconds=5,files=[dict(path='a.py',content='pass')]))
    f.receipt={**{k:f.req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
        'state':'completed','checks':[dict(path='a.py',passed=True)]}
    f.output=seal_receipt(f.req,f.receipt,sign,issued_at=100,expires_at=200).decode()
    remote=SimpleNamespace(lock=threading.RLock(),account_id='account',generation=1)
    remote._selected_device=lambda:dict(id='surface',name='Surface')
    remote.tools=[dict(name='start_process',inputSchema=dict(type='object',properties={
        'deviceId':dict(type='string'),'command':dict(type='string'),'timeout_ms':dict(type='integer')},
        required=['deviceId','command','timeout_ms'],additionalProperties=False))]
    def request(method,args,**kw):
        name=args['name']; f.calls.append((name,args.get('arguments'))); f.hook(name)
        if name=='who_am_i': return {'structuredContent':{'id':remote.account_id}}
        if name=='list_devices': return {'structuredContent':{'devices':[dict(id='surface',name='Surface',online=True)]}}
        return dict(content=[dict(type='text',text=f.output)])
    remote.client=SimpleNamespace(request=request); f.remote=remote
    f.client=RemoteWorkerClient(remote,root=tmp_path,clock=lambda:150,
        signer_factory=lambda *_:sign,verifier_factory=lambda *_:verify)
    f.envelope=seal_request(f.req,sign,issued_at=100,expires_at=200)
    return f


def test_actual_config_target_dispatch_receipt_auth(setup):
    f=setup
    assert f.client.target()['account_id']=='account'
    assert f.client.dispatch(f.envelope)==f.receipt
    command=f.calls[-1][1]['command']
    assert '/usr/bin/python3 -I' in command and '--query' not in command


@pytest.mark.parametrize('bad',['extra','disabled','path','pin'])
def test_config_fails_closed(setup,bad):
    f=setup
    if bad=='extra': f.config['unknown']=1
    elif bad=='disabled': f.config['enabled']=False
    elif bad=='path': f.config['linux_root']='/home/user/../other'
    else: f.config['worker_digest']='not a digest'
    f.save()
    with pytest.raises(ValueError): configuration(f.root)


@pytest.mark.parametrize('kind',['account','target','config','generation'])
def test_connection_changes_before_dispatch_rejected(setup,kind):
    f=setup
    def hook(name):
        if name!='list_devices': return
        if kind=='account': f.remote.account_id='other'
        elif kind=='target': f.remote._selected_device=lambda:dict(id='other',name='Other')
        elif kind=='config': f.config['linux_root']='/home/user/new'; f.save()
        else: f.remote.generation+=1
    f.hook=hook
    with pytest.raises(ValueError): f.client.dispatch(f.envelope)
    assert not any(name=='start_process' for name,_ in f.calls)


@pytest.mark.parametrize('kind',['unsigned','duplicate','excess','wrongdigest','signature'])
def test_transport_output_never_forges_terminal(setup,kind):
    f=setup
    if kind=='unsigned': f.output=json.dumps(dict(state='completed',receipt=f.receipt))
    elif kind=='duplicate': f.output+='\n'+f.output
    elif kind=='excess': f.output='x'*24001
    else:
        packet=json.loads(f.output)
        if kind=='wrongdigest': packet['payload']['request_digest']='e'*64
        else: packet['signature']='x'*344
        f.output=json.dumps(packet)
    with pytest.raises(ValueError): f.client.dispatch(f.envelope)
    assert sum(name=='start_process' for name,_ in f.calls)==1


def test_owner_ledger_restart_and_query_only_recovery(setup):
    f=setup; authority=f.client.authority_digest(); ledger=OwnerLedger(f.root,'f'*64,authority_digest=authority)
    assert ledger.claim(f.req)['claimed']
    reopened=OwnerLedger(f.root,'f'*64,authority_digest=authority)
    assert reopened.request(f.req['job_id'])==f.req
    assert not reopened.claim(f.req)['claimed']
    assert f.client.recover(f.req,reopened)['receipt']==f.receipt
    assert '--query' in f.calls[-1][1]['command']
    calls=len(f.calls)
    assert f.client.recover(f.req,reopened)['receipt']==f.receipt and len(f.calls)==calls
    with pytest.raises(ValueError): OwnerLedger(f.root,'e'*64).request(f.req['job_id'])


def test_changed_stored_request_rejected(setup):
    f=setup; ledger=OwnerLedger(f.root,'f'*64); ledger.claim(f.req)
    changed=dict(f.req); changed.pop('request_digest'); changed['timeout_seconds']=6
    with pytest.raises(ValueError): ledger.claim(changed)


def test_recovery_must_not_transfer_source_to_changed_account(setup):
    f=setup; ledger=OwnerLedger(f.root,'f'*64,authority_digest=f.client.authority_digest()); ledger.claim(f.req)
    f.config['account_id']='other'; f.remote.account_id='other'; f.save()
    fresh=RemoteWorkerClient(f.remote,root=f.root,clock=lambda:150,
        signer_factory=lambda *_:sign,verifier_factory=lambda *_:verify)
    with pytest.raises(ValueError): fresh.recover(f.req,ledger)
    rebound=OwnerLedger(f.root,'f'*64,authority_digest=fresh.authority_digest())
    with pytest.raises(ValueError): rebound.request(f.req['job_id'])
    with pytest.raises(ValueError): fresh.recover(f.req,rebound)
    assert not f.calls


def test_signing_then_config_change_cannot_dispatch(setup):
    f=setup; f.client.target()
    def factory(*_):
        def signing(data):
            f.config['linux_root']='/home/user/changed'; f.save()
            return sign(data)
        return signing
    f.client.signer_factory=factory
    envelope=seal_request(f.req,f.client.signer,issued_at=100,expires_at=200)
    with pytest.raises(ValueError): f.client.dispatch(envelope)
    assert not f.calls


@pytest.mark.parametrize('field,value',[('controller_certificate_sha256','e'*64),
    ('receipt_certificate_sha256','e'*64),('signing_library_sha256','e'*64),('linux_root','/home/user/new')])
def test_config_epoch_pinned_after_target(setup,field,value):
    f=setup; original=f.client.target()['configuration_digest']
    f.config[field]=value; f.save()
    with pytest.raises(ValueError): f.client.signer(b'ignored')
    fresh=RemoteWorkerClient(f.remote,root=f.root)
    assert fresh.authority_digest()!=original


def resource_response(f, *, mutation=None, observations=None):
    import base64
    from prometheus_assistant.remote_resource_wire import open_query, seal_advertisement
    def response(name):
        if name!='start_process': return
        command=f.calls[-1][1]['command']
        assert command.endswith(' --resources') and '--query' not in command
        data=base64.b64decode(command.split("'")[3])
        query=open_query(data,verify,device_id='surface',worker_digest='b'*64,now=150)
        assert 'files' not in query and 'request' not in query
        f.resource_query=query
        facts=dict(enabled=True,available_ram_gib=4,free_disk_gib=30,cpu_percent=10,battery_percent=90,plugged_in=True)
        if observations is not None: facts.update(observations)
        if mutation=='battery': facts['plugged_in']=False
        if mutation=='unknown': facts['cpu_percent']=None
        if mutation=='nonce': query['nonce']='e'*32
        if mutation=='device': query['device_id']='other'
        packet=seal_advertisement(query,facts,sign,issued_at=150,expires_at=170)
        if mutation=='tamper': packet=packet.replace(b'"cpu_percent":10',b'"cpu_percent":11')
        if mutation=='expired': f.client.clock=lambda:171
        if mutation=='connection': f.remote.generation+=1
        f.output=packet.decode()
    f.hook=response


def test_signed_resource_admission_has_no_source_or_job_claim(setup):
    f=setup;resource_response(f)
    first=f.client.admission(f.req)
    nonce=f.resource_query['nonce']
    assert first['authenticated'] and first['decision']=='ready'
    assert first['observations']['available_ram_gib']==4
    assert f.client.admission()['decision']=='ready' and f.resource_query['nonce']!=nonce
    assert not (f.root/'owners').exists()


@pytest.mark.parametrize('mutation',['nonce','device','tamper','expired','connection'])
def test_untrusted_resource_response_is_not_admitted(setup,mutation):
    f=setup;resource_response(f,mutation=mutation)
    with pytest.raises(ValueError):f.client.admission(f.req)
    assert not (f.root/'owners').exists()
    assert sum(name=='start_process' for name,_ in f.calls)==1


@pytest.mark.parametrize('mutation',['battery','unknown'])
def test_authenticated_but_unsuitable_resources_defer(setup,mutation):
    f=setup;resource_response(f,mutation=mutation)
    report=f.client.admission(f.req)
    assert report['authenticated'] and report['decision']=='defer' and report['reasons']


def test_resource_scope_denied_before_signing(setup,monkeypatch):
    from prometheus_assistant.application_policy import scope_digest
    f=setup
    scope=dict(account='account',device='surface',tool='prometheus.resources')
    monkeypatch.setenv('PROMETHEUS_DENY_SCOPES','remote.dispatch:'+scope_digest(scope))
    f.client.signer=lambda _:pytest.fail('Denied query signed')
    with pytest.raises(ValueError):f.client.resources()
    assert not f.calls


def test_disconnect_can_finish_while_resource_query_waits(setup):
    f=setup;resource_response(f)
    entered=threading.Event();release=threading.Event();disconnected=threading.Event();outcome={}
    original=f.hook
    def hook(name):
        if name=='start_process':
            entered.set();assert release.wait(4)
        original(name)
    f.hook=hook
    def query():
        try:outcome['result']=f.client.admission()
        except Exception as error:outcome['error']=error
    def disconnect():
        with f.remote.lock:
            f.remote.generation+=1;disconnected.set()
    worker=threading.Thread(target=query);worker.start()
    try:
        assert entered.wait(2)
        command=threading.Thread(target=disconnect);command.start()
        assert disconnected.wait(2),'Disconnect was blocked by the observation query'
    finally:
        release.set();worker.join(5)
        if 'command' in locals():command.join(5)
    assert not worker.is_alive() and isinstance(outcome.get('error'),ValueError)
    assert not (f.root/'owners').exists()


def enable_lint(f):
    f.config.update(schema_version=2,lint_profile='ruff-python-v1',
                    lint_tool={'sha256':'e'*64,'version':'0.17.1'})
    f.save()
    raw={k:v for k,v in f.req.items() if k!='request_digest'}
    raw.update(schema_version=2,operation='python-ruff-check-v1',
               profile=f.config['lint_profile'],tool=dict(f.config['lint_tool']))
    f.req=normalize_request(raw)
    f.receipt={**{k:f.req[k] for k in ('schema_version','job_id','device_id','worker_digest',
        'request_digest','operation','profile','tool')}, 'state':'completed',
        'checks':[dict(path='a.py',passed=True)],'status':'passed',
        'diagnostics':[],'reason':'','source_unchanged':True}
    f.envelope=seal_request(f.req,sign,issued_at=100,expires_at=200)
    f.output=seal_receipt(f.req,f.receipt,sign,issued_at=100,expires_at=200).decode()


def test_lint_capability_is_local_pinned_detached_and_absent_from_syntax_config(setup):
    f=setup
    assert f.client.lint_tool() is None and not f.calls
    old_target=f.client.target()
    enable_lint(f)
    with pytest.raises(ValueError): f.client.lint_tool()
    fresh=RemoteWorkerClient(f.remote,root=f.root)
    descriptor=fresh.lint_tool()
    assert descriptor==f.config['lint_tool']
    descriptor['sha256']='f'*64
    assert fresh.lint_tool()==f.config['lint_tool']
    target=fresh.target()
    assert set(target)==set(old_target) and target['configuration_digest']!=old_target['configuration_digest']
    assert not f.calls


@pytest.mark.parametrize('field,value', [
    ('schema_version',1),('schema_version',True),('schema_version',3),
    ('lint_profile','arbitrary'),('lint_tool',{}),
    ('lint_tool',{'sha256':'E'*64,'version':'0.17.1'}),
    ('lint_tool',{'sha256':'e'*64,'version':'ruff 0.17.1'}),
    ('lint_tool',{'sha256':'e'*64,'version':'0.17.1','path':'/usr/bin/ruff'}),
    ('lint_args',['--fix'])])
def test_lint_config_supports_only_exact_provisioned_identity(setup,field,value):
    f=setup;enable_lint(f);f.config[field]=value;f.save()
    with pytest.raises(ValueError): f.client.lint_tool()
    assert not f.calls


def test_lint_dispatch_uses_its_own_scope_and_signed_receipt(setup,monkeypatch):
    from prometheus_assistant.application_policy import scope_digest
    f=setup;enable_lint(f)
    syntax_scope=dict(account='account',device='surface',tool='prometheus.syntax',request_digest=f.req['request_digest'])
    monkeypatch.setenv('PROMETHEUS_DENY_SCOPES','remote.dispatch:'+scope_digest(syntax_scope))
    assert f.client.dispatch(f.envelope)==f.receipt
    assert sum(name=='start_process' for name,_ in f.calls)==1
    assert '--query' not in f.calls[-1][1]['command']


@pytest.mark.parametrize('query',[False,True])
def test_lint_policy_scope_denial_prevents_execution_or_query(setup,monkeypatch,query):
    from prometheus_assistant.application_policy import scope_digest
    f=setup;enable_lint(f)
    scope=dict(account='account',device='surface',tool='prometheus.lint.query' if query else 'prometheus.lint',
               request_digest=f.req['request_digest'])
    monkeypatch.setenv('PROMETHEUS_DENY_SCOPES','remote.dispatch:'+scope_digest(scope))
    with pytest.raises(ValueError): f.client.dispatch(f.envelope,query_only=query)
    assert not any(name=='start_process' for name,_ in f.calls)


@pytest.mark.parametrize('mutation',['old_config','tool_hash','tool_version'])
def test_lint_request_cannot_supply_its_own_capability_before_any_remote_call(setup,mutation):
    f=setup;enable_lint(f)
    if mutation=='old_config':
        f.config['schema_version']=1;f.config.pop('lint_tool');f.config.pop('lint_profile');f.save()
    else:
        raw={k:v for k,v in f.req.items() if k!='request_digest'}
        raw['tool']=dict(raw['tool'])
        raw['tool']['sha256' if mutation=='tool_hash' else 'version']='f'*64 if mutation=='tool_hash' else '0.17.2'
        f.req=normalize_request(raw)
        f.envelope=seal_request(f.req,sign,issued_at=100,expires_at=200)
    with pytest.raises(ValueError): f.client.dispatch(f.envelope)
    with pytest.raises(ValueError): f.client.dispatch(f.envelope,query_only=True)
    with pytest.raises(ValueError): f.client.admission(f.req)
    assert not f.calls


@pytest.mark.parametrize('field,value',[('sha256','f'*64),('version','0.17.2')])
def test_lint_tool_upgrade_changes_authority_and_blocks_previous_review(setup,field,value):
    f=setup;enable_lint(f);previous=f.client.target()['configuration_digest']
    f.config['lint_tool'][field]=value;f.save()
    with pytest.raises(ValueError):f.client.lint_tool()
    with pytest.raises(ValueError):f.client.dispatch(f.envelope)
    assert RemoteWorkerClient(f.remote,root=f.root).authority_digest()!=previous
    assert not f.calls


@pytest.mark.parametrize('observations,expected', [
    ({'available_ram_gib':1.25,'free_disk_gib':.5},'ready'),
    ({'available_ram_gib':1.249},'defer'), ({'free_disk_gib':.499},'defer')])
def test_lint_admission_applies_operation_cost_to_signed_resources(setup,observations,expected):
    f=setup;enable_lint(f);resource_response(f,observations=observations)
    result=f.client.admission(f.req)
    assert result['authenticated'] and result['decision']==expected
    assert not (f.root/'owners').exists()
    assert sum(name=='start_process' for name,_ in f.calls)==1
    # The same capability-bearing worker retains syntax's existing reserve.
    raw={k:v for k,v in f.req.items() if k not in {'request_digest','tool','profile'}}
    raw.update(schema_version=1,operation='python-syntax-check')
    assert f.client.admission(raw)['decision']=='ready'


def test_lint_durable_recovery_reads_original_receipt_once_and_retains_exact_pin(setup):
    f=setup;enable_lint(f)
    ledger=OwnerLedger(f.root,'f'*64,authority_digest=f.client.authority_digest())
    assert ledger.claim(f.req)['claimed']
    reopened=OwnerLedger(f.root,'f'*64,authority_digest=f.client.authority_digest())
    assert reopened.request(f.req['job_id'])==f.req
    assert f.client.recover(f.req,reopened)['receipt']==f.receipt
    assert f.calls[-1][1]['command'].endswith(' --query')
    count=len(f.calls)
    assert f.client.recover(f.req,reopened)['receipt']==f.receipt and len(f.calls)==count
    f.config['lint_tool']['version']='0.17.2';f.save()
    fresh=RemoteWorkerClient(f.remote,root=f.root,clock=lambda:150,
        signer_factory=lambda *_:sign,verifier_factory=lambda *_:verify)
    with pytest.raises(ValueError):fresh.recover(f.req,reopened)
    assert len(f.calls)==count


def test_lint_config_change_during_identity_probe_cannot_dispatch(setup):
    f=setup;enable_lint(f)
    def hook(name):
        if name=='list_devices':
            f.config['lint_tool']['sha256']='f'*64;f.save()
    f.hook=hook
    with pytest.raises(ValueError):f.client.dispatch(f.envelope)
    assert not any(name=='start_process' for name,_ in f.calls)
