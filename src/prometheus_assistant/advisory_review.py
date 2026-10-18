"""Qualify bounded advisory output against a trusted application's review context.

This module neither reads files nor invokes models, checks, tools or approvals.
The caller must validate the project candidate, supply its exact proposed file
bytes, and obtain identities and invocation metadata from its trusted adapter.
Never populate these fields from model output or a browser request. Hashes bind
records for comparison; they are not signatures or remote execution attestation.
Even a qualified report does not establish correctness or independent reasoning.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy


MAX_CANDIDATE_BYTES = 4_000_000
MAX_SNAPSHOT_BYTES = 64_000
MAX_RESPONSE_BYTES = 64_000
_MAX_FINDINGS = 32
_CONTEXT_KEYS = {'schema_version', 'candidate_sha256', 'files', 'author', 'reviewer',
                 'artifact_relationship', 'prompt_version', 'prompt_sha256', 'request_sha256'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value, limit=MAX_CANDIDATE_BYTES):
    """Bound canonical serialization without first constructing an unlimited blob."""
    result = bytearray()
    try:
        encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=True, allow_nan=False)
        for part in encoder.iterencode(value):
            # ensure_ascii makes character and encoded byte lengths identical.
            if len(result) + len(part) > limit:
                raise ValueError('Review data exceeds its byte limit.')
            result.extend(part.encode('utf-8'))
    except (TypeError, RecursionError, UnicodeError) as error:
        raise ValueError('Review data must be bounded JSON.') from error
    return bytes(result)


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('Invalid ' + label + ' fields.')


def _text(value, label, limit=200):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(character) < 32 for character in value)):
        raise ValueError('Invalid ' + label + '.')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError('Invalid ' + label + '.') from error
    return value


def _hash(value, label):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-fA-F]{64}', value) is None:
        raise ValueError('Invalid ' + label + '.')
    return value.lower()


def _path(value):
    value = _text(value, 'reviewed path', 512)
    if ('\\' in value or ':' in value or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Review paths must be exact project-relative POSIX paths.')
    return value


def _identity(value):
    _keys(value, {'provider', 'model', 'artifact_sha256'}, 'review identity')
    return dict(provider=_text(value['provider'], 'provider'),
                model=_text(value['model'], 'model'),
                artifact_sha256=(None if value['artifact_sha256'] is None else
                                 _hash(value['artifact_sha256'], 'model artifact SHA256')))


def _snapshot(candidate, reviewed_files):
    if not isinstance(candidate, dict) or type(candidate.get('version')) is not int:
        raise ValueError('A validated project candidate is required.')
    if candidate['version'] == 1:
        members = [candidate]
    elif candidate['version'] == 2:
        members = candidate.get('candidates')
        if not isinstance(members, list) or not 2 <= len(members) <= 8:
            raise ValueError('A change set must contain two to eight candidates.')
    else:
        raise ValueError('Unknown project candidate version.')
    if not isinstance(reviewed_files, dict) or not 1 <= len(reviewed_files) <= 8:
        raise ValueError('Supply one to eight exact proposed files.')
    expected, aliases = {}, set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError('Invalid project candidate member.')
        path = _path(member.get('path'))
        if path.casefold() in aliases:
            raise ValueError('Duplicate candidate file.')
        aliases.add(path.casefold())
        expected[path] = _hash(member.get('after_sha256'), 'proposed file SHA256')
    if set(reviewed_files) != set(expected):
        raise ValueError('Reviewed files differ from the exact candidate file set.')
    result, total = [], 0
    for path in sorted(expected):
        body = reviewed_files[path]
        if not isinstance(body, bytes):
            raise ValueError('Reviewed snapshots must contain exact bytes.')
        total += len(body)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError('Reviewed source exceeds its byte limit.')
        if _sha(body) != expected[path]:
            raise ValueError('Reviewed source differs from the proposed file digest.')
        try:
            source = body.decode('utf-8-sig')
        except UnicodeError as error:
            raise ValueError('Reviewed files must be UTF-8 text.') from error
        if '\x00' in source:
            raise ValueError('Reviewed source contains a NUL byte.')
        # Count actual source lines, not other Unicode/control separators.
        lines = re.split(r'\r\n|\r|\n', source)
        line_count = len(lines) - (1 if lines[-1] == '' else 0)
        result.append(dict(path=path, sha256=expected[path], bytes=len(body), lines=line_count))
    return result


def prepare_review(candidate, reviewed_files, *, author, reviewer, prompt_version, prompt_sha256):
    """Bind a trusted local invocation's intended candidate, source and provenance.

    ``reviewed_files`` maps exact candidate paths to full proposed UTF-8 bytes,
    including existing BOM/newline style. No truncated excerpts are accepted.
    ``author`` may be None; identities contain provider/model/artifact_sha256,
    with an unknown artifact represented by None. ``prompt_sha256`` is computed
    by the adapter from the exact rendered request, not asserted by the model.
    Distinct model artifacts describe provenance, not independent reasoning.
    """
    candidate_sha256 = _sha(_json_bytes(candidate))
    files = _snapshot(candidate, reviewed_files)
    author = None if author is None else _identity(author)
    reviewer = _identity(reviewer)
    original = author['artifact_sha256'] if author else None
    checked = reviewer['artifact_sha256']
    relationship = ('unestablished' if original is None or checked is None else
                    'same_artifact' if original == checked else 'different_artifact')
    context = dict(schema_version=1, candidate_sha256=candidate_sha256, files=files,
                   author=author, reviewer=reviewer, artifact_relationship=relationship,
                   prompt_version=_text(prompt_version, 'prompt version'),
                   prompt_sha256=_hash(prompt_sha256, 'prompt SHA256'))
    context['request_sha256'] = _sha(_json_bytes(context))
    return context


def _parse(response):
    if not isinstance(response, str):
        raise ValueError('Advisory response must be JSON text.')
    if len(response) > MAX_RESPONSE_BYTES:
        raise ValueError('Advisory response exceeds its byte limit.')
    try:
        body = response.encode('utf-8')
    except UnicodeError as error:
        raise ValueError('Advisory response must be UTF-8 text.') from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError('Advisory response exceeds its byte limit.')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate advisory JSON key.')
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError('Non-finite advisory JSON value.')

    try:
        payload = json.loads(response, object_pairs_hook=pairs, parse_constant=invalid)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Invalid advisory JSON.') from error
    _keys(payload, {'findings'}, 'advisory response')
    rows = payload['findings']
    if not isinstance(rows, list) or len(rows) > _MAX_FINDINGS:
        raise ValueError('Supply at most 32 advisory findings.')
    return rows, _sha(body)


def _finding(row, files, reviewer):
    _keys(row, {'file', 'line', 'category', 'risk', 'confidence', 'message', 'reproducible'}, 'finding')
    path = _path(row['file'])
    line = row['line']
    if path not in files or type(line) is not int or not 1 <= line <= files[path]:
        raise ValueError('Finding location is outside the reviewed snapshot.')
    risk, confidence = row['risk'], row['confidence']
    if risk not in ('low', 'normal', 'high', 'critical'):
        raise ValueError('Invalid finding risk.')
    if (type(confidence) not in (int, float) or not 0 <= confidence <= 1
            or not math.isfinite(confidence)):
        raise ValueError('Invalid finding confidence.')
    if type(row['reproducible']) is not bool:
        raise ValueError('Invalid reproduction claim.')
    return dict(file=path, line=line, category=_text(row['category'], 'category', 80),
                risk=risk, confidence=float(confidence), message=_text(row['message'], 'finding message', 4096),
                source=reviewer['provider'] + '/' + reviewer['model'], advisory=True,
                reproduction_claimed=row['reproducible'], reproducible=False,
                evidence_qualification='unverified_claim')


def qualify_review(candidate, reviewed_files, prepared, response_json, *, invocation):
    """Validate one response against unchanged trusted preparation and invocation.

    Invocation has request_sha256, status and duration_seconds. Status is
    completed/timed_out/cancelled/unavailable; noncompleted calls require None
    response and produce no findings. Invalid/stale input raises ValueError.
    All valid findings are advisory and unverified even when reproducible=True
    was claimed. No result is a test pass, approval, or independent attestation.
    """
    _keys(prepared, _CONTEXT_KEYS, 'prepared context')
    expected = prepare_review(candidate, reviewed_files, author=prepared['author'],
                              reviewer=prepared['reviewer'], prompt_version=prepared['prompt_version'],
                              prompt_sha256=prepared['prompt_sha256'])
    if _json_bytes(prepared) != _json_bytes(expected):
        raise ValueError('Prepared review no longer matches this candidate and context.')
    _keys(invocation, {'request_sha256', 'status', 'duration_seconds'}, 'invocation')
    if _hash(invocation['request_sha256'], 'invocation request SHA256') != expected['request_sha256']:
        raise ValueError('Invocation belongs to a different review request.')
    status, duration = invocation['status'], invocation['duration_seconds']
    if status not in ('completed', 'timed_out', 'cancelled', 'unavailable'):
        raise ValueError('Invalid invocation status.')
    if type(duration) not in (int, float) or not 0 <= duration <= 1800 or not math.isfinite(duration):
        raise ValueError('Invalid invocation duration.')
    findings, duplicates, output_sha256 = {}, 0, None
    if status == 'completed':
        rows, output_sha256 = _parse(response_json)
        files = {row['path']: row['lines'] for row in expected['files']}
        for row in rows:
            finding = _finding(row, files, expected['reviewer'])
            key = _json_bytes(finding)
            duplicates += int(key in findings)
            findings[key] = finding
    elif response_json is not None:
        raise ValueError('Incomplete invocations cannot supply advisory results.')
    report = dict(schema_version=1, context=expected,
                  invocation={'status': status, 'duration_seconds': float(duration)},
                  status='qualified_advisory' if status == 'completed' else status,
                  response_sha256=output_sha256, findings=[findings[key] for key in sorted(findings)],
                  duplicate_findings=duplicates, checks=[], authorized=False, approval_required=True)
    report['receipt_sha256'] = _sha(_json_bytes(report))
    return report


def validate_saved_advice(value):
    """Validate bounded retained provenance, without recovering any authority.

    This checks structure/integrity only. Applying a candidate still requires
    the server to validate its full bytes and the exact owner approval anew.
    """
    _keys(value, {'status', 'reason_code', 'reason', 'attempted', 'authorized', 'report'}, 'advisory result')
    _json_bytes(value)  # Bounded canonical hashing has a separate representation.
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) > 120000:
        raise ValueError('Retained advisory data exceeds its UTF-8 storage bound.')
    _text(value['reason_code'], 'reason code', 100)
    _text(value['reason'], 'reason', 1000)
    if value['authorized'] is not False or type(value['attempted']) is not bool:
        raise ValueError('Advisory records cannot grant authority.')
    if value['status'] != 'qualified_advisory':
        if value['status'] not in {'unavailable', 'timed_out', 'cancelled', 'invalid_input', 'invalid_response'} or value['report'] is not None:
            raise ValueError('Invalid incomplete advisory record.')
        return deepcopy(value)
    report = deepcopy(value['report'])
    _keys(report, {'schema_version', 'context', 'invocation', 'status', 'response_sha256', 'findings',
        'duplicate_findings', 'checks', 'authorized', 'approval_required', 'receipt_sha256'}, 'advisory report')
    receipt = report.pop('receipt_sha256')
    if receipt != _sha(_json_bytes(report)):
        raise ValueError('Retained advisory review receipt changed.')
    if (report['schema_version'] != 1 or report['status'] != 'qualified_advisory'
            or report['checks'] != [] or report['authorized'] is not False
            or report['approval_required'] is not True or value['attempted'] is not True):
        raise ValueError('Invalid advisory qualification.')
    context = report['context']
    _keys(context, _CONTEXT_KEYS, 'retained context')
    _identity(context['reviewer'])
    author = None if context['author'] is None else _identity(context['author'])
    original = author['artifact_sha256'] if author else None
    checked = context['reviewer']['artifact_sha256']
    relationship = 'unestablished' if original is None or checked is None else 'same_artifact' if original == checked else 'different_artifact'
    request = {key: item for key, item in context.items() if key != 'request_sha256'}
    if context['schema_version'] != 1 or context['artifact_relationship'] != relationship or context['request_sha256'] != _sha(_json_bytes(request)):
        raise ValueError('Invalid retained review context.')
    _hash(context['candidate_sha256'], 'candidate SHA256')
    _hash(context['prompt_sha256'], 'prompt SHA256')
    _text(context['prompt_version'], 'prompt version')
    _hash(report['response_sha256'], 'response SHA256')
    files = context['files']
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError('Invalid retained file list.')
    aliases, lines, total = set(), {}, 0
    for row in files:
        _keys(row, {'path', 'sha256', 'bytes', 'lines'}, 'retained file')
        path = _path(row['path'])
        _hash(row['sha256'], 'file SHA256')
        if path.casefold() in aliases or type(row['bytes']) is not int or type(row['lines']) is not int or not 0 <= row['lines'] <= row['bytes'] <= MAX_SNAPSHOT_BYTES:
            raise ValueError('Invalid retained file bounds.')
        aliases.add(path.casefold()); lines[path] = row['lines']; total += row['bytes']
    if total > MAX_SNAPSHOT_BYTES:
        raise ValueError('Retained source exceeds its bound.')
    invocation = report['invocation']
    _keys(invocation, {'status', 'duration_seconds'}, 'retained invocation')
    duration = invocation['duration_seconds']
    if invocation['status'] != 'completed' or type(duration) not in (int, float) or not 0 <= duration <= 1800 or not math.isfinite(duration):
        raise ValueError('Invalid retained invocation.')
    rows = report['findings']
    if not isinstance(rows, list) or len(rows) > 32 or type(report['duplicate_findings']) is not int or not 0 <= report['duplicate_findings'] <= 32:
        raise ValueError('Invalid retained findings.')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid retained finding.')
        raw = {key: row.get(key) for key in ('file', 'line', 'category', 'risk', 'confidence', 'message')}
        raw['reproducible'] = row.get('reproduction_claimed')
        if _finding(raw, lines, context['reviewer']) != row:
            raise ValueError('Invalid retained finding qualification.')
    return deepcopy(value)
