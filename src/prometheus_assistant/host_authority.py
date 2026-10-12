"""Host-local grant continuity; fail closed on partial writes or state rollback.

The DB and independent witness are one authority unit, excluded from portable
backups. Restoring either independently is detected, including while running.
An administrator or same-OS-account process restoring ALL host authority state before process start is
outside this boundary; without an external monotonic authority it is impossible
to distinguish that from the original host. This module makes no such promise.
"""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time


def authority_path():
    if os.name == 'nt':
        root = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))
    else:
        root = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))
    return root/'Prometheus/Authority/grants.sqlite3'


class HostLedger:
    def __init__(self, path, wall, monotonic):
        self.path = Path(path)
        self.witness = self.path.with_suffix('.continuity.json')
        self.poison = self.path.with_suffix('.invalid.json')
        self.lock_path = self.path.with_suffix('.lock')
        self.wall, self.monotonic = wall, monotonic
        self.lock = threading.RLock()
        self.seen = None

    def _atomic(self, path, value):
        temp = path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)

    def invalidate(self, reason):
        self._atomic(self.poison, {'reason': reason, 'invalidated': self.wall()})
        raise ValueError('Host grant continuity is invalid; no grant can authorize execution. '+reason)

    @contextmanager
    def _file_lock(self):
        from .coding_workspace import _no_links
        for path in (self.path, self.witness, self.poison, self.lock_path): _no_links(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.lock_path.open('a+b') as stream:
            if stream.tell() == 0: stream.write(b'0'); stream.flush()
            deadline = time.monotonic()+10
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline: raise ValueError('Host grant authority is busy; no action dispatched.')
                    time.sleep(.025)
            try: yield
            finally:
                stream.seek(0)
                if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(stream, fcntl.LOCK_UN)

    @staticmethod
    def _state(db):
        rows = {}
        for table in ('grants','attempts'):
            rows[table] = [list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY id')]
        return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    @contextmanager
    def transaction(self):
        with self._file_lock():
            if self.poison.exists(): raise ValueError('Host grant continuity was invalidated; grants remain disabled.')
            if self.path.exists() != self.witness.exists(): self.invalidate('A required authority file is missing.')
            fresh = not self.path.exists()
            with closing(sqlite3.connect(self.path, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
                if fresh:
                    db.execute('CREATE TABLE grants(id TEXT PRIMARY KEY, capability TEXT NOT NULL, scope TEXT NOT NULL, host TEXT NOT NULL, owner TEXT NOT NULL, revision TEXT NOT NULL, epoch TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, remaining INTEGER NOT NULL, state TEXT NOT NULL, mono_expires REAL NOT NULL, target TEXT NOT NULL)')
                    db.execute('CREATE TABLE attempts(id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, created REAL NOT NULL)')
                    db.execute('CREATE TABLE continuity(id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT, seq INTEGER, state TEXT, wall REAL, mono REAL)')
                    initial = {'epoch':secrets.token_hex(32),'seq':0,'state':self._state(db),'wall':self.wall(),'mono':self.monotonic()}
                    db.execute('INSERT INTO continuity VALUES (1,:epoch,:seq,:state,:wall,:mono)',initial)
                    db.commit();self._atomic(self.witness,initial)
                try:
                    mark = dict(db.execute('SELECT epoch,seq,state,wall,mono FROM continuity WHERE id=1').fetchone())
                    witness = json.loads(self.witness.read_text(encoding='utf-8'))
                except (TypeError,ValueError,OSError,sqlite3.Error): self.invalidate('Authority metadata is unreadable.')
                if mark != witness or mark['state'] != self._state(db): self.invalidate('Authority database and continuity witness differ.')
                if self.seen and (mark['epoch'] != self.seen['epoch'] or mark['seq'] < self.seen['seq']): self.invalidate('Running authority state was rolled back.')
                stamp,mono = self.wall(),self.monotonic()
                try:
                    db.execute('BEGIN IMMEDIATE')
                    if stamp < mark['wall'] or mono < mark['mono']:
                        # Reboot/clock reset retires existing authority, but the
                        # healthy ledger remains usable for NEW owner reviews.
                        db.execute("UPDATE grants SET state='clock_invalid' WHERE state='active'")
                    yield db
                    current = {'epoch':mark['epoch'],'seq':mark['seq']+1,'state':self._state(db),'wall':stamp,'mono':mono}
                    db.execute('UPDATE continuity SET epoch=:epoch,seq=:seq,state=:state,wall=:wall,mono=:mono WHERE id=1',current)
                    db.commit()
                    # Crash between commit and witness invalidates the unit. No
                    # grant-backed effect may occur until this call completes.
                    self._atomic(self.witness,current)
                    self.seen = current
                except BaseException:
                    db.rollback()
                    raise
