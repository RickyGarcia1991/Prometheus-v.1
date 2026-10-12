"""Exact-request, owner-authorized Core grants with durable fail-closed accounting.

Grants remain usable across ordinary process restarts on the same host and owner.
A separate host continuity witness detects rolled-back/partially written state.
The OS protects both files; administrator or same-OS-account rollback of all host authority
state before process start is outside this application boundary.
"""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from .application_policy import registered_decision, revision
from .guardrails import Decision

SUPPORTED = frozenset({('research', 'public'), ('project', 'tests')})
MAX_SECONDS = 900
MAX_USES = 5


def host_binding():
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                           winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value = winreg.QueryValueEx(key, 'MachineGuid')[0]
    else:
        value = Path('/etc/machine-id').read_text().strip()
    if not isinstance(value, str) or len(value) < 16:
        raise ValueError('Stable host binding is unavailable; grants are disabled.')
    return hashlib.sha256(value.encode()).hexdigest()


def scope_target(spec, arguments):
    if spec.key == ('project', 'tests'):
        from .builtin_tools import _project_root
        from .coding_workspace import _no_links
        root = Path(_project_root()).absolute()
        _no_links(root)
        root = root.resolve()
        selected = root/arguments.get('target', 'tests')
        _no_links(selected)
        selected = selected.resolve()
        if selected == root or not selected.is_relative_to(root):
            raise ValueError('Grant target escapes the application project root.')
        return str(selected)
    return arguments['provider']


def scope(spec, arguments):
    if spec.key not in SUPPORTED or not isinstance(arguments, dict):
        raise ValueError('This capability does not support scoped grants.')
    request = spec.request('Execute the exact owner-reviewed request.', arguments)
    args = dict(request.arguments)
    if spec.key == ('project', 'tests'):
        args.setdefault('target', 'tests')
        # Preserve executor's stricter path check; do not reinterpret glob syntax.
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_/.-]*', args['target']) or '..' in args['target'].split('/'):
            raise ValueError('Choose one exact bounded test target.')
    else:
        from .public_resources import PROVIDERS
        if args['provider'] not in PROVIDERS:
            raise ValueError('Choose an existing public provider.')
    if any(any(c in str(value) for c in ('*', '?', '\x00')) for value in args.values()):
        raise ValueError('Wildcards are not permitted in exact grants.')
    request = spec.request(request.summary, args)
    raw = json.dumps({'worker': spec.worker, 'tool': spec.name, 'arguments': args, 'target': scope_target(spec, args)},
                     sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return request, hashlib.sha256(raw.encode()).hexdigest()


class GrantStore:
    def __init__(self, path, *, host=host_binding, policy_revision=revision, wall=time.time, monotonic=time.monotonic):
        self.path = Path(path)
        self.host = host
        self.policy_revision = policy_revision
        self.wall, self.monotonic = wall, monotonic
        self.epoch = secrets.token_hex(32)
        self.lock = threading.RLock()
        self.pending = {}
        from .host_authority import HostLedger
        self.ledger = HostLedger(self.path, wall, monotonic)
        self.last_wall = wall()
        self.last_mono = monotonic()

    def _db(self):
        return self.ledger.transaction()

    def _clock(self):
        wall, mono = self.wall(), self.monotonic()
        if wall < self.last_wall or mono < self.last_mono:
            self.pending.clear()
            with self._db():
                pass  # Retires prior grants and records the new clock epoch.
            self.last_wall, self.last_mono = wall, mono
            raise ValueError('Clock moved backwards; prior grants were retired. Review a new grant.')
        self.last_wall, self.last_mono = wall, mono
        return wall, mono

    def _locked_clock(self):
        # Called after ledger lock acquisition, which may have waited ten
        # seconds. Never use a pre-wait timestamp to authorize an effect.
        wall, mono = self.wall(), self.monotonic()
        if wall < self.last_wall or mono < self.last_mono:
            self.pending.clear()
            raise ValueError('Clock moved backwards while waiting for grant authority.')
        self.last_wall, self.last_mono = wall, mono
        return wall, mono

    def review(self, spec, arguments, *, owner, seconds, uses):
        if not owner or type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS or type(uses) is not int or not 1 <= uses <= MAX_USES:
            raise ValueError('An authenticated owner, 1–900 seconds and 1–5 uses are required.')
        request, digest = scope(spec, arguments)
        if registered_decision(spec, request).decision is Decision.DENY:
            raise ValueError('Application policy denies this capability.')
        with self.lock:
            stamp, mono = self._clock()
            self.pending = {k: v for k, v in self.pending.items() if v['deadline'] > mono}
            if len(self.pending) >= 8:
                raise ValueError('Too many pending grant reviews.')
            handle = secrets.token_hex(24)
            self.pending[handle] = {'scope': digest, 'capability': '/'.join(spec.key), 'owner': owner,
                'host': self.host(), 'revision': self.policy_revision(), 'deadline': mono+120,
                'seconds': seconds, 'uses': uses, 'target': scope_target(spec, request.arguments)}
            return {'review_id': handle, 'capability': '/'.join(spec.key), 'scope_sha256': digest,
                'seconds': seconds, 'uses': uses, 'target': scope_target(spec, request.arguments), 'policy_revision': self.policy_revision(),
                'notice': 'Review the exact arguments you entered. This exact-scope grant is bound to this host and owner; target and owner checks still apply.'}

    def issue(self, handle, digest, *, owner):
        with self.lock:
            stamp, mono = self._clock()
            pending = self.pending.pop(handle, None)
            if (not pending or pending['deadline'] <= mono or pending['owner'] != owner
                    or not secrets.compare_digest(pending['scope'], digest)
                    or pending['host'] != self.host() or pending['revision'] != self.policy_revision()):
                raise ValueError('Grant review expired, changed or was already used.')
            ident = secrets.token_hex(24)
            with self._db() as db:
                stamp, mono = self._locked_clock()
                if pending['deadline'] <= mono or pending['host'] != self.host() or pending['revision'] != self.policy_revision():
                    raise ValueError('Grant review changed or expired while waiting for authority.')
                db.execute('INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, pending['capability'], digest, pending['host'], owner, pending['revision'],
                     self.epoch, stamp, stamp+pending['seconds'], pending['uses'], 'active', mono+pending['seconds'], pending['target']))
            return {'id': ident, 'capability': pending['capability'], 'scope_sha256': digest,
                    'expires_at': stamp+pending['seconds'], 'remaining': pending['uses'], 'target': pending['target'], 'state': 'active'}

    def _refresh(self, db, owner, stamp, mono):
        for row in db.execute("SELECT * FROM grants WHERE state='active'").fetchall():
            if row['owner'] != owner:
                continue
            state = None
            if row['host'] != self.host(): state = 'binding_invalid'
            elif row['revision'] != self.policy_revision(): state = 'policy_invalid'
            elif row['expires'] <= stamp or row['mono_expires'] <= mono: state = 'expired'
            elif row['remaining'] <= 0: state = 'exhausted'
            if state: db.execute('UPDATE grants SET state=? WHERE id=?', (state, row['id']))

    def inspect(self, *, owner):
        if not owner: raise ValueError('Authenticated owner required.')
        with self.lock:
            stamp, mono = self._clock()
            if not any(path.exists() for path in (self.path, self.ledger.witness, self.ledger.poison)): return []
            with self._db() as db:
                stamp, mono = self._locked_clock()
                self._refresh(db, owner, stamp, mono)
                rows = db.execute('SELECT id,capability,target,scope,created,expires,remaining,state FROM grants WHERE owner=? ORDER BY created DESC LIMIT 100', (owner,)).fetchall()
                return [dict(row) for row in rows]

    def revoke(self, ident, *, owner):
        if not owner: raise ValueError('Authenticated owner required.')
        with self.lock, self._db() as db:
            changed = db.execute("UPDATE grants SET state='revoked' WHERE id=? AND owner=?", (ident, owner)).rowcount
            if not changed: raise ValueError('Unknown owner grant.')
        return {'id': ident, 'state': 'revoked'}

    def run(self, ident, invocation, spec, arguments, *, owner):
        if not owner or not isinstance(invocation, str) or not re.fullmatch('[a-f0-9]{32}', invocation):
            raise ValueError('Authenticated owner and fresh invocation identifier required.')
        request, digest = scope(spec, arguments)
        with self.lock:
            stamp, mono = self._clock()
            invalid = False
            with self._db() as db:
                stamp, mono = self._locked_clock()
                self._refresh(db, owner, stamp, mono)
                row = db.execute('SELECT * FROM grants WHERE id=?', (ident,)).fetchone()
                invalid = (row is None or row['state'] != 'active' or row['owner'] != owner
                           or row['scope'] != digest or row['capability'] != '/'.join(spec.key))
                if not invalid:
                    if registered_decision(spec, request).decision is Decision.DENY:
                        raise ValueError('Application policy denies this capability.')
                    try:
                        db.execute('INSERT INTO attempts VALUES (?,?,?)', (invocation, ident, stamp))
                    except sqlite3.IntegrityError as error:
                        raise ValueError('Invocation was already consumed; do not replay it.') from error
                    db.execute('UPDATE grants SET remaining=remaining-1 WHERE id=?', (ident,))
            if invalid:
                raise ValueError('Grant is unavailable or does not match this exact request.')
            # Durable budget/continuity commit precedes effects. The second
            # transaction serializes dispatch with revocation across processes.
            stamp, mono = self._clock()
            with self._db() as db:
                stamp, mono = self._locked_clock()
                row = db.execute('SELECT * FROM grants WHERE id=?', (ident,)).fetchone()
                reserved = db.execute('SELECT grant_id FROM attempts WHERE id=?', (invocation,)).fetchone()
                if (row['state'] not in {'active', 'exhausted'} or reserved is None or reserved['grant_id'] != ident
                        or row['host'] != self.host() or row['owner'] != owner
                        or row['revision'] != self.policy_revision() or stamp >= row['expires']
                        or mono >= row['mono_expires']):
                    raise ValueError('Grant became invalid before dispatch; the attempt remains spent.')
                if registered_decision(spec, request).decision is Decision.DENY:
                    raise ValueError('Application policy denies this capability.')
                return spec.executor(request)
