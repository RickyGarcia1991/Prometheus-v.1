"""Canonical request envelopes; cryptographic trust is supplied by the caller.

The caller must choose a pinned trusted cryptographic signer/verifier, never a
key or verifier supplied by the incoming envelope. This module implements no
cryptography, key discovery, authorization, replay ledger or transport.
"""
import base64
import binascii
import json

from .remote_job_protocol import normalize_request

_PURPOSE = 'prometheus.remote.syntax'
_LINT_PURPOSE = 'prometheus.remote.lint'
_PAYLOAD_FIELDS = {'schema_version','purpose','issued_at','expires_at','request'}
_MAX_CANONICAL_BYTES = 3100000


def request_purpose(request):
    """Map a validated schema to its distinct signing domain."""
    return _LINT_PURPOSE if request['schema_version'] == 2 else _PURPOSE


def _canonical(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > _MAX_CANONICAL_BYTES:
        raise ValueError('Signed request exceeds its bounded size.')
    return encoded


def _payload(value):
    if not isinstance(value,dict) or set(value) != _PAYLOAD_FIELDS:
        raise ValueError('Unexpected or missing signed payload fields.')
    issued, expires = value['issued_at'], value['expires_at']
    if type(issued) is not int or type(expires) is not int or not 0 < expires-issued <= 300:
        raise ValueError('Signed request lifetime must be one to 300 integer seconds.')
    raw = value['request']
    if not isinstance(raw,dict) or 'request_digest' not in raw:
        raise ValueError('Signed request must contain its normalized digest.')
    req = normalize_request(raw)
    purpose = request_purpose(req)
    if (type(value['schema_version']) is not int or value['schema_version'] != req['schema_version']
            or value['purpose'] != purpose):
        raise ValueError('Unsupported signed request schema or purpose.')
    return dict(schema_version=req['schema_version'],purpose=purpose,
                issued_at=issued,expires_at=expires,request=req)


def seal_request(request, signer, *, issued_at, expires_at):
    """Seal exact fixed work using a caller-selected trusted signer(bytes).

    signer must return 256..1024 signature bytes from a cryptographic signing
    implementation. This function cannot establish the signer's trust itself.
    """
    req = normalize_request(request)
    payload = _payload(dict(schema_version=req['schema_version'],purpose=request_purpose(req),
                            issued_at=issued_at,expires_at=expires_at,request=req))
    signature = signer(_canonical(payload))
    if type(signature) is not bytes or not 256 <= len(signature) <= 1024:
        raise ValueError('Signer must return 256 to 1024 signature bytes.')
    return dict(schema_version=1,payload=payload,signature=base64.b64encode(signature).decode('ascii'))


def open_envelope(envelope, verify, *, device_id, worker_digest, now):
    """Verify an exact, current target-bound envelope using a pinned verifier.

    verify(canonical_payload_bytes, signature_bytes) must return exactly True.
    Success is authentication under caller-supplied trust, not owner approval
    or replay permission; use the separate admission and durable claim layers.
    """
    if type(now) is not int:
        raise ValueError('The trusted current time must be integer seconds.')
    if not isinstance(envelope,dict) or set(envelope) != {'schema_version','payload','signature'}:
        raise ValueError('Unexpected or missing envelope fields.')
    if type(envelope['schema_version']) is not int or envelope['schema_version'] != 1:
        raise ValueError('Unsupported envelope schema.')
    payload = _payload(envelope['payload'])
    if not payload['issued_at'] <= now < payload['expires_at']:
        raise ValueError('Signed request is expired or future-dated.')
    req = payload['request']
    if req['device_id'] != device_id or req['worker_digest'] != worker_digest:
        raise ValueError('Signed request does not match the expected device and worker.')
    text = envelope['signature']
    if type(text) is not str or not 344 <= len(text) <= 1368:
        raise ValueError('Malformed signature encoding.')
    try:
        signature = base64.b64decode(text,validate=True)
    except (ValueError,binascii.Error):
        raise ValueError('Malformed signature encoding.') from None
    if not 256 <= len(signature) <= 1024 or base64.b64encode(signature).decode('ascii') != text:
        raise ValueError('Malformed or noncanonical signature encoding.')
    if verify(_canonical(payload),signature) is not True:
        raise ValueError('Signed request verification failed.')
    return req
