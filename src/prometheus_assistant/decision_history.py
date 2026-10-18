"""Bounded owner decision audit, never execution authority or restored grants.

SQLite transactions serialize concurrent writers. Capacity is a hard limit;
receipts are never evicted. History failure is explicit, not an empty success.
Only intentional owner feedback is retained; no proposal payload is accepted.
"""
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3

MAX_RECEIPTS = 4096
MAX_RECEIPT_BYTES = 16 * 1024
MAX_DATABASE_BYTES = 32 * 1024 * 1024
KINDS = frozenset({'project_edit','project_task','idle_config','scheduled_syntax',
    'remote_syntax','self_improve','integration','integration_access','runtime_config','other'})
DECISIONS = frozenset({'approve','reject','request_changes'})
FIELDS = frozenset({'schema_version','id','owner','fingerprint','session','kind','decision','feedback','decided_at'})
_SCHEMA = 'CREATE TABLE decisions (owner TEXT NOT NULL, id TEXT NOT NULL, decided_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,id))'


def _identity(value, size):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value):
        raise ValueError('An exact owner decision identity is required.')


def validate_feedback(decision, kind, feedback):
    if type(feedback) is not str or '\x00' in feedback or len(feedback) > 2000:
        raise ValueError('Change feedback must be text without NUL, at most 2000 characters.')
    if decision == 'request_changes':
        if kind != 'project_edit' or not feedback.strip():
            raise ValueError('Request changes requires a project edit and nonempty feedback.')
        return feedback.strip()
    if feedback:
        raise ValueError('Feedback is only allowed when requesting project changes.')
    return ''


def _event(row):
    if type(row) is not dict or set(row) != FIELDS or type(row['schema_version']) is not int or row['schema_version'] != 1:
        raise ValueError('Invalid decision receipt schema.')
    for name,size in [('id',32),('owner',64),('fingerprint',64),('session',32)]:
        _identity(row[name],size)
    if type(row['kind']) is not str or row['kind'] not in KINDS or type(row['decision']) is not str or row['decision'] not in DECISIONS:
        raise ValueError('Invalid decision receipt kind or decision.')
    if validate_feedback(row['decision'],row['kind'],row['feedback']) != row['feedback']:
        raise ValueError('Decision feedback must already be trimmed.')
    value=row['decided_at']
    if type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError('Invalid decision timestamp.')
    encoded=json.dumps(row,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_RECEIPT_BYTES:
        raise ValueError('Decision receipt exceeds its size bound.')
    return encoded


class DecisionHistory:
    def __init__(self, root):
        self.root=Path(root).absolute()
        self.path=self.root/'decisions.sqlite3'
        self._plain()

    def _plain(self):
        for target in [self.path,Path(str(self.path)+'-journal'),Path(str(self.path)+'-wal'),Path(str(self.path)+'-shm')]:
            for path in (target,*target.parents):
                if path.is_symlink() or getattr(path,'is_junction',lambda:False)() or (path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400):
                    raise ValueError('Linked decision history is refused.')
            if target.exists() and (not target.is_file() or target.stat().st_size > MAX_DATABASE_BYTES):
                raise ValueError('Decision history exceeds its size bound; archive or maintain it before deciding.')

    @staticmethod
    def _schema(db, *, create=False):
        version=db.execute('PRAGMA user_version').fetchone()[0]
        rows=db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        if version==0 and not rows and create:
            db.execute(_SCHEMA)
            db.execute('PRAGMA user_version=1')
            return
        if version!=1 or rows != [('decisions',_SCHEMA)]:
            raise ValueError('Unrecognized decision history schema.')

    @staticmethod
    def _rows(db):
        rows=db.execute('SELECT owner,id,decided_at,payload FROM decisions ORDER BY decided_at DESC,owner,id LIMIT ?', (MAX_RECEIPTS+1,)).fetchall()
        if len(rows)>MAX_RECEIPTS:
            raise ValueError('Decision history exceeds its receipt capacity; archive or maintain it.')
        result=[]
        for owner,ident,decided,payload in rows:
            if type(payload) is not str or len(payload)>MAX_RECEIPT_BYTES:
                raise ValueError('Invalid bounded decision receipt.')
            row=json.loads(payload)
            if _event(row)!=payload or (owner,ident,decided)!=(row['owner'],row['id'],row['decided_at']):
                raise ValueError('Decision receipt identity or canonical bytes differ.')
            result.append(row)
        return result

    def record(self, event):
        payload=_event(event)
        self._plain()
        self.root.mkdir(parents=True,exist_ok=True)
        self._plain()
        try:
            with closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                db.execute('PRAGMA synchronous=FULL')
                page=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//page))
                db.execute('BEGIN IMMEDIATE')
                self._schema(db,create=True)
                rows=self._rows(db)
                prior=next((row for row in rows if row['owner']==event['owner'] and row['id']==event['id']),None)
                if prior is not None:
                    if _event(prior)!=payload:
                        raise ValueError('This decision identity already has a conflicting receipt.')
                    return False
                if len(rows)>=MAX_RECEIPTS:
                    raise ValueError('Decision history is full; archive or maintain it before deciding.')
                db.execute('INSERT INTO decisions VALUES (?,?,?,?)',(event['owner'],event['id'],event['decided_at'],payload))
            return True
        except sqlite3.Error as error:
            raise ValueError('Decision history could not be saved; retry after storage maintenance.') from error

    def history(self, owner, limit=50):
        _identity(owner,64)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Decision history limit must be between 1 and 50.')
        self._plain()
        if not self.path.exists():
            return []
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db)
                rows=self._rows(db)
            return [{key:value for key,value in row.items() if key!='owner'} for row in rows if row['owner']==owner][:limit]
        except (sqlite3.Error,json.JSONDecodeError,UnicodeError) as error:
            raise ValueError('Decision history is unavailable or malformed.') from error

    def lookup(self, owner, approval_id):
        """Exact durable identity lookup, including decisions outside recent history."""
        _identity(owner,64);_identity(approval_id,32)
        self._plain()
        if not self.path.exists():return None
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db)
                return next((dict(row) for row in self._rows(db) if row['owner']==owner and row['id']==approval_id),None)
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Decision history unavailable.') from error
