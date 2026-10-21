"""Short-lived signed resource observations, with no source or work authority.

The caller chooses pinned signing and verification callbacks. A fresh random
nonce ties each advertisement to one query; this codec does not retain replay
state. Consumers must not reuse a nonce or extend the advertised expiry.
"""
import base64
import math
import re

from .remote_job_wire import _dump, _load, _outer, _signature

MAX_RESOURCE_BYTES = 8192
_QUERY = 'prometheus.remote.resources.query'
_ADVERTISEMENT = 'prometheus.remote.resources.advertisement'
_FIELDS = {'schema_version', 'purpose', 'nonce', 'device_id', 'worker_digest',
           'issued_at', 'expires_at'}
_METRICS = {'available_ram_gib', 'free_disk_gib', 'cpu_percent', 'battery_percent'}


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _observations(value):
    if (type(value) is not dict or set(value) != _METRICS | {'enabled', 'plugged_in'}
            or type(value['enabled']) is not bool
            or value['plugged_in'] is not None and type(value['plugged_in']) is not bool):
        raise ValueError('Unexpected resource observation fields or boolean values.')
    for name in _METRICS:
        metric = value[name]
        ceiling = 100 if name in ('cpu_percent', 'battery_percent') else math.inf
        if metric is not None and (not _finite(metric) or not 0 <= metric <= ceiling):
            raise ValueError('Resource observations must be finite values in range or null.')
    return dict(value)


def _payload(value, purpose):
    fields = _FIELDS | ({'observations'} if purpose == _ADVERTISEMENT else set())
    if (type(value) is not dict or set(value) != fields
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['purpose'] != purpose):
        raise ValueError('Unexpected resource payload fields, schema or purpose.')
    for name, pattern in (('nonce', '[0-9a-f]{32}'), ('worker_digest', '[0-9a-f]{64}'),
                          ('device_id', r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')):
        if type(value[name]) is not str or re.fullmatch(pattern, value[name]) is None:
            raise ValueError('Malformed resource query nonce or target identity.')
    issued, expires = value['issued_at'], value['expires_at']
    if type(issued) is not int or type(expires) is not int or not 0 < expires-issued <= 30:
        raise ValueError('Resource lifetime must be one to 30 integer seconds.')
    result = dict(value)
    if purpose == _ADVERTISEMENT:
        result['observations'] = _observations(value['observations'])
    return result


def _seal(payload, signer):
    signature = signer(_dump(payload, MAX_RESOURCE_BYTES))
    if type(signature) is not bytes or not 256 <= len(signature) <= 1024:
        raise ValueError('Signer must return 256 to 1024 signature bytes.')
    return _dump(dict(schema_version=1, payload=payload,
        signature=base64.b64encode(signature).decode('ascii')), MAX_RESOURCE_BYTES)


def _open(data, verify, *, purpose, device_id, worker_digest, now):
    if type(now) is not int:
        raise ValueError('Trusted resource time must be integer seconds.')
    envelope = _load(data, MAX_RESOURCE_BYTES)
    _outer(envelope)
    payload = _payload(envelope['payload'], purpose)
    if payload['device_id'] != device_id or payload['worker_digest'] != worker_digest:
        raise ValueError('Resource payload does not match the expected worker.')
    if not payload['issued_at'] <= now < payload['expires_at']:
        raise ValueError('Resource payload is expired or future-dated.')
    if verify(_dump(payload, MAX_RESOURCE_BYTES), _signature(envelope['signature'])) is not True:
        raise ValueError('Resource signature verification failed.')
    return payload


def seal_query(signer, *, nonce, device_id, worker_digest, issued_at, expires_at):
    """Sign a bounded observation-only query; this grants no work permission."""
    return _seal(_payload(dict(schema_version=1, purpose=_QUERY, nonce=nonce,
        device_id=device_id, worker_digest=worker_digest, issued_at=issued_at,
        expires_at=expires_at), _QUERY), signer)


def open_query(data, verify, *, device_id, worker_digest, now):
    """Authenticate a current query with the locally pinned controller verifier."""
    return _open(data, verify, purpose=_QUERY, device_id=device_id,
                 worker_digest=worker_digest, now=now)


def seal_advertisement(query, observations, signer, *, issued_at, expires_at):
    """Sign fresh observations for a query already authenticated by the caller."""
    request = _payload(query, _QUERY)
    payload = {**request, 'purpose': _ADVERTISEMENT, 'observations': observations,
               'issued_at': issued_at, 'expires_at': expires_at}
    return _seal(_payload(payload, _ADVERTISEMENT), signer)


def open_advertisement(data, verify, *, nonce, device_id, worker_digest, now):
    """Authenticate fresh observations for the exact outstanding query nonce."""
    payload = _open(data, verify, purpose=_ADVERTISEMENT, device_id=device_id,
                    worker_digest=worker_digest, now=now)
    if type(nonce) is not str or payload['nonce'] != nonce:
        raise ValueError('Resource advertisement does not match the outstanding query.')
    return payload
