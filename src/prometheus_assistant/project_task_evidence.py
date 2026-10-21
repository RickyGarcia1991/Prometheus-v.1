"""Bind project review evidence to exact candidates and trusted engine results.

Server-only adapter: never accept engine_result from a model or browser request.
This reports evidence and never grants permission or executes a candidate.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from .verification_council import evaluate_verification


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()


def candidate_digest(candidate):
    """Digest the complete JSON candidate, including its exact review and checks."""
    return _digest(candidate)


def _members(candidate):
    if not isinstance(candidate, dict):
        raise ValueError('Invalid project candidate.')
    if candidate.get('version') == 1:
        return [candidate]
    members = candidate.get('candidates')
    if candidate.get('version') != 2 or not isinstance(members, list) or not 2 <= len(members) <= 8:
        raise ValueError('Invalid project change set.')
    return members


def _plan(candidate):
    checks, paths = [], set()
    for member in _members(candidate):
        if not isinstance(member, dict) or not isinstance(member.get('path'), str):
            raise ValueError('Invalid candidate member.')
        path = member['path'].casefold()
        if path in paths:
            raise ValueError('Duplicate candidate file.')
        paths.add(path)
        if not isinstance(member.get('checks'), list):
            raise ValueError('Invalid candidate check plan.')
        for check in member['checks']:
            if (not isinstance(check, dict) or set(check) != {'preset', 'target'}
                    or any(not isinstance(check[k], str) for k in ('preset', 'target'))):
                raise ValueError('Invalid candidate check.')
            if check not in checks:
                checks.append(check)
    if candidate.get('checks') != checks:
        raise ValueError('Displayed checks differ from candidate members.')
    return checks


def _name(check):
    return 'check:' + _digest(check)


def _evidence(name, status, digest, payload):
    return dict(name=name, status=status, commit_sha=None, content_sha256=digest,
                tool='Prometheus project engine', tool_version='project-evidence-v1',
                evidence_sha256=_digest(payload))


def prepare_evidence(editor, candidate):
    """Read-only preflight. Proposed runtime checks stay pending until executed."""
    candidate = deepcopy(candidate)
    with editor.workspace.lock:
        checks = _plan(candidate)
        for member in _members(candidate):
            editor._validate_candidate(member)
        digest = candidate_digest(candidate)
    report = dict(schema_version=1, identity_kind='content_snapshot',
                  candidate_sha256=digest, phase='prepared', status='pending_checks',
                  preflight='passed', authorized=False, approval_required=True,
                  checks=[{**check, 'status': 'pending'} for check in checks],
                  test_coverage='planned' if checks else 'unavailable')
    report['receipt_sha256'] = _digest(report)
    return report


def _passed_check(workspace, planned, result):
    # Require runner-shaped data and recompute success, never trust passed alone.
    if (result.get('passed') is not True or type(result.get('exit_code')) is not int
            or result['exit_code'] != 0 or result.get('executed') is False
            or result.get('cancelled') is not False or result.get('timed_out') is not False):
        return False
    if not isinstance(result.get('output'), str):
        return False
    seconds = result.get('duration_seconds')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return False
    if result.get('argv') != workspace._command(planned['preset'], planned['target']):
        return False
    count = re.search(r'Ran (\d+) tests?\b|(?:#|\u2139) tests (\d+)\b', result['output'])
    if result.get('tests_run') == 0 or (count and int(count.group(1) or count.group(2)) == 0):
        return False
    return True


def finish_evidence(editor, candidate, prepared_report, engine_result, *, reviewer_findings=()):
    """Validate trusted engine output against prepared identity and current files.

    Invoke immediately after editor.apply/apply_group within the server task flow.
    This is not an authenticity verifier for arbitrary serialized result input.
    Missing, duplicate, unexpected or failed checks cannot produce green evidence.
    """
    candidate, prepared, result = deepcopy(candidate), deepcopy(prepared_report), deepcopy(engine_result)
    digest = candidate_digest(candidate)
    checks = _plan(candidate)
    if not isinstance(prepared, dict) or not isinstance(result, dict):
        raise ValueError('Missing prepared or engine evidence.')
    receipt = prepared.pop('receipt_sha256', None)
    if (receipt != _digest(prepared) or prepared.get('candidate_sha256') != digest
            or prepared.get('phase') != 'prepared' or prepared.get('preflight') != 'passed'
            or prepared.get('checks') != [{**c, 'status': 'pending'} for c in checks]):
        raise ValueError('Prepared evidence does not match this exact candidate.')
    required = ['workspace-preflight', 'applied-snapshot'] + [_name(c) for c in checks]
    records = [_evidence('workspace-preflight', 'passed', digest, prepared)]
    visible, unexpected = [], []
    rows = result.get('checks')
    if not isinstance(rows, list) or len(rows) > 256 or any(not isinstance(r, dict) for r in rows):
        raise ValueError('Invalid engine check results.')
    with editor.workspace.lock:
        applied = result.get('status') in {'passed', 'applied_without_checks', 'unchanged'}
        edits = result.get('edits') if candidate.get('version') == 2 else [result]
        if not isinstance(edits, list) or len(edits) != len(_members(candidate)):
            applied = False
            edits = []
        for member in _members(candidate):
            matching = [r for r in edits if isinstance(r, dict) and r.get('path') == member['path']]
            try:
                actual = editor.workspace.read_file(member['path'])['sha256']
            except (ValueError, OSError):
                actual = None
            if (len(matching) != 1 or matching[0].get('sha256') != member['after_sha256']
                    or actual != member['after_sha256']):
                applied = False
        for row in rows:
            if {'preset': row.get('preset'), 'target': row.get('target')} not in checks:
                unexpected.append(row)
        if unexpected:
            applied = False
        records.append(_evidence('applied-snapshot', 'passed' if applied else 'failed', digest,
                                  {'result': result, 'matched_current_snapshot': applied}))
        for check in checks:
            matching = [r for r in rows if r.get('preset') == check['preset'] and r.get('target') == check['target']]
            state = 'missing' if not matching else 'conflicting' if len(matching) > 1 else 'failed'
            for row in matching:
                try:
                    passed = _passed_check(editor.workspace, check, row)
                except (ValueError, OSError):
                    passed = False
                records.append(_evidence(_name(check), 'passed' if passed else 'failed', digest, row))
                if len(matching) == 1 and passed:
                    state = 'passed'
            visible.append({**check, 'status': state})
    if not checks:
        required.append('test-coverage')
        records.append(_evidence('test-coverage', 'unavailable', digest, {}))
    council = evaluate_verification(None, digest, required, records, reviewer_findings=reviewer_findings)
    return dict(schema_version=1, identity_kind='content_snapshot', candidate_sha256=digest,
                phase='executed', status='passed' if council['status'] == 'ready_for_owner_review' else 'blocked',
                preflight='passed', snapshot='passed' if applied else 'failed', checks=visible,
                test_coverage='executed' if checks else 'unavailable',
                unexpected_checks=len(unexpected), authorized=False, approval_required=True,
                council=council, receipt_sha256=council['receipt_sha256'])
