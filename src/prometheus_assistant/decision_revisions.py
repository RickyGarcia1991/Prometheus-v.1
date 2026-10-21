"""Bounded revision audit links. These never restore a proposal or grant."""
from contextlib import closing
from collections import Counter
import json
import math
import sqlite3
from .decision_history import DecisionHistory, _identity, MAX_DATABASE_BYTES

MAX_LINKS = 4096
MAX_CHAIN = 16
FIELDS = {'owner','session','project_hash','parent_id','parent_fingerprint',
          'child_id','child_fingerprint','created_at'}
SCHEMA = 'CREATE TABLE revisions (child TEXT PRIMARY KEY, payload TEXT NOT NULL)'


class DecisionRevisions(DecisionHistory):
    def __init__(self, history):
        self.history = history
        self.root = history.root/'RevisionLinks'
        self.path = self.root/'revisions.sqlite3'
        self._plain()

    def _receipt_index(self):
        """One bounded canonical read, never a caller-supplied authority cache."""
        self.history._plain()
        if not self.history.path.exists():return {}
        try:
            with closing(sqlite3.connect(self.history.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self.history._schema(db)
                return {(r['owner'],r['id']):r for r in self.history._rows(db)}
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Revision parent audit unavailable.') from error

    def validate(self, row, receipts=None):
        if receipts is None:receipts=self._receipt_index()
        if type(row) is not dict or set(row) != FIELDS:
            raise ValueError('Invalid revision relationship schema.')
        for key in FIELDS-{'created_at'}:
            _identity(row[key],32 if key in {'session','parent_id','child_id'} else 64)
        if (type(row['created_at']) not in (int,float) or not math.isfinite(row['created_at'])
                or not 0 <= row['created_at'] <= 253402300799 or row['child_id']==row['parent_id']):
            raise ValueError('Invalid revision identity or timestamp.')
        parent=receipts.get((row['owner'],row['parent_id']))
        if (not parent or parent['kind']!='project_edit' or parent['decision']!='request_changes'
                or parent['fingerprint']!=row['parent_fingerprint'] or parent['session']!=row['session']):
            raise ValueError('Revision requires the exact saved change request.')
        child=receipts.get((row['owner'],row['child_id']))
        if child and (child['kind']!='project_edit' or child['session']!=row['session']
                      or child['fingerprint']!=row['child_fingerprint']):
            raise ValueError('Revision child receipt does not match.')
        return json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False)

    @staticmethod
    def _schema(db, *, create=False):
        rows=db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if not rows and version==0 and create:
            db.execute(SCHEMA);db.execute('PRAGMA user_version=1');return
        if version!=1 or rows!=[('revisions',SCHEMA)]:raise ValueError('Invalid revision database schema.')

    def _rows(self, db, receipts):
        records=db.execute('SELECT child,payload FROM revisions ORDER BY child LIMIT ?', (MAX_LINKS+1,)).fetchall()
        if len(records)>MAX_LINKS:raise ValueError('Revision capacity exceeded.')
        result=[]
        for child,payload in records:
            if type(payload) is not str or len(payload)>2048:raise ValueError('Invalid revision size.')
            row=json.loads(payload)
            if self.validate(row,receipts)!=payload or child!=row['child_id']:raise ValueError('Invalid revision bytes.')
            result.append(row)
        by_child={r['child_id']:r for r in result}
        children=Counter(r['parent_id'] for r in result)
        for row in result:
            if children[row['parent_id']]>MAX_CHAIN-1:
                raise ValueError('Revision relationship count exceeds bounds.')
            seen=set();current=row
            while current:
                if current['child_id'] in seen or len(seen)>=MAX_CHAIN:raise ValueError('Revision chain exceeds bounds.')
                seen.add(current['child_id'])
                previous=by_child.get(current['parent_id'])
                if previous and any(previous[k]!=current[k] for k in ('owner','session','project_hash')):
                    raise ValueError('Revision chain identity differs.')
                current=previous
        return result

    def record(self, row):
        payload=self.validate(row);self._plain();self.root.mkdir(parents=True,exist_ok=True);self._plain()
        try:
            with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
                db.execute('PRAGMA synchronous=FULL')
                page=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//page))
                db.execute('BEGIN IMMEDIATE');self._schema(db,create=True)
                receipts=self._receipt_index()
                self.validate(row,receipts)
                rows=self._rows(db,receipts)
                existing=next((r for r in rows if r['child_id']==row['child_id']),None)
                if existing:
                    if existing!=row:raise ValueError('Conflicting revision link.')
                    return False
                if len(rows)>=MAX_LINKS:raise ValueError('Revision capacity full; archive or maintain it.')
                if (row['owner'],row['child_id']) in receipts:raise ValueError('Revision child must be a new review.')
                db.execute('INSERT INTO revisions VALUES (?,?)',(row['child_id'],payload))
                self._rows(db,receipts)
            return True
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Revision audit unavailable.') from error

    def read(self):
        return self.read_with_states()[0]

    def read_with_states(self):
        """Return links and recorded child identities from the same validated snapshot."""
        self._plain()
        if not self.path.exists():return [],set()
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db)
                receipts=self._receipt_index()
                return self._rows(db,receipts),set(receipts)
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Revision audit unavailable.') from error
