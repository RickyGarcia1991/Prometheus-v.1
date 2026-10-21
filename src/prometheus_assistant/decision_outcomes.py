"""Immutable bounded outcome evidence. Never an execution grant."""
from contextlib import closing
import json
import math
import re
import sqlite3
from .decision_history import DecisionHistory, _identity

MAX_EVENTS=4096*16
MAX_PER_DECISION=16
MAX_DATABASE_BYTES=32*1024*1024
_SCHEMA='CREATE TABLE outcomes (sequence INTEGER PRIMARY KEY, owner TEXT NOT NULL, approval_id TEXT NOT NULL, fingerprint TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)'
_FIELDS={'event_id','phase','observed_at','project_hash','status','edit_id','checks','rollback','evidence_sha256'}
_CHECKS={'planned','executed','passed','failed','cancelled','timed_out'}


def validate(event):
    if type(event) is not dict or set(event)!=_FIELDS:raise ValueError('Invalid outcome schema.')
    _identity(event['event_id'],32);_identity(event['project_hash'],64)
    if type(event['observed_at']) not in (int,float) or not math.isfinite(event['observed_at']) or not 0<=event['observed_at']<=253402300799:raise ValueError('Invalid outcome timestamp.')
    if type(event['edit_id']) is not str or not re.fullmatch(r'(?:|[0-9a-f]{32}|group-[0-9a-f]{32})',event['edit_id']):raise ValueError('Invalid edit identity.')
    phase,status=event['phase'],event['status']
    if type(phase) is not str or phase not in {'started','result','undo_started','undo_result'}:raise ValueError('Invalid phase.')
    if type(status) is not str or status not in {'started','passed','failed','cancelled','unchanged','applied_without_checks','unknown','restored','restore_failed'}:raise ValueError('Invalid status.')
    if type(event['rollback']) is not str or event['rollback'] not in {'not_attempted','restored','needs_attention','unknown'}:raise ValueError('Invalid rollback.')
    checks=event['checks']; digest=event['evidence_sha256']
    if digest is not None:_identity(digest,64)
    if checks is not None:
        if type(checks) is not dict or set(checks)!=_CHECKS or any(type(v) is not int or not 0<=v<=64 for v in checks.values()):raise ValueError('Invalid check counts.')
        if checks['executed']>checks['planned'] or sum(checks[k] for k in ('passed','failed','cancelled','timed_out'))!=checks['executed']:raise ValueError('Incoherent check counts.')
    if phase in {'started','undo_started'}:
        if status!='started' or checks is not None or digest is not None or event['rollback']!='unknown' or (phase=='started' and event['edit_id']):raise ValueError('Invalid start evidence.')
    elif phase=='result':
        if status not in {'passed','failed','cancelled','unchanged','applied_without_checks','unknown'}:raise ValueError('Invalid result status.')
        if status=='unknown':
            if checks is not None or digest is not None or event['rollback']!='unknown' or event['edit_id']:raise ValueError('Unknown result cannot claim native evidence.')
        elif digest is None:raise ValueError('Native result requires evidence digest.')
        if status=='passed' and (checks is None or checks['planned']==0 or checks['executed']!=checks['planned'] or checks['passed']!=checks['planned'] or event['rollback']!='not_attempted'):
            raise ValueError('Passed outcome requires complete successful checks without rollback.')
    elif status not in {'restored','restore_failed'} or checks is not None or digest is None or event['rollback']!=('restored' if status=='restored' else 'needs_attention'):raise ValueError('Invalid undo evidence.')
    return json.dumps(event,sort_keys=True,separators=(',',':'),allow_nan=False)


def sequence(prior,event):
    phase=event['phase']
    if len(prior)>=MAX_PER_DECISION:raise ValueError('Outcome capacity reached; maintenance required.')
    if not prior:
        if phase!='started':raise ValueError('Outcome must begin with started.')
        return
    if event['project_hash']!=prior[0]['project_hash']:raise ValueError('Outcome project differs.')
    last=prior[-1]['phase']
    expected={'started':'result','result':'undo_started','undo_started':'undo_result','undo_result':'undo_started'}[last]
    if phase!=expected:raise ValueError('Conflicting outcome phase.')
    if phase.startswith('undo'):
        result=prior[1]
        if not result['edit_id'] or event['edit_id']!=result['edit_id']:raise ValueError('Undo requires exact recorded edit identity.')


class DecisionOutcomes(DecisionHistory):
    def __init__(self,history):
        self.history_store=history
        self.root=history.root/'OutcomeLinks'
        self.path=self.root/'outcomes.sqlite3'
        self._plain()

    @staticmethod
    def _schema(db,*,create=False):
        rows=db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if not rows and version==0 and create:
            db.execute(_SCHEMA);db.execute('PRAGMA user_version=1');return
        if version!=1 or rows!=[('outcomes',_SCHEMA)]:raise ValueError('Invalid outcome database schema.')

    def _approved(self,owner,approval_id,fingerprint):
        _identity(owner,64);_identity(approval_id,32);_identity(fingerprint,64)
        row=self.history_store.lookup(owner,approval_id)
        if not row or row['fingerprint']!=fingerprint or row['decision']!='approve' or row['kind'] not in {'project_edit','project_task'}:raise ValueError('Exact approved project decision required.')

    @staticmethod
    def _rows(db):
        rows=db.execute('SELECT owner,approval_id,fingerprint,event_id,payload FROM outcomes ORDER BY sequence LIMIT ?', (MAX_EVENTS+1,)).fetchall()
        if len(rows)>MAX_EVENTS:raise ValueError('Outcome store capacity exceeded.')
        groups={};result=[]
        for owner,ident,fingerprint,eid,payload in rows:
            _identity(owner,64);_identity(ident,32);_identity(fingerprint,64)
            if type(payload) is not str or len(payload)>4096:raise ValueError('Invalid bounded outcome.')
            event=json.loads(payload)
            if validate(event)!=payload or event['event_id']!=eid:raise ValueError('Outcome bytes differ.')
            key=(owner,ident);prior=groups.setdefault(key,[])
            if prior and prior[0][0]!=fingerprint:raise ValueError('Outcome fingerprint differs.')
            sequence([p[1] for p in prior],event);prior.append((fingerprint,event))
            result.append((owner,ident,fingerprint,event))
        return result

    def read(self):
        self._plain()
        if not self.path.exists():return []
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db);rows=self._rows(db)
            if rows:
                history=self.history_store
                history._plain()
                with closing(sqlite3.connect(history.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as decisions:
                    history._schema(decisions)
                    approved={(r['owner'],r['id'],r['fingerprint']) for r in history._rows(decisions) if r['decision']=='approve' and r['kind'] in {'project_edit','project_task'}}
                if any((o,i,f) not in approved for o,i,f,e in rows):raise ValueError('Outcome reference is not an approved decision.')
            return rows
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Outcome history unavailable.') from error

    def record(self,owner,approval_id,fingerprint,event):
        payload=validate(event);self._approved(owner,approval_id,fingerprint)
        self._plain();self.root.mkdir(parents=True,exist_ok=True);self._plain()
        try:
            with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
                db.execute('PRAGMA synchronous=FULL');page=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//page));db.execute('BEGIN IMMEDIATE')
                self._schema(db,create=True);rows=self._rows(db)
                for o,i,f,e in rows:
                    if e['event_id']==event['event_id']:
                        if (o,i,f,validate(e))!=(owner,approval_id,fingerprint,payload):raise ValueError('Conflicting outcome identity.')
                        return False
                if len(rows)>=MAX_EVENTS:raise ValueError('Outcome store full; maintenance required.')
                sequence([e for o,i,f,e in rows if (o,i)==(owner,approval_id)],event)
                db.execute('INSERT INTO outcomes(owner,approval_id,fingerprint,event_id,payload) VALUES (?,?,?,?,?)',(owner,approval_id,fingerprint,event['event_id'],payload))
            return True
        except sqlite3.Error as error:raise ValueError('Outcome could not be saved; storage maintenance required.') from error
