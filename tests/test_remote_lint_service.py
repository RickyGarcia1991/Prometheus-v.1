"""Real ephemeral signatures and durable service flow; Linux execution mocked.

The actual pinned Ruff primitive is qualified separately on Linux. No enrolled
certificate, production worker configuration or network service is used here.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from prometheus_assistant import remote_lint_runner, remote_worker_service as service
from prometheus_assistant.remote_job_envelope import seal_request
from prometheus_assistant.remote_job_protocol import normalize_request, validate_receipt
from prometheus_assistant.remote_job_store import JobStore
from prometheus_assistant.remote_job_wire import encode_request, open_receipt


@pytest.fixture
def signed_lint(tmp_path, monkeypatch):
    pytest.importorskip('cryptography')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from prometheus_assistant.remote_job_crypto import certificate_verifier
    now=datetime.now(timezone.utc); issued=int(now.timestamp())
    def identity(label):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,label)])
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
              .sign(key,hashes.SHA256()))
        return key,cert.public_bytes(serialization.Encoding.DER)
    controller,controller_der=identity('ephemeral-lint-controller')
    worker,worker_der=identity('ephemeral-lint-worker')
    builder_path=Path(__file__).resolve().parents[1]/'tools/build-lint-worker.py'
    spec=importlib.util.spec_from_file_location('signed_lint_service_builder',builder_path)
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    binary=tmp_path/'inert-ruff';binary.write_bytes(b'\x7fELFinert-unit-fixture')
    root=tmp_path/'worker';built=builder.build(root,binary,'0.17.1')
    config=dict(schema_version=1,enabled=True,device_id='surface',worker_digest=built['worker_digest'],
        controller_certificate_sha256=hashlib.sha256(controller_der).hexdigest(),
        receipt_certificate_sha256=hashlib.sha256(worker_der).hexdigest())
    (root/'worker-config.json').write_text(json.dumps(config))
    (root/'controller.cer').write_bytes(controller_der)
    (root/'receipt.cer').write_bytes(worker_der)
    (root/'receipt-key.pem').write_bytes(worker.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    req=normalize_request(dict(schema_version=2,operation='python-ruff-check-v1',profile='ruff-python-v1',
        tool=built['tool'],worker_digest=built['worker_digest'],device_id='surface',job_id='a'*32,
        timeout_seconds=10,files=[dict(path='pkg/a.py',content='answer = missing\n'),
                                  dict(path='b.py',content='answer = 42\n')]))
    sign=lambda data:controller.sign(data,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
    envelope=seal_request(req,sign,issued_at=issued,expires_at=issued+300)
    verify=certificate_verifier(worker_der,config['receipt_certificate_sha256'],now=lambda:now)
    f=SimpleNamespace(root=root,req=req,config=config,built=built,now=issued,sign=sign,
        envelope=envelope,wire=encode_request(envelope),verify=verify,calls=[],status='passed',hook=lambda:None)
    f.observe=lambda:dict(observed_at=issued,available_ram_gib=4,free_disk_gib=10,
                          cpu_percent=10,plugged_in=True)
    def runner(files,**options):
        f.calls.append((copy.deepcopy(files),dict(options)));f.hook()
        assert files==req['files']
        assert options==dict(tool_path=root/'tools/ruff',tool_sha256=req['tool']['sha256'],
            tool_version=req['tool']['version'],profile=req['profile'],timeout_seconds=req['timeout_seconds'])
        diagnostics=[dict(path='pkg/a.py',code='F821',message='Undefined name: missing',
            line=1,column=10,end_line=1,end_column=17)] if f.status=='findings' else []
        return dict(state='completed' if f.status in ('passed','findings') else 'failed',
            status=f.status,diagnostics=diagnostics,reason='' if f.status in ('passed','findings') else 'tool_unavailable',
            source_unchanged=True,tool=dict(req['tool']),profile=req['profile'],
            files=[dict(path=row['path'],sha256=hashlib.sha256(row['content'].encode()).hexdigest()) for row in files])
    monkeypatch.setattr(remote_lint_runner,'execute_linux_lint',runner)
    # Only the service's platform guard is simulated. Cryptography, manifest,
    # executable byte pin, protocol validation and SQLite claims are all real.
    monkeypatch.setattr(service,'sys',SimpleNamespace(platform='linux'))
    return f


def process(f, **overrides):
    return service.process_packet(f.wire,f.root,clock=lambda:f.now,observe=f.observe,**overrides)


def authenticated(f, wire):
    return open_receipt(f.req,wire,f.verify,device_id='surface',
                        worker_digest=f.req['worker_digest'],now=f.now)


@pytest.mark.parametrize('status',['passed','findings','tool_failed','unavailable'])
def test_signed_lint_service_retains_exact_outcome_and_queries_without_rerun(signed_lint,status):
    f=signed_lint;f.status=status
    assert json.loads(process(f,query_only=True))['state']=='unconfirmed'
    assert JobStore(f.root/'jobs.sqlite3').lookup(f.req) is None and not f.calls
    receipt=authenticated(f,process(f))
    assert receipt['status']==status
    evidence=validate_receipt(f.req,receipt)
    assert evidence['status']==('passed' if status=='passed' else 'failed')
    assert evidence['lint_status']==status and not evidence['authorized']
    assert receipt['checks']==[dict(path='pkg/a.py',passed=status=='passed'),
        dict(path='b.py',passed=status in ('passed','findings'))]
    assert JobStore(f.root/'jobs.sqlite3').lookup(f.req)['receipt']==receipt
    assert authenticated(f,process(f))==receipt
    assert authenticated(f,process(f,query_only=True))==receipt
    assert len(f.calls)==1


@pytest.mark.parametrize('change',['source','tool','version','worker'])
def test_changed_signed_request_is_rejected_before_claim(signed_lint,change):
    f=signed_lint;envelope=copy.deepcopy(f.envelope)
    raw=envelope['payload']['request'];raw.pop('request_digest')
    if change=='source': raw['files'][0]['content']='answer = 42\n'
    elif change=='tool': raw['tool']['sha256']='e'*64
    elif change=='version': raw['tool']['version']='0.17.2'
    else: raw['worker_digest']='e'*64
    envelope['payload']['request']=normalize_request(raw)
    f.wire=encode_request(envelope)
    assert json.loads(process(f))['state']=='rejected'
    assert not f.calls and not (f.root/'jobs.sqlite3').exists()


@pytest.mark.parametrize('change',['tool_bytes','tool_manifest','bundle_manifest','controller_pin','receipt_key'])
def test_provisioned_tool_or_identity_changes_reject_before_claim(signed_lint,change):
    f=signed_lint
    if change=='tool_bytes': (f.root/'tools/ruff').write_bytes(b'\x7fELFchanged')
    elif change=='tool_manifest': (f.root/'ruff-tool.json').write_text('{}')
    elif change=='bundle_manifest': (f.root/'bundle-manifest.json').write_text('{}')
    elif change=='receipt_key': (f.root/'receipt-key.pem').write_text('invalid fixture')
    else:
        f.config['controller_certificate_sha256']='e'*64
        (f.root/'worker-config.json').write_text(json.dumps(f.config))
    assert json.loads(process(f))['state']=='rejected'
    assert not f.calls and not (f.root/'jobs.sqlite3').exists()


def test_lint_resource_floor_defers_without_claim_or_syntax_fallback(signed_lint):
    f=signed_lint;observe=f.observe
    f.observe=lambda:{**observe(),'available_ram_gib':1.249}
    assert json.loads(process(f))['state']=='deferred'
    assert JobStore(f.root/'jobs.sqlite3').lookup(f.req) is None and not f.calls


def test_worker_disable_during_telemetry_read_prevents_claim(signed_lint):
    f=signed_lint;observe=f.observe
    def observations():
        f.config['enabled']=False
        (f.root/'worker-config.json').write_text(json.dumps(f.config))
        return observe()
    f.observe=observations
    assert json.loads(process(f))['state']=='rejected'
    assert JobStore(f.root/'jobs.sqlite3').lookup(f.req) is None and not f.calls


def test_uncertain_lint_after_claim_is_never_retried_or_mislabeled_clean(signed_lint):
    f=signed_lint
    def uncertain(): raise RuntimeError('private fixture execution detail')
    f.hook=uncertain
    first=process(f)
    assert json.loads(first)==dict(schema_version=1,state='unconfirmed',authenticated=False,receipt=None)
    assert b'private' not in first
    assert JobStore(f.root/'jobs.sqlite3').lookup(f.req)['receipt'] is None
    assert json.loads(process(f))['state']=='unconfirmed'
    assert json.loads(process(f,query_only=True))['state']=='unconfirmed'
    assert len(f.calls)==1


def test_signed_receipt_tamper_cannot_be_accepted_as_success(signed_lint):
    f=signed_lint;f.status='findings'
    wire=process(f)
    assert authenticated(f,wire)['status']=='findings'
    altered=wire.replace(b'"message":"Undefined name: missing"',b'"message":"Different finding"')
    assert altered!=wire
    with pytest.raises(ValueError): authenticated(f,altered)
