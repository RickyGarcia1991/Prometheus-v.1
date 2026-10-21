"""Durable once-only claims, not execution or transport authentication.

A persisted claim with no receipt is unconfirmed forever; callers must not
automatically rerun it. The trusted caller controls who may claim and finish.
No source text is retained. Deleting/replacing this database is outside the
once-only guarantee and is not an operation provided by this module.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from .remote_job_protocol import normalize_request, validate_receipt

MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_BYTES = 8192
MAX_CLAIMS = 10000


def _no_links(path):
    """Reject ancestor symlinks and all Windows reparse points, including junctions."""
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError('Linked paths, junctions and reparse points are not accepted.')


class JobStore:
    def __init__(self, absolute_db_path):
        self.path = Path(absolute_db_path)
        if not self.path.is_absolute():
            raise ValueError('Job storage requires an absolute path.')
        self._check()

    def _check(self):
        for path in (self.path, *(Path(str(self.path)+suffix) for suffix in ('-journal','-wal','-shm'))):
            _no_links(path)
            if path.exists() and (not path.is_file() or path.stat().st_size > MAX_DATABASE_BYTES):
                raise ValueError('Job storage exceeds its limit or is malformed.')

    @staticmethod
    def _schema(db):
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Unsupported or malformed job storage.')
        columns = db.execute('PRAGMA table_info(jobs)').fetchall()
        if ([row[1] for row in columns] != ['job_id','request_digest','device_id','worker_digest','receipt']
                or columns[0][5] != 1 or any(row[5] for row in columns[1:])):
            raise ValueError('Malformed job storage schema.')

    @staticmethod
    def _limit(db):
        # Enforce the cap during writes, rather than discovering excess growth
        # after an irreversible claim has already been granted.
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        pages = MAX_DATABASE_BYTES // page_size
        if pages < 1 or db.execute('PRAGMA page_count').fetchone()[0] > pages:
            raise ValueError('Job storage exceeds its bounded capacity.')
        db.execute('PRAGMA max_page_count=' + str(pages))

    @staticmethod
    def _terminal(req, receipt):
        evidence = validate_receipt(req, receipt)
        if (evidence['status'] == 'incomplete' or receipt.get('state') not in ('completed','failed')):
            raise ValueError('An exact valid terminal receipt is required.')
        encoded = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > MAX_RECEIPT_BYTES:
            raise ValueError('Job receipt exceeds its limit.')
        return encoded

    def _read(self, db, req):
        self._schema(db)
        rows = db.execute('SELECT request_digest,device_id,worker_digest,receipt FROM jobs WHERE job_id=? LIMIT 2',
                          (req['job_id'],)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError('Duplicate job identities in storage.')
        digest, device, worker, encoded = rows[0]
        if (digest, device, worker) != (req['request_digest'],req['device_id'],req['worker_digest']):
            raise ValueError('Job identity already belongs to a different exact request.')
        receipt = None
        if encoded is not None:
            if not isinstance(encoded,str) or len(encoded.encode('utf-8')) > MAX_RECEIPT_BYTES:
                raise ValueError('Malformed stored job receipt.')
            try:
                receipt = json.loads(encoded)
            except (ValueError, RecursionError):
                raise ValueError('Malformed stored job receipt.') from None
            if self._terminal(req,receipt) != encoded:
                raise ValueError('Malformed noncanonical stored receipt.')
        return dict(claimed=False,state=receipt['state'] if receipt else 'unconfirmed',receipt=receipt)

    @staticmethod
    def _insert(db, req):
        if db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= MAX_CLAIMS:
            raise ValueError('Job claim storage has reached its bounded capacity.')
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,NULL)',
                   tuple(req[key] for key in ('job_id','request_digest','device_id','worker_digest')))

    def _publish(self, req):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._check()
        fd, name = tempfile.mkstemp(prefix='.remote-jobs-',suffix='.sqlite3',dir=self.path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary,timeout=5)) as db:
                self._limit(db)
                with db:
                    db.execute('CREATE TABLE jobs(job_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, '
                               'device_id TEXT NOT NULL, worker_digest TEXT NOT NULL, receipt TEXT)')
                    db.execute('PRAGMA user_version=1')
                    self._insert(db,req)
            self._check()
            try:
                os.link(temporary,self.path)
            except FileExistsError:
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)

    def lookup(self, request):
        """Read a detached receipt or unconfirmed claim; missing storage is inert."""
        req = normalize_request(request)
        self._check()
        if not self.path.exists():
            return None
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                return self._read(db,req)
        except sqlite3.Error:
            raise ValueError('Job storage is unavailable or malformed.') from None

    def claim(self, request):
        """Return claimed=True exactly once per persisted job ID and digest."""
        req = normalize_request(request)
        self._check()
        try:
            if not self.path.exists() and self._publish(req):
                return dict(claimed=True,state='claimed',receipt=None)
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=5)) as db:
                self._limit(db)
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    existing = self._read(db,req)
                    if existing is not None:
                        return existing
                    self._insert(db,req)
                return dict(claimed=True,state='claimed',receipt=None)
        except sqlite3.Error:
            raise ValueError('Job storage is unavailable or malformed.') from None

    def finish(self, request, receipt):
        """Persist an exact terminal receipt once; identical retries are inert."""
        req = normalize_request(request)
        encoded = self._terminal(req,receipt)
        self._check()
        if not self.path.exists():
            raise ValueError('An existing exact job claim is required.')
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=5)) as db:
                self._limit(db)
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    existing = self._read(db,req)
                    if existing is None:
                        raise ValueError('An existing exact job claim is required.')
                    if existing['receipt'] is not None:
                        if self._terminal(req,existing['receipt']) != encoded:
                            raise ValueError('A terminal receipt cannot be replaced.')
                        return existing
                    db.execute('UPDATE jobs SET receipt=? WHERE job_id=?',(encoded,req['job_id']))
                return dict(claimed=False,state=receipt['state'],receipt=json.loads(encoded))
        except sqlite3.Error:
            raise ValueError('Job storage is unavailable or malformed.') from None
