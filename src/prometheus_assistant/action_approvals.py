"""Short-lived, exact owner approvals. Model output never enters this module."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

from .coding_workspace import _no_links, _write_atomic


def decision_text(text):
    """Recognize a complete confirmation, not a quoted or embedded instruction."""
    if not isinstance(text, str) or len(text) > 100:
        return None
    phrase = re.sub(r'\s+', ' ', text.strip().casefold()).rstrip('.!')
    phrase = re.sub(r',\s*', ' ', phrase)
    if phrase in {'yes', 'yes please', 'approve', 'approve it', 'approve this change',
                  'yes approve', 'yes approve it', 'go ahead', 'yes go ahead',
                  'please go ahead', 'do it', 'proceed', 'allow', 'allow it'}:
        return 'approve'
    if phrase in {'no', 'no thanks', 'reject', 'reject it', 'reject this change',
                  'cancel', 'cancel it', 'cancel the change', 'do not proceed',
                  "don't proceed", 'stop', 'deny', 'deny it'}:
        return 'reject'
    return None


class ActionApprovals:
    """One pending action per authenticated owner/browser session, held in RAM.

    A restart revokes pending approvals. Consuming an approval removes it before
    dispatch, so failures cannot turn a retry into an unintended second action.
    """
    def __init__(self, *, clock=time.monotonic, lifetime=600, receipt_root=None):
        if not 1 <= lifetime <= 600:
            raise ValueError('Approval lifetime must be at most ten minutes.')
        self.clock, self.lifetime = clock, lifetime
        self._pending = {}
        self._lock = threading.RLock()
        self.receipt_root = Path(receipt_root) if receipt_root is not None else None

    def _expire(self):
        now = self.clock()
        self._pending = {key: row for key, row in self._pending.items() if row['expires_at'] > now}

    @staticmethod
    def _key(owner, binding):
        if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                   for value in (owner, binding)):
            raise ValueError('A current authenticated owner session is required.')
        return owner, binding

    def has_binding(self, binding):
        with self._lock:
            self._expire()
            return any(key[1] == binding for key in self._pending)

    def pending(self, *, owner, binding):
        key = self._key(owner, binding)
        with self._lock:
            self._expire()
            row = self._pending.get(key)
            return copy.deepcopy(row) if row else None

    def peek(self, *, owner, binding):
        """Project this session's live proposal without expiring any stored row."""
        key = self._key(owner, binding)
        with self._lock:
            row = self._pending.get(key)
            return copy.deepcopy(row) if row and row['expires_at'] > self.clock() else None

    def review(self, *, owner, binding, session, kind, payload, review, policy_revision):
        key = self._key(owner, binding)
        if not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session):
            raise ValueError('An exact conversation is required for approval.')
        document = json.loads(json.dumps({'kind': kind, 'payload': payload, 'review': review,
            'session': session, 'policy_revision': policy_revision}, allow_nan=False, sort_keys=True))
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self._lock:
            self._expire()
            if key in self._pending:
                raise ValueError('Approve or reject the existing proposal before preparing another.')
            row = {**document, 'id': secrets.token_hex(16), 'fingerprint': digest,
                   'expires_at': self.clock() + self.lifetime, 'expires_in_seconds': self.lifetime}
            self._pending[key] = row
            return copy.deepcopy(row)

    def consume(self, *, owner, binding, session, approval_id, fingerprint, policy_revision, decision):
        key = self._key(owner, binding)
        if decision not in {'approve', 'reject'}:
            raise ValueError('Choose approve or reject.')
        with self._lock:
            self._expire()
            row = self._pending.get(key)
            if (not row or row['session'] != session or row['id'] != approval_id
                    or not secrets.compare_digest(row['fingerprint'], fingerprint)):
                raise ValueError('This exact approval is missing, expired, or belongs to another conversation.')
            if decision == 'approve' and row['policy_revision'] != policy_revision:
                del self._pending[key]
                raise ValueError('Application policy changed. Prepare a new review before approval.')
            del self._pending[key]
            return copy.deepcopy(row)

    def revoke_binding(self, binding):
        with self._lock:
            self._pending = {key: row for key, row in self._pending.items() if key[1] != binding}

    def _receipt_path(self, job_id):
        if self.receipt_root is None:
            raise ValueError('Approved-change receipts are not configured.')
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('Invalid approved change identifier.')
        path = self.receipt_root / (job_id + '.json')
        _no_links(path)
        return path

    def record_approved(self, *, owner, job_id, candidate_fingerprint):
        """Minimal ownership needed to read activation status after a restart."""
        self._key(owner, candidate_fingerprint)
        path = self._receipt_path(job_id)
        row = {'job_id': job_id, 'owner': owner, 'candidate_fingerprint': candidate_fingerprint,
               'approved_at_ns': time.time_ns(), 'dispatch_state': 'dispatching'}
        with self._lock:
            if path.exists():
                existing = self.approved_job(owner=owner, job_id=job_id)
                if existing['candidate_fingerprint'] != candidate_fingerprint:
                    raise ValueError('This approved job has different candidate bytes.')
                return existing
            self.receipt_root.mkdir(parents=True, exist_ok=True)
            _no_links(path)
            _write_atomic(path, (json.dumps(row, sort_keys=True)+'\n').encode())
            return row

    def record_dispatch(self, *, owner, job_id, state, error=''):
        if state not in {'promotion_pending', 'failed'}:
            raise ValueError('Invalid activation dispatch state.')
        with self._lock:
            row = self.approved_job(owner=owner, job_id=job_id)
            row.update(dispatch_state=state, dispatch_error=str(error)[:256])
            _write_atomic(self._receipt_path(job_id), (json.dumps(row, sort_keys=True)+'\n').encode())

    def approved_job(self, *, owner, job_id):
        self._key(owner, '0'*64)
        row = self._read_approved(job_id)
        if row['owner'] != owner:
            raise ValueError('No approved change belongs to this owner with that identifier.')
        return row

    def _read_approved(self, job_id):
        path = self._receipt_path(job_id)
        if not path.is_file() or path.stat().st_size > 2048:
            raise ValueError('No approved change belongs to this owner with that identifier.')
        with path.open('rb') as stream:
            data = stream.read(2049)
        if len(data) > 2048:
            raise ValueError('Approved-change receipt exceeds its limit.')
        row = json.loads(data)
        if (not isinstance(row, dict) or not isinstance(row.get('owner'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['owner']) or row.get('job_id') != job_id
                or type(row.get('approved_at_ns')) is not int
                or not isinstance(row.get('candidate_fingerprint'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['candidate_fingerprint'])):
            raise ValueError('No approved change belongs to this owner with that identifier.')
        return row

    def latest_approved(self, *, owner):
        self._key(owner, '0'*64)
        if self.receipt_root is None:
            raise ValueError('Approved-change receipts are not configured.')
        _no_links(self.receipt_root)
        latest = None
        for count, path in enumerate(self.receipt_root.glob('*.json')):
            if count >= 4096:
                raise ValueError('Too many approved-change receipts to select one safely.')
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            row = self._read_approved(path.stem)
            if row['owner'] != owner:
                continue
            if latest is None or row['approved_at_ns'] > latest['approved_at_ns']:
                latest = row
        return latest
