"""Bounded, owner-local retention of exact approved syntax jobs awaiting resources.

This queue grants no execution authority. Its trusted caller must recheck the
owner, approval target, policy and resources before claim, and claim the separate
execution ledger before dispatch. A queue claim without a terminal receipt is
uncertain after restart and can only be queried, never automatically retried.
"""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .remote_job_protocol import normalize_request, validate_receipt
from .remote_job_store import _no_links

MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024
MAX_JOBS = 100
MAX_LIFETIME = 3600
_RECORD_FIELDS = {'request', 'target', 'policy_revision', 'approval_id', 'fingerprint',
                  'approved_at', 'expires_at', 'placement'}
_STATES = {'waiting', 'claimed', 'cancelled', 'expired', 'completed', 'failed', 'unconfirmed'}
_META_SQL = ('CREATE TABLE metadata(singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
             'owner TEXT NOT NULL, last_now REAL NOT NULL)')
_JOBS_SQL = ('CREATE TABLE jobs(job_id TEXT PRIMARY KEY, record TEXT NOT NULL, state TEXT NOT NULL, '
             'reasons TEXT NOT NULL, next_attempt_at REAL NOT NULL, result TEXT)')


def _identity(value, length):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', value):
        raise ValueError('An exact owner, approval or job identity is required.')
    return value


def _time(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253402300799
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('A finite trusted timestamp is required.')
    return value


def _json(value, limit):
    def check(item, depth=0):
        if depth > 12:
            raise ValueError('Waiting job metadata is too deeply nested.')
        if isinstance(item, dict):
            if len(item) > 128 or any(not isinstance(key, str) or len(key) > limit for key in item):
                raise ValueError('Waiting job metadata requires bounded text keys.')
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 128:
                raise ValueError('Waiting job metadata exceeds its item limit.')
            for child in item:
                check(child, depth + 1)
        elif isinstance(item, str) and len(item) > limit:
            raise ValueError('Waiting job data exceeds its byte limit.')
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError('Waiting job metadata must be JSON data.')
    try:
        check(value)
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > limit:
            raise ValueError('Waiting job data exceeds its byte limit.')
        return encoded
    except (TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError('Waiting job metadata is malformed.') from None


def _record(raw):
    if not isinstance(raw, dict) or set(raw) != _RECORD_FIELDS:
        raise ValueError('Unexpected or missing approved waiting job fields.')
    result = dict(raw, request=normalize_request(raw['request']))
    _identity(result['policy_revision'], 64)
    _identity(result['approval_id'], 32)
    _identity(result['fingerprint'], 64)
    approved, expires = _time(result['approved_at']), _time(result['expires_at'])
    if not 0 < expires - approved <= MAX_LIFETIME:
        raise ValueError('An approved waiting job must expire within one hour.')
    target = result['target']
    fields = {'account_id', 'device_id', 'device_name', 'worker_digest', 'online'}
    if isinstance(target, dict) and 'configuration_digest' in target:
        fields.add('configuration_digest')
        _identity(target['configuration_digest'], 64)
    if (not isinstance(target, dict) or set(target) != fields or target['online'] is not True
            or any(not isinstance(target[key], str) or not 1 <= len(target[key]) <= 256
                   for key in fields - {'online'})
            or any(target[key] != result['request'][key] for key in ('device_id', 'worker_digest'))):
        raise ValueError('An exact reviewed destination is required.')
    if not isinstance(result['placement'], dict):
        raise ValueError('A bounded placement explanation is required.')
    return json.loads(_json(result, MAX_RECORD_BYTES))


def _reasons(value):
    if (not isinstance(value, list) or len(value) > 16
            or any(not isinstance(reason, str) or not 1 <= len(reason) <= 512 for reason in value)):
        raise ValueError('Waiting reasons must be a bounded list of text.')
    return _json(value, 8192)


def _result(req, value):
    if (not isinstance(value, dict) or not isinstance(value.get('state'), str)
            or value['state'] not in {'completed', 'failed', 'unconfirmed'}):
        raise ValueError('A terminal or unconfirmed waiting job result is required.')
    if value['state'] in {'completed', 'failed'}:
        receipt = value.get('receipt')
        if (validate_receipt(req, receipt)['status'] == 'incomplete'
                or receipt['state'] != value['state']):
            raise ValueError('An exact terminal syntax receipt is required.')
    elif value.get('receipt') is not None:
        raise ValueError('An unconfirmed result cannot contain a terminal receipt.')
    return _json(value, MAX_RESULT_BYTES)


class WaitingStore:
    def __init__(self, absolute_path, owner):
        self.path = Path(absolute_path)
        self.owner = _identity(owner, 64)
        if not self.path.is_absolute():
            raise ValueError('Waiting storage requires an absolute owner-local path.')
        self._check()

    def _check(self):
        for path in (self.path, *(Path(str(self.path) + suffix) for suffix in ('-journal', '-wal', '-shm'))):
            _no_links(path)
            if path.exists() and (not path.is_file() or path.stat().st_size > MAX_DATABASE_BYTES):
                raise ValueError('Waiting storage exceeds its limit or is malformed.')

    @staticmethod
    def _limit(db):
        pages = MAX_DATABASE_BYTES // db.execute('PRAGMA page_size').fetchone()[0]
        if pages < 1 or db.execute('PRAGMA page_count').fetchone()[0] > pages:
            raise ValueError('Waiting storage exceeds its bounded capacity.')
        db.execute('PRAGMA max_page_count=' + str(pages))

    def _load(self, db):
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Unsupported waiting storage schema.')
        objects = db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if sorted(objects) != sorted([('table', 'metadata', _META_SQL), ('table', 'jobs', _JOBS_SQL)]):
            raise ValueError('Malformed waiting storage schema.')
        metadata = db.execute('SELECT singleton,owner,last_now FROM metadata').fetchall()
        if len(metadata) != 1 or metadata[0][:2] != (1, self.owner):
            raise ValueError('Waiting storage belongs to another owner or is malformed.')
        last_now = _time(metadata[0][2])
        rows = db.execute('SELECT job_id,record,state,reasons,next_attempt_at,result FROM jobs '
                          'ORDER BY job_id LIMIT ?', (MAX_JOBS + 1,)).fetchall()
        if len(rows) > MAX_JOBS:
            raise ValueError('Waiting storage exceeds its job capacity.')
        result = {}
        for job_id, encoded, state, reasons, next_at, outcome in rows:
            try:
                raw = _record(json.loads(encoded))
                if (encoded != _json(raw, MAX_RECORD_BYTES) or job_id != raw['request']['job_id']
                        or state not in _STATES or _reasons(json.loads(reasons)) != reasons):
                    raise ValueError('Malformed stored waiting job.')
                _time(next_at)
                terminal = json.loads(outcome) if outcome is not None else None
                if terminal is not None and (_result(raw['request'], terminal) != outcome
                                              or state != terminal['state']):
                    raise ValueError('Malformed stored waiting result.')
                if (state in {'completed', 'failed', 'unconfirmed'} and terminal is None
                        or state not in {'completed', 'failed', 'unconfirmed'} and terminal is not None):
                    raise ValueError('Waiting state and stored result do not match.')
            except (TypeError, RecursionError, UnicodeError):
                raise ValueError('Malformed stored waiting job.') from None
            result[job_id] = dict(**raw, state='unconfirmed' if state == 'claimed' else state,
                                 reasons=json.loads(reasons), next_attempt_at=next_at,
                                 result=terminal, claimed=False)
        return last_now, result

    def _read(self):
        self._check()
        if not self.path.exists():
            return {}
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                return self._load(db)[1]
        except sqlite3.Error:
            raise ValueError('Waiting storage is unavailable or malformed.') from None

    def get(self, job_id):
        return self._read().get(_identity(job_id, 32))

    def list(self):
        return sorted(self._read().values(), key=lambda row: (row['approved_at'], row['request']['job_id']))

    def _publish(self, raw, now):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._check()
        fd, name = tempfile.mkstemp(prefix='.syntax-waiting-', suffix='.sqlite3', dir=self.path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary, timeout=5)) as db:
                self._limit(db)
                with db:
                    db.execute(_META_SQL)
                    db.execute(_JOBS_SQL)
                    db.execute('PRAGMA user_version=1')
                    db.execute('INSERT INTO metadata VALUES(1,?,?)', (self.owner, now))
                    self._insert(db, raw, now)
            self._check()
            try:
                os.link(temporary, self.path)
            except FileExistsError:
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _insert(db, raw, now):
        if db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= MAX_JOBS:
            raise ValueError('Waiting storage has reached its bounded job capacity.')
        state = 'waiting' if raw['approved_at'] <= now < raw['expires_at'] else 'expired'
        reasons = [] if state == 'waiting' else ['The approved waiting interval is not current.']
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL)',
                   (raw['request']['job_id'], _json(raw, MAX_RECORD_BYTES), state, _reasons(reasons), now))

    def _write(self, now, action):
        now = _time(now)
        self._check()
        if not self.path.exists():
            return action(None, {}, False)
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
                db.execute('PRAGMA trusted_schema=OFF')
                self._limit(db)
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    last_now, rows = self._load(db)
                    rolled_back = now < last_now
                    for job_id, row in rows.items():
                        if row['state'] == 'waiting' and (rolled_back or not row['approved_at'] <= now < row['expires_at']):
                            reason = 'The clock moved backwards; a new exact approval is required.' if rolled_back else 'The approved waiting interval expired.'
                            row.update(state='expired', reasons=[reason])
                            db.execute("UPDATE jobs SET state='expired',reasons=? WHERE job_id=?",
                                       (_reasons(row['reasons']), job_id))
                    db.execute('UPDATE metadata SET last_now=? WHERE singleton=1', (max(last_now, now),))
                    return action(db, rows, rolled_back)
        except sqlite3.Error:
            raise ValueError('Waiting storage is unavailable or malformed.') from None

    def enqueue(self, record, *, now):
        raw, now = _record(record), _time(now)
        self._check()
        try:
            if not self.path.exists() and self._publish(raw, now):
                return self.get(raw['request']['job_id'])
        except sqlite3.Error:
            raise ValueError('Waiting storage is unavailable or malformed.') from None
        def action(db, rows, rollback):
            job_id = raw['request']['job_id']
            existing = rows.get(job_id)
            if existing is not None:
                if _json({key: existing[key] for key in _RECORD_FIELDS}, MAX_RECORD_BYTES) != _json(raw, MAX_RECORD_BYTES):
                    raise ValueError('This job identity already belongs to another exact approval.')
                return existing
            if db is None:
                raise ValueError('Waiting storage disappeared before enqueue.')
            self._insert(db, raw, now)
            if rollback:
                db.execute("UPDATE jobs SET state='expired',reasons=? WHERE job_id=?",
                           (_reasons(['The clock moved backwards; a new exact approval is required.']), job_id))
            return self._load(db)[1][job_id]
        return self._write(now, action)

    def update_wait(self, job_id, reasons, *, now, next_attempt_at):
        job_id, encoded = _identity(job_id, 32), _reasons(reasons)
        now, next_attempt_at = _time(now), _time(next_attempt_at)
        if not now <= next_attempt_at <= now + MAX_LIFETIME:
            raise ValueError('The next attempt must be within the bounded waiting interval.')
        def action(db, rows, rollback):
            row = rows.get(job_id)
            if row is None:
                raise ValueError('No approved waiting job has that identity.')
            if row['state'] == 'waiting':
                row.update(reasons=json.loads(encoded), next_attempt_at=min(next_attempt_at, row['expires_at']))
                db.execute('UPDATE jobs SET reasons=?,next_attempt_at=? WHERE job_id=?',
                           (encoded, row['next_attempt_at'], job_id))
            return row
        return self._write(now, action)

    def cancel(self, job_id, *, now, reason='The owner cancelled this waiting job.'):
        job_id = _identity(job_id, 32)
        encoded = _reasons([reason])
        def action(db, rows, rollback):
            row = rows.get(job_id)
            if row is None:
                raise ValueError('No approved waiting job has that identity.')
            if row['state'] == 'waiting':
                row.update(state='cancelled', reasons=[reason])
                db.execute("UPDATE jobs SET state='cancelled',reasons=? WHERE job_id=?",
                           (encoded, job_id))
            return row
        return self._write(now, action)

    def expire(self, *, now):
        return self._write(now, lambda db, rows, rollback: list(rows.values()))

    def claim(self, job_id, *, now):
        job_id = _identity(job_id, 32)
        def action(db, rows, rollback):
            row = rows.get(job_id)
            if row is None:
                raise ValueError('No approved waiting job has that identity.')
            if row['state'] == 'waiting' and now >= row['next_attempt_at']:
                db.execute("UPDATE jobs SET state='claimed',reasons='[]' WHERE job_id=?", (job_id,))
                row.update(state='claimed', claimed=True, reasons=[])
            return row
        return self._write(now, action)

    def finish(self, job_id, result, *, now):
        job_id = _identity(job_id, 32)
        def action(db, rows, rollback):
            row = rows.get(job_id)
            if row is None or row['state'] not in {'unconfirmed', 'completed', 'failed'}:
                raise ValueError('An existing waiting job claim is required.')
            encoded = _result(row['request'], result)
            if row['state'] in {'completed', 'failed'}:
                if _json(row['result'], MAX_RESULT_BYTES) != encoded:
                    raise ValueError('A terminal waiting job result cannot be replaced.')
                return row
            row.update(state=result['state'], result=json.loads(encoded))
            db.execute('UPDATE jobs SET state=?,result=? WHERE job_id=?', (row['state'], encoded, job_id))
            return row
        return self._write(now, action)
