"""Resource advertisements authenticate observations without authorizing work."""
import hashlib
import json

import pytest

from prometheus_assistant.remote_resource_wire import (
    seal_query, open_query, seal_advertisement, open_advertisement,
)


def sign(data): return hashlib.sha256(data).digest()*8
def verify(data, signature): return signature == sign(data)


def query(**changes):
    args = dict(nonce='a'*32, device_id='surface', worker_digest='b'*64,
                issued_at=100, expires_at=130)
    args.update(changes)
    return seal_query(sign, **args)


def open_request(data, **changes):
    args = dict(device_id='surface', worker_digest='b'*64, now=110)
    args.update(changes)
    return open_query(data, verify, **args)


def observations():
    return dict(enabled=True, available_ram_gib=4.5, free_disk_gib=20,
                cpu_percent=12, battery_percent=75, plugged_in=True)


def advertisement(facts=None):
    return seal_advertisement(open_request(query()), facts or observations(), sign,
                              issued_at=110, expires_at=130)


def opened(data, verifier=verify, **changes):
    args = dict(nonce='a'*32, device_id='surface', worker_digest='b'*64, now=115)
    args.update(changes)
    return open_advertisement(data, verifier, **args)


def test_roundtrip_binds_only_target_nonce_time_and_resource_observations():
    request = open_request(query())
    assert set(request) == {'schema_version', 'purpose', 'nonce', 'device_id',
                            'worker_digest', 'issued_at', 'expires_at'}
    assert request['purpose'] == 'prometheus.remote.resources.query'
    result = opened(advertisement())
    assert result['purpose'] == 'prometheus.remote.resources.advertisement'
    assert result['observations'] == observations()
    assert 'files' not in result and 'operation' not in result and 'job_id' not in result


def test_unknown_metrics_remain_null_and_local_disablement_is_representable():
    facts = dict.fromkeys(observations())
    facts['enabled'] = False
    assert opened(advertisement(facts))['observations'] == facts


@pytest.mark.parametrize('changes', [dict(nonce='c'*32), dict(device_id='other'),
    dict(worker_digest='c'*64), dict(now=109), dict(now=130), dict(now=True)])
def test_old_or_wrong_target_advertisement_cannot_satisfy_new_query(changes):
    with pytest.raises(ValueError): opened(advertisement(), **changes)


@pytest.mark.parametrize('changes', [dict(device_id='other'), dict(worker_digest='c'*64),
    dict(now=99), dict(now=130), dict(now=True)])
def test_query_freshness_and_target(changes):
    with pytest.raises(ValueError): open_request(query(), **changes)


@pytest.mark.parametrize('field,value', [('nonce', 'c'*32), ('device_id', 'other'),
    ('worker_digest', 'c'*64), ('issued_at', 109), ('expires_at', 129),
    ('purpose', 'prometheus.remote.syntax'), ('extra', 'source'), ('schema_version', True)])
def test_tampered_fields_rejected(field, value):
    for wire, opener in ((query(), open_request), (advertisement(), opened)):
        packet = json.loads(wire); packet['payload'][field] = value
        with pytest.raises(ValueError): opener(json.dumps(packet).encode())


@pytest.mark.parametrize('field,value', [('available_ram_gib', -1), ('free_disk_gib', True),
    ('cpu_percent', 101), ('battery_percent', -1), ('plugged_in', 1), ('enabled', None),
    ('available_ram_gib', float('nan')), ('free_disk_gib', float('inf')),
    ('available_ram_gib', 10**400),
    ('battery_percent', '75'), ('extra', 'private information')])
def test_malformed_observations_rejected_before_signing(field, value):
    facts = observations(); facts[field] = value
    with pytest.raises(ValueError): advertisement(facts)


def test_missing_field_is_not_silently_zero():
    facts = observations(); del facts['cpu_percent']
    with pytest.raises(ValueError): advertisement(facts)


@pytest.mark.parametrize('data', [b'', b'{}', b'null', b'\xff', b' '*8192,
    b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":1e999}',
    b'['*13+b']'*13])
def test_bounded_strict_wire_decoding(data):
    with pytest.raises(ValueError): open_request(data)
    with pytest.raises(ValueError): opened(data)


def test_duplicate_nested_fields_and_unsigned_extra_fields_rejected():
    data = advertisement().replace(b'"cpu_percent":12', b'"cpu_percent":12,"cpu_percent":12')
    with pytest.raises(ValueError): opened(data)
    packet = json.loads(advertisement()); packet['public_key'] = 'not trusted'
    with pytest.raises(ValueError): opened(json.dumps(packet).encode())


@pytest.mark.parametrize('result', [False, None, 1, 'yes'])
def test_signature_verifier_must_return_true(result):
    with pytest.raises(ValueError): opened(advertisement(), lambda *_: result)


@pytest.mark.parametrize('issued,expires', [(100, 100), (100, 131), (True, 20), (100, 110.0)])
def test_lifetime_is_one_to_thirty_integer_seconds(issued, expires):
    with pytest.raises(ValueError): seal_query(sign, nonce='a'*32, device_id='surface',
        worker_digest='b'*64, issued_at=issued, expires_at=expires)
    with pytest.raises(ValueError): seal_advertisement(open_request(query()), observations(),
        sign, issued_at=issued, expires_at=expires)


def test_protocol_purposes_are_not_interchangeable():
    with pytest.raises(ValueError): open_request(advertisement())
    with pytest.raises(ValueError): opened(query())
    with pytest.raises(ValueError): seal_query(lambda _: True, nonce='a'*32,
        device_id='surface', worker_digest='b'*64, issued_at=100, expires_at=110)


@pytest.mark.parametrize('changes', [dict(nonce='A'*32), dict(nonce='a'*31),
    dict(device_id='../worker'), dict(worker_digest='b'*63)])
def test_query_identity_is_canonical(changes):
    with pytest.raises(ValueError): query(**changes)
