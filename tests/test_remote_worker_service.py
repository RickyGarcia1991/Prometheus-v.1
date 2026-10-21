import hashlib
import json

import pytest

from prometheus_assistant import remote_worker_service as service
from prometheus_assistant.remote_job_envelope import seal_request
from prometheus_assistant.remote_job_wire import encode_request,open_receipt
from prometheus_assistant.remote_job_protocol import normalize_request


def sign(data): return hashlib.sha256(data).digest()*8
def verify(data,signature): return signature==sign(data)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    config=dict(schema_version=1,enabled=True,device_id='surface',worker_digest='b'*64,
                controller_certificate_sha256='c'*64,receipt_certificate_sha256='d'*64)
    path=tmp_path/'worker-config.json'; path.write_text(json.dumps(config))
    (tmp_path/'controller.cer').write_bytes(b'fixture')
    request=normalize_request(dict(schema_version=1,job_id='a'*32,device_id='surface',worker_digest='b'*64,
        operation='python-syntax-check',timeout_seconds=5,files=[dict(path='a.py',content='pass')]))
    wire=encode_request(seal_request(request,sign,issued_at=100,expires_at=200))
    monkeypatch.setattr(service,'certificate_verifier',lambda *_args,**_kw:verify)
    monkeypatch.setattr(service,'_receipt_signer',lambda *_:sign)
    monkeypatch.setattr(service.sys,'platform','linux')
    calls=[]
    from prometheus_assistant import remote_syntax_worker
    def execute(req):
        calls.append(req)
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
                'state':'completed','checks':[dict(path='a.py',passed=True)]}
    monkeypatch.setattr(remote_syntax_worker,'execute_linux_syntax',execute)
    observe=lambda:dict(observed_at=150,available_ram_gib=1,free_disk_gib=1,cpu_percent=1,plugged_in=True)
    return tmp_path,config,path,request,wire,calls,observe


def run(f,**kw):
    root,config,path,req,wire,calls,observe=f
    return service.process_packet(wire,root,clock=lambda:150,observe=observe,**kw)


def test_real_ledger_execution_query_and_replay(setup):
    root,_,_,req,_,calls,_=setup
    assert json.loads(run(setup,query_only=True))['state']=='unconfirmed'
    assert not (root/'jobs.sqlite3').exists()
    for query in (False,False,True):
        result=open_receipt(req,run(setup,query_only=query),verify,device_id='surface',worker_digest='b'*64,now=150)
        assert result['state']=='completed'
    assert len(calls)==1


def test_local_disable_reloaded_and_identity_not_observer_controlled(setup):
    root,config,path,req,wire,calls,observe=setup
    config['enabled']=False; path.write_text(json.dumps(config))
    assert json.loads(run(setup))['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()
    config['enabled']=True; path.write_text(json.dumps(config))
    forged=lambda:{**observe(),'enabled':True,'device_id':'attacker','worker_digest':'e'*64}
    result=service.process_packet(wire,root,clock=lambda:150,observe=forged)
    assert open_receipt(req,result,verify,device_id='surface',worker_digest='b'*64,now=150)


@pytest.mark.parametrize('bad',['duplicate','oversize','fields','bool','pin'])
def test_bad_config_no_claim(setup,bad):
    root,config,path,_,_,calls,_=setup
    if bad=='duplicate': path.write_text('{"schema_version":1,"schema_version":1}')
    elif bad=='oversize': path.write_bytes(b' '*4097)
    else:
        config[{'fields':'extra','bool':'enabled','pin':'worker_digest'}[bad]]=1
        path.write_text(json.dumps(config))
    assert json.loads(run(setup))['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_invalid_crypto_material_and_input_sanitized(setup,monkeypatch):
    root,_,_,_,wire,calls,_=setup
    def fail(*_args,**_kwargs): raise ValueError('PRIVATE KEY SECRET DETAIL')
    monkeypatch.setattr(service,'_receipt_signer',fail)
    output=run(setup)
    assert b'SECRET' not in output and json.loads(output)['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()
    assert json.loads(service.process_packet(b'x'*3200000,root))['state']=='rejected'


def test_unconfirmed_process_failure_never_retried(setup,monkeypatch):
    from prometheus_assistant import remote_syntax_worker
    calls=[]
    def fail(req): calls.append(req); raise RuntimeError('failed')
    monkeypatch.setattr(remote_syntax_worker,'execute_linux_syntax',fail)
    assert json.loads(run(setup))['state']=='unconfirmed'
    assert json.loads(run(setup))['state']=='unconfirmed'
    assert json.loads(run(setup,query_only=True))['state']=='unconfirmed'
    assert len(calls)==1


def test_nonlinux_and_unknown_power_defer_before_claim(setup,monkeypatch):
    root,_,_,_,wire,calls,observe=setup
    result=service.process_packet(wire,root,clock=lambda:150,observe=lambda:{**observe(),'plugged_in':None})
    assert json.loads(result)['state']=='deferred'
    monkeypatch.setattr(service.sys,'platform','win32')
    assert json.loads(run(setup))['state']=='deferred'
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_changed_local_config_before_admission_refuses(setup,monkeypatch):
    root,config,path,_,_,calls,_=setup
    def signer(*_):
        config['enabled']=False; path.write_text(json.dumps(config)); return sign
    monkeypatch.setattr(service,'_receipt_signer',signer)
    assert json.loads(run(setup))['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_link_and_relative_root_rejected(setup):
    root,_,_,_,wire,_,_=setup
    assert json.loads(service.process_packet(wire,'relative'))['state']=='rejected'
    link=root/'linked'
    try: link.symlink_to(root,target_is_directory=True)
    except OSError: pytest.skip('Windows symlink privilege unavailable')
    assert json.loads(service.process_packet(wire,link))['state']=='rejected'


def test_native_observation_delta_and_missing_power(tmp_path,monkeypatch):
    import io
    import builtins
    real=builtins.open
    monkeypatch.setattr(builtins,'open',lambda path,*a,**kw:io.BytesIO(b'MemAvailable: 1048576 kB\n') if path=='/proc/meminfo' else real(path,*a,**kw))
    sequence=iter([(100,40),(200,70)])
    monkeypatch.setattr(service,'_cpu',lambda:next(sequence))
    monkeypatch.setattr(service.time,'sleep',lambda _:None)
    monkeypatch.setattr(service,'read_power',lambda:(100,None))
    facts=service._observe(tmp_path,lambda:150)
    assert facts['cpu_percent']==70 and facts['available_ram_gib']==1
    assert facts['plugged_in'] is None and facts['free_disk_gib']>0
    assert facts['battery_percent']==100


def test_cpu_counter_reset_stays_unknown(tmp_path,monkeypatch):
    sequence=iter([(200,70),(100,40)])
    monkeypatch.setattr(service,'_cpu',lambda:next(sequence))
    monkeypatch.setattr(service.time,'sleep',lambda _:None)
    monkeypatch.setattr(service,'read_power',lambda:(None,None))
    assert service._observe(tmp_path,lambda:150)['cpu_percent'] is None


def test_native_crypto_key_certificate_matching(tmp_path):
    cryptography=pytest.importorskip('cryptography')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime,timedelta,timezone
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    now=datetime.now(timezone.utc)
    name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,'test')])
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256())
    der=cert.public_bytes(serialization.Encoding.DER)
    (tmp_path/'receipt.cer').write_bytes(der)
    (tmp_path/'receipt-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    signer=service._receipt_signer(tmp_path,{'receipt_certificate_sha256':hashlib.sha256(der).hexdigest()},lambda:now.timestamp())
    assert len(signer(b'qualification'))==256
    (tmp_path/'receipt-key.pem').write_bytes(b'bad')
    with pytest.raises(ValueError): service._receipt_signer(tmp_path,{'receipt_certificate_sha256':hashlib.sha256(der).hexdigest()},lambda:now.timestamp())


def test_resource_query_has_no_ledger_or_execution_even_if_disabled(setup,monkeypatch):
    from prometheus_assistant.remote_resource_wire import seal_query,open_advertisement
    root,config,path,_,_,calls,observe=setup
    def forbidden(*_a,**_kw): raise AssertionError('Resources must not open the job ledger')
    monkeypatch.setattr(service,'JobStore',forbidden)
    wire=seal_query(sign,nonce='f'*32,device_id='surface',worker_digest='b'*64,issued_at=140,expires_at=170)
    for enabled in (True,False):
        config['enabled']=enabled;path.write_text(json.dumps(config))
        result=service.process_resource_query(wire,root,clock=lambda:150,
            observe=lambda:{**observe(),'enabled':True,'battery_percent':80})
        payload=open_advertisement(result,verify,nonce='f'*32,device_id='surface',worker_digest='b'*64,now=150)
        assert payload['observations']==dict(enabled=enabled,available_ram_gib=1,free_disk_gib=1,
            cpu_percent=1,battery_percent=80,plugged_in=True)
        assert payload['expires_at']==170
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_resource_query_missing_telemetry_is_unknown(setup):
    from prometheus_assistant.remote_resource_wire import seal_query,open_advertisement
    root,_,_,_,_,calls,_=setup
    wire=seal_query(sign,nonce='f'*32,device_id='surface',worker_digest='b'*64,issued_at=140,expires_at=170)
    result=service.process_resource_query(wire,root,clock=lambda:150,observe=lambda:{})
    facts=open_advertisement(result,verify,nonce='f'*32,device_id='surface',worker_digest='b'*64,now=150)['observations']
    assert facts['enabled'] is True
    assert all(value is None for name,value in facts.items() if name!='enabled')
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_resource_query_expiring_during_observation_is_not_renewed(setup):
    from prometheus_assistant.remote_resource_wire import seal_query
    root,_,_,_,_,calls,observe=setup
    wire=seal_query(sign,nonce='f'*32,device_id='surface',worker_digest='b'*64,issued_at=140,expires_at=170)
    clock=iter((150,170))
    result=service.process_resource_query(wire,root,clock=lambda:next(clock),observe=observe)
    assert json.loads(result)['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_resource_signer_cannot_change_config_before_response(setup,monkeypatch):
    from prometheus_assistant.remote_resource_wire import seal_query
    root,config,path,_,_,calls,observe=setup
    wire=seal_query(sign,nonce='f'*32,device_id='surface',worker_digest='b'*64,issued_at=140,expires_at=170)
    def signer(*_):
        config['enabled']=False;path.write_text(json.dumps(config));return sign
    monkeypatch.setattr(service,'_receipt_signer',signer)
    result=service.process_resource_query(wire,root,clock=lambda:150,observe=observe)
    assert json.loads(result)['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()


@pytest.mark.parametrize('change',['expired','future','target','tampered','syntax-packet','config','telemetry'])
def test_rejected_resource_queries_never_execute_or_claim(setup,change):
    from prometheus_assistant.remote_resource_wire import seal_query
    root,config,path,_,syntax_wire,calls,observe=setup
    args=dict(nonce='f'*32,device_id='surface',worker_digest='b'*64,issued_at=140,expires_at=170)
    if change=='expired': args.update(issued_at=120,expires_at=150)
    elif change=='future': args.update(issued_at=151,expires_at=170)
    elif change=='target': args['device_id']='other'
    wire=seal_query(sign,**args)
    if change=='tampered': wire=wire.replace(b'"issued_at":140',b'"issued_at":141')
    elif change=='syntax-packet': wire=syntax_wire
    def observations():
        if change=='config': config['enabled']=False;path.write_text(json.dumps(config))
        return {**observe(),'cpu_percent':float('nan')} if change=='telemetry' else observe()
    result=service.process_resource_query(wire,root,clock=lambda:150,observe=observations)
    assert json.loads(result)['state']=='rejected'
    assert not calls and not (root/'jobs.sqlite3').exists()


def test_native_resource_query_signature_and_pin_roundtrip(tmp_path):
    pytest.importorskip('cryptography')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa,padding
    from datetime import datetime,timedelta,timezone
    from prometheus_assistant.remote_job_crypto import certificate_verifier
    from prometheus_assistant.remote_resource_wire import seal_query,open_advertisement
    now=datetime.now(timezone.utc);issued=int(now.timestamp())
    def identity(label):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,label)])
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256())
        return key,cert.public_bytes(serialization.Encoding.DER)
    controller,controller_der=identity('controller');receipt,receipt_der=identity('worker')
    config=dict(schema_version=1,enabled=True,device_id='surface',worker_digest='b'*64,
        controller_certificate_sha256=hashlib.sha256(controller_der).hexdigest(),
        receipt_certificate_sha256=hashlib.sha256(receipt_der).hexdigest())
    (tmp_path/'worker-config.json').write_text(json.dumps(config))
    (tmp_path/'controller.cer').write_bytes(controller_der)
    (tmp_path/'receipt.cer').write_bytes(receipt_der)
    (tmp_path/'receipt-key.pem').write_bytes(receipt.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    signer=lambda data:controller.sign(data,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
    wire=seal_query(signer,nonce='f'*32,device_id='surface',worker_digest='b'*64,
        issued_at=issued,expires_at=issued+30)
    facts=dict(available_ram_gib=4,free_disk_gib=25,cpu_percent=20,battery_percent=50,plugged_in=True)
    output=service.process_resource_query(wire,tmp_path,clock=lambda:issued,observe=lambda:facts)
    pinned=certificate_verifier(receipt_der,config['receipt_certificate_sha256'],now=lambda:now)
    result=open_advertisement(output,pinned,nonce='f'*32,device_id='surface',worker_digest='b'*64,now=issued)
    assert result['observations']=={**facts,'enabled':True}
    with pytest.raises(ValueError): open_advertisement(output.replace(b'"cpu_percent":20',b'"cpu_percent":10'),
        pinned,nonce='f'*32,device_id='surface',worker_digest='b'*64,now=issued)
    config['controller_certificate_sha256']='c'*64
    (tmp_path/'worker-config.json').write_text(json.dumps(config))
    assert json.loads(service.process_resource_query(wire,tmp_path,clock=lambda:issued,observe=lambda:facts))['state']=='rejected'
    assert not (tmp_path/'jobs.sqlite3').exists()
