"""Optional local backup PIN. Not a TPM, hardware key, or disk-encryption lock.

The verifier lives in the backup-excluded secrets directory. Five failures
disable PIN sign-in until a genuine passkey assertion resets the counter.
An OS user able to modify this code/storage is outside this UI lock's boundary.
"""
from contextlib import closing, contextmanager
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time


class BackupPIN:
    MAX_FAILURES = 5
    COMMON = {str(n) * 4 for n in range(10)} | {'1234', '4321', '1212', '2580'}

    def __init__(self, root, *, clock=time.time):
        self.root = Path(root)
        self.path = self.root / 'backup-pin.sqlite3'
        self.clock = clock

    @staticmethod
    def _validate(pin, *, new=False):
        if not isinstance(pin, str) or re.fullmatch(r'[0-9]{4}', pin) is None:
            raise ValueError('Enter exactly four digits.')
        if new and pin in BackupPIN.COMMON:
            raise ValueError('Choose a less predictable PIN; avoid repeated digits and common sequences.')

    @staticmethod
    def _derive(pin, salt):
        return hashlib.scrypt(pin.encode('ascii'), salt=salt, n=32768, r=8, p=1,
                              dklen=32, maxmem=64 * 1024 * 1024)

    @contextmanager
    def _db(self):
        if self.root.is_symlink():
            raise ValueError('PIN storage is unavailable.')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('PIN storage is unavailable.')
            if hasattr(os, 'getuid') and info.st_uid != os.getuid():
                raise ValueError('PIN storage belongs to another account.')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=3)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS pin (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, salt BLOB NOT NULL, verifier BLOB NOT NULL, failures INTEGER NOT NULL, retry_at REAL NOT NULL)')
            db.commit()
            yield db

    def status(self, owner):
        absent = {'configured': False, 'locked': False, 'remaining': 5, 'retry_after': 0}
        if not self.path.exists():
            return absent
        try:
            with self._db() as db:
                row = db.execute('SELECT owner,failures,retry_at FROM pin WHERE id=1').fetchone()
            if row is None or row[0] != owner:
                return absent
            return {'configured': True, 'locked': row[1] >= self.MAX_FAILURES,
                    'remaining': max(0, self.MAX_FAILURES - row[1]),
                    'retry_after': math.ceil(max(0, row[2] - self.clock()))}
        except (OSError, ValueError, sqlite3.Error):
            return {**absent, 'locked': True, 'unavailable': True, 'remaining': 0}

    def configure(self, pin, owner):
        self._validate(pin, new=True)
        salt = secrets.token_bytes(32)
        verifier = self._derive(pin, salt)
        with self._db() as db, db:
            db.execute('INSERT OR REPLACE INTO pin VALUES (1,?,?,?,?,?)',
                       (owner, salt, verifier, 0, 0))

    def verify(self, pin, owner):
        self._validate(pin)
        if not self.path.exists():
            raise ValueError('No backup PIN is configured. Use your passkey.')
        # One transaction serializes verification, failed attempts and resets,
        # including callers from a second process. Failures are committed before
        # returning an error, and survive application restarts.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner,salt,verifier,failures,retry_at FROM pin WHERE id=1').fetchone()
            if row is None or row[0] != owner:
                raise ValueError('No backup PIN is configured for this owner. Use your passkey.')
            if row[3] >= self.MAX_FAILURES:
                raise ValueError('PIN locked after five incorrect attempts. Sign in with your passkey to restore PIN access.')
            if self.clock() < row[4]:
                raise ValueError('Wait before trying the PIN again.')
            correct = secrets.compare_digest(self._derive(pin, row[1]), row[2])
            failures = 0 if correct else row[3] + 1
            db.execute('UPDATE pin SET failures=?,retry_at=? WHERE id=1',
                       (failures, 0 if correct else self.clock() + 2 ** failures))
            db.commit()
        if not correct:
            if failures >= self.MAX_FAILURES:
                raise ValueError('PIN locked after five incorrect attempts. Sign in with your passkey to restore PIN access.')
            raise ValueError('Incorrect PIN. ' + str(self.MAX_FAILURES - failures) + ' attempts remain.')

    def reset_attempts(self, owner):
        if self.path.exists():
            with self._db() as db, db:
                db.execute('UPDATE pin SET failures=0,retry_at=0 WHERE owner=?', (owner,))

    def disable(self):
        if self.path.exists():
            with self._db() as db, db:
                db.execute('DELETE FROM pin')
