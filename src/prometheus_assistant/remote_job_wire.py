"""Bounded JSON codecs and pinned-callback receipt authentication.

Request decoding validates structure, NOT authenticity; call open_envelope
before admission. Receipt verification requires a caller-selected pinned worker
verifier, never a key from the packet. No transport or key loading lives here.
"""
import base64
import binascii
import json
import math

from .remote_job_envelope import _payload as _request_payload, request_purpose
from .remote_job_protocol import normalize_request, validate_receipt

MAX_REQUEST_BYTES = 3200000
MAX_RECEIPT_BYTES = 8192
MAX_RECEIPT_ENVELOPE_BYTES = 16384
_PURPOSE = 'prometheus.remote.syntax.receipt'


def _dump(value, limit):
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Invalid wire JSON value.') from None
    if len(data) >= limit:
        raise ValueError('Wire JSON exceeds its byte limit.')
    return data


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key.')
        result[key] = value
    return result


def _constant(_):
    raise ValueError('Nonfinite JSON numbers are not accepted.')


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Nonfinite JSON numbers are not accepted.')
    return number


def _load(data, limit):
    if type(data) is not bytes or not 0 < len(data) < limit:
        raise ValueError('Wire input must be bounded UTF-8 bytes.')
    # Bound nesting before JSON allocation, ignoring brackets within strings.
    depth, quoted, escaped = 0, False, False
    for char in data:
        if quoted:
            if escaped: escaped = False
            elif char == 92: escaped = True
            elif char == 34: quoted = False
        elif char == 34: quoted = True
        elif char in (91,123):
            depth += 1
            if depth > 12: raise ValueError('Wire JSON is too deeply nested.')
        elif char in (93,125): depth -= 1
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=_constant, parse_float=_float)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('Malformed, duplicate or nonfinite wire JSON.') from None


def _signature(text):
    if type(text) is not str or not 344 <= len(text) <= 1368:
        raise ValueError('Malformed signature encoding.')
    try:
        value = base64.b64decode(text,validate=True)
    except (ValueError,binascii.Error):
        raise ValueError('Malformed signature encoding.') from None
    if not 256 <= len(value) <= 1024 or base64.b64encode(value).decode('ascii') != text:
        raise ValueError('Noncanonical or unsupported signature.')
    return value


def _outer(value):
    if (type(value) is not dict or set(value) != {'schema_version','payload','signature'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('Unexpected envelope fields or schema.')
    _signature(value['signature'])


def _request(value):
    _outer(value)
    payload = _request_payload(value['payload'])
    return dict(schema_version=1,payload=payload,signature=value['signature'])


def encode_request(envelope):
    """Encode structurally valid existing signed envelope; does not verify it."""
    return _dump(_request(envelope),MAX_REQUEST_BYTES)


def decode_request(data):
    """Decode bounded bytes; call open_envelope separately before trusting them."""
    return _request(_load(data,MAX_REQUEST_BYTES))


def _receipt_payload(request, payload):
    req = normalize_request(request)
    fields = {'schema_version','purpose','issued_at','expires_at','request_digest','receipt'}
    if (type(payload) is not dict or set(payload) != fields
            or type(payload['schema_version']) is not int or payload['schema_version'] != req['schema_version']
            or payload['purpose'] != request_purpose(req)+'.receipt'
            or payload['request_digest'] != req['request_digest']):
        raise ValueError('Receipt payload does not match the exact request and purpose.')
    issued, expires = payload['issued_at'],payload['expires_at']
    if type(issued) is not int or type(expires) is not int or not 0 < expires-issued <= 300:
        raise ValueError('Receipt lifetime must be one to 300 integer seconds.')
    receipt = payload['receipt']
    if (validate_receipt(req,receipt)['status']=='incomplete'
            or receipt['state'] not in ('completed','failed')):
        raise ValueError('An exact terminal receipt is required.')
    _dump(receipt,MAX_RECEIPT_BYTES)
    return payload


def seal_receipt(request, receipt, signer, *, issued_at, expires_at):
    """Return wire bytes signed by the caller's trusted worker signer(bytes)."""
    req = normalize_request(request)
    payload = _receipt_payload(req,dict(schema_version=req['schema_version'],purpose=request_purpose(req)+'.receipt',
        issued_at=issued_at,expires_at=expires_at,request_digest=req['request_digest'],receipt=receipt))
    canonical = _dump(payload,MAX_RECEIPT_ENVELOPE_BYTES)
    signature = signer(canonical)
    if type(signature) is not bytes or not 256 <= len(signature) <= 1024:
        raise ValueError('Signer must return 256 to 1024 signature bytes.')
    return _dump(dict(schema_version=1,payload=payload,
                      signature=base64.b64encode(signature).decode('ascii')),MAX_RECEIPT_ENVELOPE_BYTES)


def open_receipt(request, data, verify, *, device_id, worker_digest, now):
    """Authenticate fresh terminal receipt bytes against exact work and target.

    verify(canonical_payload, signature) must return exactly True. Successful
    authentication does not turn failed syntax into passed evidence or authorize
    any subsequent action. Replay/storage policy remains the caller's concern.
    """
    req = normalize_request(request)
    if type(now) is not int or req['device_id'] != device_id or req['worker_digest'] != worker_digest:
        raise ValueError('Trusted receipt time or target does not match.')
    envelope = _load(data,MAX_RECEIPT_ENVELOPE_BYTES)
    _outer(envelope)
    payload = _receipt_payload(req,envelope['payload'])
    if not payload['issued_at'] <= now < payload['expires_at']:
        raise ValueError('Receipt is expired or future-dated.')
    if verify(_dump(payload,MAX_RECEIPT_ENVELOPE_BYTES),_signature(envelope['signature'])) is not True:
        raise ValueError('Receipt signature verification failed.')
    return payload['receipt']
