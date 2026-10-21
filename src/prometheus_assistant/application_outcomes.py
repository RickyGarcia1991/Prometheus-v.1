"""Bounded application-attempt observations; never activation or retry authority."""
from contextlib import closing
import hashlib
import json
import math
import secrets
import sqlite3
import time

from .decision_history import DecisionHistory, _event, _identity

PENDING = frozenset({'promotion_pending', 'validating', 'retesting', 'signing', 'waiting_for_shutdown', 'starting'})
NATIVE = PENDING | {'active', 'failed', 'rolled_back', 'recovery_required'}
STATUSES = NATIVE | {'started', 'unconfirmed'}
MAX_PER_DECISION = 32
MAX_LINKS = 4096
MAX_EVENTS = MAX_LINKS * MAX_PER_DECISION
MAX_EVENT_BYTES = 2048
MAX_DATABASE_BYTES = 32 * 1024 * 1024
_FIELDS = {'event_id', 'phase', 'observed_at', 'status', 'job_id', 'candidate_fingerprint', 'evidence_sha256'}
_SCHEMA = 'CREATE TABLE outcomes (sequence INTEGER PRIMARY KEY, owner TEXT NOT NULL, approval_id TEXT NOT NULL, fingerprint TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)'


def _facts(status, job_id, candidate_fingerprint):
    return dict(status=status, job_id=job_id, candidate_fingerprint=candidate_fingerprint)


def _digest(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(event):
    if type(event) is not dict or set(event) != _FIELDS:
        raise ValueError('Invalid application observation schema.')
    _identity(event['event_id'], 32); _identity(event['job_id'], 32); _identity(event['candidate_fingerprint'], 64)
    if type(event['status']) is not str or event['status'] not in STATUSES:
        raise ValueError('Invalid application observation status.')
    if type(event['phase']) is not str or event['phase'] not in {'started', 'observed'} or (event['phase'] == 'started') != (event['status'] == 'started'):
        raise ValueError('Invalid application observation phase.')
    stamp = event['observed_at']
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= stamp <= 253402300799:
        raise ValueError('Invalid application observation timestamp.')
    expected = _digest(_facts(event['status'], event['job_id'], event['candidate_fingerprint'])) if event['status'] in NATIVE else None
    if event['evidence_sha256'] != expected:
        raise ValueError('Application evidence does not match selected native facts.')
    payload = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(payload.encode()) > MAX_EVENT_BYTES:
        raise ValueError('Application observation exceeds its bound.')
    return payload


def sequence(prior, event):
    if not prior:
        if event['status'] != 'started':
            raise ValueError('Application observation requires a recorded start.')
        return
    if (event['job_id'], event['candidate_fingerprint']) != (prior[0]['job_id'], prior[0]['candidate_fingerprint']):
        raise ValueError('Application observation binding differs.')
    if event['status'] == 'started' or len(prior) >= MAX_PER_DECISION:
        raise ValueError('Invalid or full application observation sequence.')
    if any(e['status'] == event['status'] for e in prior):
        raise ValueError('Duplicate application observation.')


def summary(events):
    """Historical truth, independent of refresh ordering; no current-health claim."""
    prior = []
    for event in events:
        validate(event); sequence(prior, event); prior.append(event)
    if not events:
        return 'missing', 'no_recorded_start'
    statuses = {e['status'] for e in events}
    if ('active' in statuses and statuses & {'failed', 'rolled_back', 'recovery_required'}
            or {'rolled_back', 'recovery_required'} <= statuses):
        return 'conflicting', 'native_identity_conflict'
    if 'rolled_back' in statuses:
        return 'restored', 'application_restored'
    if 'recovery_required' in statuses:
        return 'unconfirmed', 'restoration_unconfirmed'
    if statuses & {'active', 'failed'}:
        return 'recorded', 'native_result_recorded'
    if events[-1]['status'] in PENDING:
        return 'pending', 'execution_pending'
    return 'unconfirmed', 'outcome_unconfirmed'


class ApplicationOutcomes(DecisionHistory):
    def __init__(self, history):
        if history is None:
            raise ValueError('Decision history is not configured.')
        self.history_store = history
        self.root = history.root / 'ApplicationLinks'
        self.path = self.root / 'outcomes.sqlite3'
        self._plain()

    @staticmethod
    def _schema(db, *, create=False):
        rows = db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if not rows and version == 0 and create:
            db.execute(_SCHEMA); db.execute('PRAGMA user_version=1'); return
        if version != 1 or rows != [('outcomes', _SCHEMA)]:
            raise ValueError('Invalid application evidence schema.')

    def _approved(self, decisions=None):
        result = {}
        for row in self.history_store._all_rows() if decisions is None else decisions:
            _event(row)
            if row['kind'] == 'self_improve' and row['decision'] == 'approve':
                key = (row['owner'], row['id'], row['fingerprint'])
                if key in result:raise ValueError('Repeated approved application decision.')
                result[key] = row
        return result

    @staticmethod
    def _rows(db, approved):
        rows = db.execute('SELECT owner,approval_id,fingerprint,event_id,payload FROM outcomes ORDER BY sequence LIMIT ?', (MAX_EVENTS + 1,)).fetchall()
        if len(rows) > MAX_EVENTS:raise ValueError('Application evidence capacity exceeded.')
        groups, jobs, result = {}, {}, []
        for owner, ident, fingerprint, eid, payload in rows:
            _identity(owner, 64); _identity(ident, 32); _identity(fingerprint, 64)
            key = owner, ident, fingerprint
            if key not in approved:raise ValueError('Application evidence lacks an exact approved decision.')
            if type(payload) is not str or len(payload) > MAX_EVENT_BYTES:raise ValueError('Invalid bounded application observation.')
            event = json.loads(payload)
            if validate(event) != payload or event['event_id'] != eid:raise ValueError('Application observation bytes differ.')
            binding = (*key, event['candidate_fingerprint'])
            if event['job_id'] in jobs and jobs[event['job_id']] != binding:
                raise ValueError('Application job has ambiguous ownership or approval.')
            jobs[event['job_id']] = binding
            prior = groups.setdefault(key, [])
            sequence(prior, event); prior.append(event)
            result.append((*key, event))
        if len(groups) > MAX_LINKS:raise ValueError('Application link capacity exceeded.')
        return result

    def read(self, *, decisions=None):
        self._plain()
        if not self.path.exists():return []
        try:
            approved = self._approved(decisions)
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                self._schema(db)
                return self._rows(db, approved)
        except (sqlite3.Error, UnicodeError) as error:
            raise ValueError('Application evidence unavailable.') from error

    def _record(self, key, event):
        payload = validate(event); self._plain()
        approved = self._approved()
        if key not in approved:raise ValueError('Exact approved application decision required.')
        if not self.path.exists() and event['status'] != 'started':raise ValueError('Application start is not recorded.')
        try:
            self.root.mkdir(parents=True, exist_ok=True); self._plain()
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.execute('PRAGMA synchronous=FULL')
                page = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count=' + str(MAX_DATABASE_BYTES // page))
                db.execute('BEGIN IMMEDIATE'); self._schema(db, create=True)
                rows = self._rows(db, approved)
                prior = []
                for o, i, f, saved in rows:
                    if saved['job_id'] == event['job_id'] and ((o, i, f) != key or saved['candidate_fingerprint'] != event['candidate_fingerprint']):
                        raise ValueError('Application job has ambiguous ownership or approval.')
                    if (o, i, f) == key:prior.append(saved)
                if prior and (prior[0]['job_id'], prior[0]['candidate_fingerprint']) != (event['job_id'], event['candidate_fingerprint']):
                    raise ValueError('Application observation binding differs.')
                if any(e['status'] == event['status'] for e in prior):return False
                sequence(prior, event)
                if len(rows) >= MAX_EVENTS or not prior and sum(e['status'] == 'started' for *_, e in rows) >= MAX_LINKS:
                    raise ValueError('Application evidence is full; maintenance required.')
                db.execute('INSERT INTO outcomes(owner,approval_id,fingerprint,event_id,payload) VALUES (?,?,?,?,?)', (*key, event['event_id'], payload))
            return True
        except sqlite3.Error as error:
            raise ValueError('Application evidence could not be saved.') from error

    @staticmethod
    def _new(status, job_id, candidate):
        return dict(event_id=secrets.token_hex(16), phase='started' if status == 'started' else 'observed',
                    observed_at=time.time(), status=status, job_id=job_id, candidate_fingerprint=candidate,
                    evidence_sha256=_digest(_facts(status, job_id, candidate)) if status in NATIVE else None)

    def start(self, *, owner, row, approved_job):
        _identity(owner, 64)
        if type(row) is not dict or row.get('kind') != 'self_improve':raise ValueError('Exact application proposal required.')
        try:
            document = {key: row[key] for key in ('kind', 'payload', 'review', 'session', 'policy_revision')}
            digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            job, candidate = row['payload']['job_id'], row['payload']['approval_fingerprint']
            key = owner, row['id'], row['fingerprint']
        except (KeyError, TypeError, ValueError) as error:raise ValueError('Exact application proposal required.') from error
        _identity(job, 32); _identity(candidate, 64)
        receipt = self._approved().get(key)
        if (digest != row['fingerprint'] or receipt is None or receipt['session'] != row['session']
                or type(approved_job) is not dict or approved_job.get('owner') != owner
                or approved_job.get('job_id') != job or approved_job.get('candidate_fingerprint') != candidate):
            raise ValueError('Application start does not match its approved receipt.')
        return self._record(key, self._new('started', job, candidate))

    def for_job(self, *, owner, job_id, candidate_fingerprint):
        _identity(owner, 64); _identity(job_id, 32); _identity(candidate_fingerprint, 64)
        matches = [(o, i, f) for o, i, f, e in self.read() if o == owner and e['status'] == 'started'
                   and e['job_id'] == job_id and e['candidate_fingerprint'] == candidate_fingerprint]
        if len(matches) > 1:raise ValueError('Application job binding is ambiguous.')
        return matches[0] if matches else None

    def observe_native(self, *, owner, job_id, candidate_fingerprint, result):
        key = self.for_job(owner=owner, job_id=job_id, candidate_fingerprint=candidate_fingerprint)
        if key is None:return False  # Legacy jobs never acquire inferred decision links.
        if type(result) is not dict or result.get('job_id') != job_id:
            raise ValueError('Native application job identity differs.')
        candidate = result.get('approval_fingerprint')
        if candidate is not None and candidate != candidate_fingerprint:
            raise ValueError('Native application candidate identity differs.')
        status = result.get('state')
        if type(status) is not str or status not in NATIVE or candidate is None:
            status = 'unconfirmed'
        return self._record(key, self._new(status, job_id, candidate_fingerprint))

    def unconfirmed(self, *, owner, job_id, candidate_fingerprint):
        key = self.for_job(owner=owner, job_id=job_id, candidate_fingerprint=candidate_fingerprint)
        return False if key is None else self._record(key, self._new('unconfirmed', job_id, candidate_fingerprint))
