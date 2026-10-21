"""Wire contracts use deterministic test doubles, not production cryptography."""
import hashlib
import json

import pytest

from prometheus_assistant.remote_job_envelope import seal_request, open_envelope
from prometheus_assistant.remote_job_protocol import normalize_request, validate_receipt
from prometheus_assistant.remote_job_wire import encode_request, decode_request, seal_receipt, open_receipt


def req():
    return normalize_request(dict(schema_version=1,job_id='a'*32,device_id='surface',worker_digest='b'*64,
        operation='python-syntax-check',timeout_seconds=5,files=[dict(path='a.py',content='x="é"\r\n')]))


def sign(data): return hashlib.sha256(data).digest()*8
def verify(data,signature): return signature==sign(data)


def receipt():
    request=req()
    return {**{k:request[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
            'state':'completed','checks':[dict(path='a.py',passed=True)]}


def sealed(): return seal_receipt(req(),receipt(),sign,issued_at=100,expires_at=200)


def opened(data,verifier=verify,**kwargs):
    return open_receipt(req(),data,verifier,**{'device_id':'surface','worker_digest':'b'*64,'now':150,**kwargs})


def test_request_roundtrip_preserves_signed_bytes():
    envelope=seal_request(req(),sign,issued_at=100,expires_at=200)
    decoded=decode_request(encode_request(envelope))
    assert decoded==envelope
    assert open_envelope(decoded,verify,device_id='surface',worker_digest='b'*64,now=150)==req()


@pytest.mark.parametrize('data',[b'{"schema_version":1,"schema_version":1}',b'{"x":NaN}',
    b'{"x":Infinity}',b'{"x":-Infinity}',b'{"x":1e999}',b'['*13+b']'*13,b'\xff',b'{} trailing',b'',b'null'])
def test_strict_decoding_rejects_malformed_wire(data):
    with pytest.raises(ValueError): decode_request(data)
    with pytest.raises(ValueError): opened(data)


def test_nested_duplicate_and_size_caps():
    envelope=seal_request(req(),sign,issued_at=100,expires_at=200)
    wire=encode_request(envelope).replace(b'"timeout_seconds":5',b'"timeout_seconds":5,"timeout_seconds":5')
    with pytest.raises(ValueError): decode_request(wire)
    with pytest.raises(ValueError): decode_request(b' '*3200000)
    with pytest.raises(ValueError): opened(b' '*16384)
    with pytest.raises(ValueError): decode_request('{}')


def test_request_codec_is_structural_not_fake_authentication():
    envelope=seal_request(req(),sign,issued_at=100,expires_at=200)
    envelope['signature']=seal_request(req(),lambda _:b'x'*256,issued_at=100,expires_at=200)['signature']
    decoded=decode_request(encode_request(envelope))
    with pytest.raises(ValueError): open_envelope(decoded,verify,device_id='surface',worker_digest='b'*64,now=150)
    envelope['payload']['request']['timeout_seconds']=6
    with pytest.raises(ValueError): encode_request(envelope)


def test_maximum_escaped_source_roundtrip():
    request=req(); del request['request_digest']
    request['files']=[dict(path='a.py',content='\x01'*256000),dict(path='b.py',content='\x02'*256000)]
    envelope=seal_request(request,sign,issued_at=100,expires_at=200)
    assert decode_request(encode_request(envelope))==envelope


def test_receipt_roundtrip_and_failed_evidence():
    assert opened(sealed())==receipt()
    row=receipt(); row['checks'][0]['passed']=False
    result=opened(seal_receipt(req(),row,sign,issued_at=100,expires_at=200))
    assert validate_receipt(req(),result)['status']=='failed'


@pytest.mark.parametrize('field,value',[('purpose','prometheus.remote.syntax'),('schema_version',True),
    ('issued_at',99),('expires_at',201),('request_digest','c'*64),('extra','bad')])
def test_every_receipt_payload_field_bound(field,value):
    packet=json.loads(sealed()); packet['payload'][field]=value
    with pytest.raises(ValueError): opened(json.dumps(packet).encode())


@pytest.mark.parametrize('field,value',[('device_id','other'),('worker_digest','c'*64),('job_id','c'*32),
    ('state','running'),('checks',[]),('checks',[dict(path='other.py',passed=True)]),('extra','bad')])
def test_changed_receipt_never_accepted(field,value):
    packet=json.loads(sealed()); packet['payload']['receipt'][field]=value
    with pytest.raises(ValueError): opened(json.dumps(packet).encode())


@pytest.mark.parametrize('now',[99,200,True,float('nan')])
def test_receipt_freshness(now):
    with pytest.raises(ValueError): opened(sealed(),now=now)


def test_target_and_cross_request_replay_rejected():
    for changes in ({'device_id':'other'},{'worker_digest':'c'*64}):
        with pytest.raises(ValueError): opened(sealed(),**changes)
    changed=req(); del changed['request_digest']; changed['files'][0]['content']='pass'
    with pytest.raises(ValueError): open_receipt(changed,sealed(),verify,device_id='surface',worker_digest='b'*64,now=150)


@pytest.mark.parametrize('result',[False,None,1,'yes'])
def test_strict_verifier(result):
    with pytest.raises(ValueError): opened(sealed(),lambda *_:result)


@pytest.mark.parametrize('signature',[None,'!', 'x'*10000])
def test_bad_signature(signature):
    packet=json.loads(sealed()); packet['signature']=signature
    with pytest.raises(ValueError): opened(json.dumps(packet).encode())


@pytest.mark.parametrize('issued,expires',[(1,1),(2,1),(0,301),(True,2),(1,2.0)])
def test_invalid_signing_lifetime(issued,expires):
    with pytest.raises(ValueError): seal_receipt(req(),receipt(),sign,issued_at=issued,expires_at=expires)


def test_exact_outer_fields_and_signer_type():
    packet=json.loads(sealed()); packet['key']='incoming key'
    with pytest.raises(ValueError): opened(json.dumps(packet).encode())
    with pytest.raises(ValueError): seal_receipt(req(),receipt(),lambda _:True,issued_at=1,expires_at=2)


def lint_pair():
    raw=req(); raw.pop('request_digest')
    raw.update(schema_version=2,operation='python-ruff-check-v1',profile='ruff-python-v1',
               tool={'sha256':'c'*64,'version':'0.14.0'})
    request=normalize_request(raw)
    answer={**{k:request[k] for k in ('schema_version','job_id','device_id','worker_digest',
        'request_digest','operation','profile','tool')}, 'state':'completed',
        'checks':[dict(path='a.py',passed=True)], 'status':'passed',
        'diagnostics':[], 'reason':'', 'source_unchanged':True}
    return request,answer


def test_lint_request_and_receipt_wire_roundtrip_preserves_schema_and_scope():
    request,answer=lint_pair()
    envelope=seal_request(request,sign,issued_at=100,expires_at=200)
    assert decode_request(encode_request(envelope))==envelope
    data=seal_receipt(request,answer,sign,issued_at=100,expires_at=200)
    value=json.loads(data)
    assert value['schema_version']==1 and value['payload']['schema_version']==2
    assert value['payload']['purpose']=='prometheus.remote.lint.receipt'
    assert open_receipt(request,data,verify,device_id='surface',worker_digest='b'*64,now=150)==answer


@pytest.mark.parametrize('field,value',[('purpose','prometheus.remote.syntax.receipt'),
    ('purpose','prometheus.remote.lint'),('schema_version',1),('schema_version',True)])
def test_lint_receipt_requires_its_own_payload_domain(field,value):
    request,answer=lint_pair()
    packet=json.loads(seal_receipt(request,answer,sign,issued_at=100,expires_at=200))
    packet['payload'][field]=value
    with pytest.raises(ValueError): open_receipt(request,json.dumps(packet).encode(),lambda *_:True,
        device_id='surface',worker_digest='b'*64,now=150)


def test_lint_unavailable_is_signed_as_failed_job_not_as_clean_source():
    request,answer=lint_pair()
    answer.update(state='failed',status='unavailable',reason='Pinned tool is unavailable.',source_unchanged=False)
    answer['checks'][0]['passed']=False
    wire=seal_receipt(request,answer,sign,issued_at=100,expires_at=200)
    got=open_receipt(request,wire,verify,device_id='surface',worker_digest='b'*64,now=150)
    assert validate_receipt(request,got)['lint_status']=='unavailable'
    answer['state']='unconfirmed'
    with pytest.raises(ValueError): seal_receipt(request,answer,sign,issued_at=100,expires_at=200)


def test_lint_receipt_tool_and_operation_are_bound_to_exact_request():
    request,answer=lint_pair()
    for field,value in [('tool',{'sha256':'d'*64,'version':'0.14.0'}),('operation','python-syntax-check')]:
        changed={**answer,field:value}
        with pytest.raises(ValueError): seal_receipt(request,changed,sign,issued_at=100,expires_at=200)
    wire=seal_receipt(request,answer,sign,issued_at=100,expires_at=200)
    with pytest.raises(ValueError): open_receipt(req(),wire,verify,device_id='surface',worker_digest='b'*64,now=150)
