"""Local WebAuthn owner gate; face matching and biography never unlock it.

Protects this HTTP interface, not files from an OS user who can read the SSD.
Private passkey material stays with the authenticator. No camera access here.
"""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from .owner_pin import BackupPIN


class OwnerAccess:
    COOKIE = 'prometheus_owner'
    SESSION_SECONDS = 1800
    CHALLENGE_SECONDS = 120

    def __init__(self, root, origin, *, clock=time.monotonic):
        self.root = Path(root)
        self.origin = origin
        self.clock = clock
        self.lock = threading.RLock()
        self.pending = {}
        self.sessions = {}
        self.passkey_sessions = set()
        self.pin = BackupPIN(self.root / 'secrets')
        self.path = self.root/'public_keys.sqlite3'
        self.required = (self.root/'required.json').exists() or self.path.exists()
        self.ready = False
        if not self.required:
            return
        # Invalid/missing dependencies leave the gate locked; never fall back open.
        try:
            from fido2.server import Fido2Server
            from fido2.webauthn import PublicKeyCredentialRpEntity
            self.server = Fido2Server(PublicKeyCredentialRpEntity(id='localhost', name='Prometheus'),
                                     attestation='none', verify_origin=lambda value: value == origin)
            self.server.timeout = 120000
            self.root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS credentials(id BLOB PRIMARY KEY, data BLOB NOT NULL, counter INTEGER NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value BLOB NOT NULL)')
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('user_id', secrets.token_bytes(32)))
            self.ready = True
        except (ImportError, OSError, ValueError, sqlite3.Error):
            self.ready = False

    def _rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT id,data,counter FROM credentials').fetchall()

    def authenticated(self, cookie):
        if not self.required:
            return True
        with self.lock:
            expires = self.sessions.get(cookie or '', 0)
            if expires <= self.clock():
                self.sessions.pop(cookie or '', None)
                self.passkey_sessions.discard(cookie or '')
                return False
            return True

    def state(self, cookie=None):
        with self.lock:
            enrolled = bool(self._rows()) if self.ready else False
            return {'required': self.required, 'ready': self.ready,
                    'enrolled': enrolled,
                    'unlocked': self.authenticated(cookie), 'camera_is_authentication': False,
                    'passkey_verified': self.passkey_authenticated(cookie),
                    'pin': self.pin.status(self._owner_binding()) if enrolled else {'configured': False}}

    def logout(self, cookie):
        with self.lock:
            self.sessions.pop(cookie or '', None)
            self.passkey_sessions.discard(cookie or '')

    def passkey_authenticated(self, cookie):
        return bool(self.required and self.ready and self.authenticated(cookie)
                    and cookie in self.passkey_sessions)

    def _owner_binding(self):
        rows = self._rows()
        if not rows:
            raise ValueError('An enrolled owner is required.')
        with closing(sqlite3.connect(self.path)) as db:
            value = db.execute("SELECT value FROM settings WHERE key='user_id'").fetchone()
        if not value:
            raise ValueError('Owner binding is unavailable.')
        # PIN enrollment follows this owner identity, including when the owner
        # adds another genuine passkey. Scoped-action grants retain their own
        # stronger credential-set binding below.
        return hashlib.sha256(b'prometheus-backup-pin\0' + value[0]).hexdigest()

    def _new_session(self, *, passkey=False):
        now = self.clock()
        self.sessions = {key: expires for key, expires in self.sessions.items() if expires > now}
        self.passkey_sessions.intersection_update(self.sessions)
        session = secrets.token_urlsafe(32)
        self.sessions[session] = now + self.SESSION_SECONDS
        if passkey:
            self.passkey_sessions.add(session)
        return {'unlocked': True, 'expires_in': self.SESSION_SECONDS}, session

    def configure_pin(self, pin, confirmation, cookie=None):
        with self.lock:
            if not self.passkey_authenticated(cookie):
                raise ValueError('Sign in with your existing passkey before setting or changing the backup PIN.')
            self.pin._validate(pin, new=True)
            self.pin._validate(confirmation)
            if not secrets.compare_digest(pin, confirmation):
                raise ValueError('The two PIN entries must match.')
            self.pin.configure(pin, self._owner_binding())
            self.sessions = {key: value for key, value in self.sessions.items() if key in self.passkey_sessions}
            return {'pin_configured': True}

    def disable_pin(self, cookie=None):
        with self.lock:
            if not self.passkey_authenticated(cookie):
                raise ValueError('Sign in with your passkey before removing the backup PIN.')
            self.pin.disable()
            self.sessions = {key: value for key, value in self.sessions.items() if key in self.passkey_sessions}
            return {'pin_configured': False}

    def unlock_pin(self, pin):
        with self.lock:
            if not self.required or not self.ready:
                raise ValueError('Owner verification is unavailable; access stays locked.')
            self.pin.verify(pin, self._owner_binding())
            return self._new_session()

    def boundary_state(self, cookie=None):
        """Safe in-memory observation; no credentials, session ID or storage I/O."""
        with self.lock:
            remaining = max(0, self.sessions.get(cookie or '', 0) - self.clock())
            return {'required': self.required, 'ready': self.ready,
                    'unlocked': not self.required or remaining > 0,
                    'expires_in_seconds': remaining if self.required and remaining > 0 else None}

    @contextmanager
    def grant_owner(self, cookie):
        """Server-derived owner binding, serialized with logout/key changes.

        Unconfigured owner access deliberately cannot mint/use grants. The
        ordinary application's legacy optional-owner behavior is unchanged.
        """
        with self.lock:
            if not self.required or not self.ready or not self.authenticated(cookie):
                raise ValueError('Unlock a configured owner before managing scoped grants.')
            rows = self._rows()
            if not rows:
                raise ValueError('An enrolled owner is required for scoped grants.')
            with closing(sqlite3.connect(self.path)) as db:
                value = db.execute("SELECT value FROM settings WHERE key='user_id'").fetchone()
            if not value:
                raise ValueError('Owner binding is unavailable.')
            digest = hashlib.sha256(value[0])
            for row in sorted(rows, key=lambda row: row[0]):
                digest.update(bytes(row[1]))
            yield digest.hexdigest()

    def begin(self, kind, cookie=None, setup_ticket=''):
        if not self.ready or kind not in {'register', 'authenticate'}:
            raise ValueError('Owner verification is unavailable; access stays locked.')
        from fido2.webauthn import AttestedCredentialData
        with self.lock:
            rows = self._rows()
            credentials = [AttestedCredentialData(row[1]) for row in rows]
            if kind == 'register':
                if rows:
                    if not self.passkey_authenticated(cookie):
                        raise ValueError('Unlock with an existing passkey before adding another.')
                    if len(rows) >= 5:
                        raise ValueError('Five passkeys are already registered.')
                else:
                    config = json.loads((self.root/'required.json').read_text(encoding='utf-8'))
                    expected = config.get('setup_ticket_sha256', '')
                    if not expected or not secrets.compare_digest(hashlib.sha256(setup_ticket.encode()).hexdigest(), expected):
                        raise ValueError('Open the private owner setup launcher on the main computer.')
                with closing(sqlite3.connect(self.path)) as db:
                    user_id = db.execute("SELECT value FROM settings WHERE key='user_id'").fetchone()[0]
                options, state = self.server.register_begin(
                    {'id': user_id, 'name': 'prometheus-owner', 'displayName': 'Prometheus owner'},
                    credentials, resident_key_requirement='preferred', user_verification='required')
            else:
                if not rows:
                    raise ValueError('Register your first passkey before unlocking.')
                options, state = self.server.authenticate_begin(credentials, user_verification='required')
            now = self.clock()
            self.pending = {key: row for key, row in self.pending.items() if row['expires'] > now}
            if len(self.pending) >= 8:
                raise ValueError('Too many sign-in attempts. Wait two minutes and retry.')
            handle = secrets.token_urlsafe(32)
            self.pending[handle] = {'kind': kind, 'state': state, 'expires': now+self.CHALLENGE_SECONDS,
                                    'cookie': cookie, 'first': not rows}
            return {'request_id': handle, 'options': dict(options)}

    def complete(self, handle, response, cookie=None):
        from fido2.webauthn import AttestedCredentialData, AuthenticationResponse, RegistrationResponse
        if not self.ready or not isinstance(response, dict):
            raise ValueError('Invalid verification response.')
        with self.lock:
            pending = self.pending.pop(handle, None)
            if pending is None or pending['expires'] <= self.clock() or pending['cookie'] != cookie:
                raise ValueError('Verification expired or was already used. Try again.')
            rows = self._rows()
            if pending['kind'] == 'register':
                if pending['first'] and rows:
                    raise ValueError('An owner passkey was already registered.')
                if not pending['first'] and not self.passkey_authenticated(cookie):
                    raise ValueError('Unlock again before adding a passkey.')
                parsed = RegistrationResponse.from_dict(response)
                if parsed.response.client_data.cross_origin:
                    raise ValueError('Cross-origin verification is not allowed.')
                auth = self.server.register_complete(pending['state'], parsed)
                credential = auth.credential_data
                # Enrollment requires a follow-up signed assertion before access is granted.
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('INSERT INTO credentials VALUES (?,?,?)',
                               (credential.credential_id, bytes(credential), auth.counter))
                return {'registered': True, 'unlocked': False}, None
            parsed = AuthenticationResponse.from_dict(response)
            if parsed.response.client_data.cross_origin:
                raise ValueError('Cross-origin verification is not allowed.')
            credential = self.server.authenticate_complete(pending['state'],
                [AttestedCredentialData(row[1]) for row in rows], parsed)
            counter = parsed.response.authenticator_data.counter
            old = next(row[2] for row in rows if row[0] == credential.credential_id)
            if (old or counter) and counter <= old:
                raise ValueError('Passkey counter did not advance; access stays locked.')
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('UPDATE credentials SET counter=? WHERE id=?', (counter, credential.credential_id))
            try:
                self.pin.reset_attempts(self._owner_binding())
            except (OSError, ValueError, sqlite3.Error):
                pass  # Broken optional PIN storage must not prevent valid passkey recovery.
            return self._new_session(passkey=True)
