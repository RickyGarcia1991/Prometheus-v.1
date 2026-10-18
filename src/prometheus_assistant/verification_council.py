"""Local, deterministic review evidence aggregation; never an authorization gate.

Callers must obtain check records from trusted runners and verify referenced logs.
Hashes bind supplied records, but are not signatures or proof that a test ran.
No commands, models, services, or approvals are executed by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

_MAX_RECORDS = 256


def _text(value: object, label: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'Invalid {label}')
    if any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid {label}')
    return value.strip()


def _hash(value: object, label: str, lengths: tuple[int, ...]) -> str:
    value = _text(value, label, max(lengths))
    if len(value) not in lengths or re.fullmatch('[0-9a-fA-F]+', value) is None:
        raise ValueError(f'Invalid {label}')
    return value.lower()


def _records(values: Iterable, label: str) -> list:
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError(f'Invalid {label}')
    result = []
    for value in values:
        if len(result) >= _MAX_RECORDS:
            raise ValueError(f'Too many {label}')
        result.append(value)
    return result


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _finding(record: Mapping) -> dict:
    if not isinstance(record, Mapping):
        raise ValueError('Invalid finding')
    path = _text(record.get('file'), 'file').replace('\\', '/')
    if PurePosixPath(path).is_absolute() or '..' in path.split('/') or ':' in path:
        raise ValueError('Finding file must be project-relative')
    line = record.get('line')
    if type(line) is not int or line < 1:
        raise ValueError('Invalid line')
    confidence = record.get('confidence')
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid confidence')
    risk = record.get('risk')
    if risk not in ('low', 'normal', 'high', 'critical'):
        raise ValueError('Invalid risk')
    reproducible = record.get('reproducible')
    if type(reproducible) is not bool:
        raise ValueError('Invalid reproducible flag')
    return dict(file=str(PurePosixPath(path)), line=line,
                category=_text(record.get('category'), 'category'), risk=risk,
                confidence=float(confidence),
                message=_text(record.get('message'), 'message', 4096),
                reproducible=reproducible,
                sources=[_text(record.get('source'), 'source')], advisory=True)


def evaluate_verification(
    commit_sha: str | None,
    content_sha256: str,
    required_checks: Iterable[str],
    check_results: Iterable[Mapping],
    reviewer_findings: Iterable[Mapping] = (),
) -> dict:
    """Build a bounded, JSON-ready evidence report tied to one exact change.

    Each check has name, status (passed/failed/skipped/unavailable/running),
    commit_sha, content_sha256, tool, tool_version and evidence_sha256.
    Required check names are an explicit caller policy, not inferred from results.
    Duplicate records block rather than using last-writer-wins. Non-passing or
    stale evidence blocks readiness. All findings remain advisory; high/critical
    claims require review even if deterministic checks pass. This API deliberately
    accepts no owner approval field and always returns authorized=False.

    Supply redacted finding descriptions: this pure aggregator neither reads nor
    redacts source files or logs. Receipt hashes protect comparison/integrity only.
    """
    # Ungit-backed project edits use a content snapshot identity explicitly.
    commit = None if commit_sha is None else _hash(commit_sha, 'commit SHA', (40, 64))
    digest = _hash(content_sha256, 'content SHA256', (64,))
    required = [_text(item, 'check name') for item in _records(required_checks, 'required checks')]
    if not required or len(set(required)) != len(required):
        raise ValueError('Required checks must be nonempty and unique')
    grouped = {name: [] for name in sorted(required)}
    for record in _records(check_results, 'check results'):
        if not isinstance(record, Mapping):
            raise ValueError('Invalid check result')
        name = _text(record.get('name'), 'check name')
        if name not in grouped:
            raise ValueError('Check not in required policy')
        status = record.get('status')
        if status not in ('passed', 'failed', 'skipped', 'unavailable', 'running'):
            raise ValueError('Invalid check status')
        normalized = dict(name=name, status=status,
            commit_sha=(None if record.get('commit_sha') is None else
                        _hash(record.get('commit_sha'), 'check commit SHA', (40, 64))),
            content_sha256=_hash(record.get('content_sha256'), 'check content SHA256', (64,)),
            tool=_text(record.get('tool'), 'tool'),
            tool_version=_text(record.get('tool_version'), 'tool version'),
            evidence_sha256=_hash(record.get('evidence_sha256'), 'evidence SHA256', (64,)))
        grouped[name].append(normalized)
    checks = []
    for name, records in grouped.items():
        status = 'missing'
        if len(records) > 1:
            status = 'conflicting'
        elif records:
            item = records[0]
            status = item['status'] if (item['commit_sha'] == commit and item['content_sha256'] == digest) else 'stale'
        checks.append(dict(name=name, status=status, evidence=sorted(records, key=_canonical)))
    findings = {}
    for record in _records(reviewer_findings, 'reviewer findings'):
        finding = _finding(record)
        # Exact corroborating claims merge sources; differing severity, confidence,
        # wording or reproduction claims remain visible instead of becoming votes.
        key = _canonical({k: v for k, v in finding.items() if k != 'sources'})
        if key in findings:
            findings[key]['sources'] = sorted(set(findings[key]['sources'] + finding['sources']))
        else:
            findings[key] = finding
    normalized_findings = [findings[key] for key in sorted(findings)]
    status = 'ready_for_owner_review'
    if any(f['risk'] in ('high', 'critical') for f in normalized_findings):
        status = 'review_needed'
    if any(c['status'] != 'passed' for c in checks):
        status = 'blocked'
    report = dict(schema_version=1, policy='local-evidence-v1',
                  identity_kind='content_snapshot' if commit is None else 'git_commit',
                  commit_sha=commit, content_sha256=digest,
                  required_checks=sorted(required), checks=checks,
                  findings=normalized_findings, status=status,
                  approval_required=True, authorized=False)
    report['receipt_sha256'] = hashlib.sha256(_canonical(report).encode('utf-8')).hexdigest()
    return report
