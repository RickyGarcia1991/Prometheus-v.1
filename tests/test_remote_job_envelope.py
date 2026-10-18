"""Deterministic callback contract tests, not native crypto qualification."""
import base64
import hashlib
import json

import pytest

from prometheus_assistant.remote_job_envelope import seal_request, open_envelope
from prometheus_assistant.remote_job_protocol import normalize_request


def request():
    return dict(schema_version=1,job_id='a'*32,device_id='surface',worker_digest='b'*64,
        operation='python-syntax-check',timeout_seconds=5,
        files=[dict(path='main.py',content='x = "é"\r\n')])


# Deliberately deterministic test doubles, NOT signing algorithms for production.
def signer(payload):
    return hashlib.sha256(payload).digest()*8


def verifier(payload,signature):
    return signature == signer(payload)


def sealed():
    return seal_request(request(),signer,issued_at=100,expires_at=200)


def opened(envelope,verify=verifier,**overrides):
    return open_envelope(envelope,verify,**{'device_id':'surface','worker_digest':'b'*64,'now':150,**overrides})


def test_roundtrip_canonical_bytes_and_detachment():
    seen=[]
    raw=request()
    env=seal_request(raw,lambda data: seen.append(data) or signer(data),issued_at=100,expires_at=200)
    assert seen==[json.dumps(env['payload'],sort_keys=True,separators=(',', ':'),ensure_ascii=False,
                            allow_nan=False).encode('utf-8')]
    result=opened(env)
    assert result==normalize_request(raw)
    result['files'][0]['content']='changed'
    assert env['payload']['request']['files'][0]['content']==raw['files'][0]['content']


@pytest.mark.parametrize('field,value',[('schema_version',2),('purpose','other'),('issued_at',99),
    ('expires_at',201),('extra',True)])
def test_payload_field_tamper(field,value):
    env=sealed(); env['payload'][field]=value
    with pytest.raises(ValueError): opened(env)


@pytest.mark.parametrize('field,value',[('schema_version',True),('job_id','c'*32),('device_id','other'),
    ('worker_digest','c'*64),('operation','shell'),('timeout_seconds',6),
    ('request_digest','c'*64),('files',[dict(path='other.py',content='pass')])])
def test_every_request_field_bound_even_if_digest_recomputed(field,value):
    env=sealed(); raw=env['payload']['request']; raw[field]=value
    if field!='request_digest':
        raw.pop('request_digest')
        try: env['payload']['request']=normalize_request(raw)
        except ValueError: pass
    with pytest.raises(ValueError): opened(env)


@pytest.mark.parametrize('now',[99,200,201,True,1.5,float('nan')])
def test_stale_future_or_invalid_clock(now):
    with pytest.raises(ValueError): open_envelope(sealed(),verifier,device_id='surface',worker_digest='b'*64,now=now)


def test_time_edges_and_target():
    for now in (100,199):
        assert open_envelope(sealed(),verifier,device_id='surface',worker_digest='b'*64,now=now)
    for device,worker in [('wrong','b'*64),('surface','c'*64),(None,'b'*64)]:
        with pytest.raises(ValueError): open_envelope(sealed(),verifier,device_id=device,worker_digest=worker,now=150)


@pytest.mark.parametrize('issued,expires',[(100,100),(100,99),(100,401),(True,100),(100,True),(1.5,2)])
def test_invalid_lifetime(issued,expires):
    with pytest.raises(ValueError): seal_request(request(),signer,issued_at=issued,expires_at=expires)


@pytest.mark.parametrize('signature',[b'',b'x'*255,b'x'*1025,bytearray(b'x'*256),'x'*256,None])
def test_signer_contract(signature):
    with pytest.raises(ValueError): seal_request(request(),lambda _:signature,issued_at=100,expires_at=200)


@pytest.mark.parametrize('signature',['!', 'x'*10000, None, b'x'*344,
    base64.b64encode(b'x'*255).decode(),base64.b64encode(b'x'*1025).decode()])
def test_bad_signature_encoding(signature):
    env=sealed(); env['signature']=signature
    with pytest.raises(ValueError): opened(env)


@pytest.mark.parametrize('result',[False,None,1,'yes',[],{'valid':True}])
def test_verifier_requires_exact_true(result):
    with pytest.raises(ValueError): opened(sealed(),lambda *_:result)


def test_envelope_exact_fields_and_tampered_signature():
    for change in ('extra','schema','signature','missing_digest'):
        env=sealed()
        if change=='extra': env['key']='incoming-key'
        elif change=='schema': env['schema_version']=True
        elif change=='signature': env['signature']=base64.b64encode(b'x'*256).decode()
        else: del env['payload']['request']['request_digest']
        with pytest.raises(ValueError): opened(env)


def test_maximum_control_character_payload_roundtrip():
    raw=request(); raw['files']=[dict(path='a.py',content='\x01'*256000),dict(path='b.py',content='\x02'*256000)]
    env=seal_request(raw,signer,issued_at=0,expires_at=300)
    assert opened(env)==normalize_request(raw)


@pytest.mark.parametrize('size',[256,1024])
def test_signature_length_edges_and_callback_bytes(size):
    env=seal_request(request(),lambda _:b'x'*size,issued_at=100,expires_at=101)
    def verify(payload,signature):
        assert type(payload) is bytes and signature==b'x'*size
        return True
    assert opened(env,verify,now=100)


def test_noncanonical_base64_padding_bits_rejected_before_verify():
    env=sealed()
    # A 256-byte signature has two padding characters. Alter only unused bits.
    alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    value=env['signature']; index=alphabet.index(value[-3])
    env['signature']=value[:-3]+alphabet[index+1]+'=='
    assert base64.b64decode(env['signature'])==base64.b64decode(value)
    with pytest.raises(ValueError): opened(env,lambda *_:pytest.fail('Noncanonical encoding reached verifier'))


def lint_request():
    return {**request(), 'schema_version':2, 'operation':'python-ruff-check-v1',
            'profile':'ruff-python-v1', 'tool':{'sha256':'c'*64,'version':'0.14.0'}}


def test_lint_uses_distinct_signed_payload_schema_and_purpose():
    envelope=seal_request(lint_request(),signer,issued_at=100,expires_at=200)
    assert envelope['schema_version']==1  # Existing outer encoding container.
    assert envelope['payload']['schema_version']==2
    assert envelope['payload']['purpose']=='prometheus.remote.lint'
    assert opened(envelope)==normalize_request(lint_request())
    assert sealed()['payload']['schema_version']==1
    assert sealed()['payload']['purpose']=='prometheus.remote.syntax'


@pytest.mark.parametrize('kind',['lint_as_syntax','syntax_as_lint','lint_payload_v1',
    'syntax_payload_v2','lint_tool','lint_profile'])
def test_cross_operation_or_tool_rebinding_rejected_even_with_test_signature(kind):
    lint=not kind.startswith('syntax_')
    envelope=seal_request(lint_request() if lint else request(),signer,issued_at=100,expires_at=200)
    if kind=='lint_as_syntax': envelope['payload']['purpose']='prometheus.remote.syntax'
    elif kind=='syntax_as_lint': envelope['payload']['purpose']='prometheus.remote.lint'
    elif kind=='lint_payload_v1': envelope['payload']['schema_version']=1
    elif kind=='syntax_payload_v2': envelope['payload']['schema_version']=2
    elif kind=='lint_tool': envelope['payload']['request']['tool']['sha256']='d'*64
    else: envelope['payload']['request']['profile']='custom'
    envelope['signature']=base64.b64encode(signer(json.dumps(envelope['payload'],
        sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())).decode()
    with pytest.raises(ValueError): opened(envelope)
