"""Bounded non-authorizing capability observations, separate from legacy ledgers."""
from contextlib import closing
import hashlib
import json
import math
import secrets
import sqlite3
import time
from .decision_history import DecisionHistory, _identity

KINDS=frozenset({'runtime_config','idle_config','integration','integration_access','remote_syntax','scheduled_syntax'})
ASYNC=frozenset({'remote_syntax','scheduled_syntax'})
TERMINAL=frozenset({'settings_saved','access_granted','adapter_returned','cancelled','expired','checks_passed','checks_failed','tool_failed'})
STATUSES=TERMINAL|{'started','waiting','deferred','unconfirmed','unknown'}
MAX_PER_DECISION=16
MAX_EVENTS=4096*MAX_PER_DECISION
MAX_DATABASE_BYTES=32*1024*1024
MAX_EVENT_BYTES=4096
_FIELDS={'event_id','phase','observed_at','status','job_id','request_digest','checks','evidence_sha256'}
_COUNTS={'planned','executed','passed','failed','cancelled','timed_out'}
_SCHEMA='CREATE TABLE outcomes (sequence INTEGER PRIMARY KEY, owner TEXT NOT NULL, approval_id TEXT NOT NULL, fingerprint TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)'


def _binding(job_id,request_digest):
    if job_id=='' and request_digest=='':return
    _identity(job_id,32);_identity(request_digest,64)


def validate(event):
    if type(event) is not dict or set(event)!=_FIELDS:raise ValueError('Invalid capability observation schema.')
    _identity(event['event_id'],32);_binding(event['job_id'],event['request_digest'])
    stamp=event['observed_at']
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=stamp<=253402300799:raise ValueError('Invalid observation timestamp.')
    if type(event['status']) is not str or event['status'] not in STATUSES:raise ValueError('Invalid capability observation status.')
    if type(event['phase']) is not str or event['phase'] not in {'started','observed'}:raise ValueError('Invalid capability observation phase.')
    if (event['phase']=='started')!=(event['status']=='started'):raise ValueError('Invalid start status.')
    digest=event['evidence_sha256'];checks=event['checks']
    if digest is not None:_identity(digest,64)
    if checks is not None:
        if type(checks) is not dict or set(checks)!=_COUNTS or any(type(v) is not int or not 0<=v<=64 for v in checks.values()):raise ValueError('Invalid capability check counts.')
        if checks['executed']>checks['planned'] or sum(checks[k] for k in ('passed','failed','cancelled','timed_out'))!=checks['executed']:raise ValueError('Incoherent capability counts.')
    status=event['status']
    if status in {'started','unknown'}:
        if checks is not None or digest is not None:raise ValueError('Unknown/start cannot claim observed evidence.')
    elif status in {'checks_passed','checks_failed'}:
        if checks is None or digest is None or checks['planned']==0:raise ValueError('Check outcome requires bounded evidence.')
        successful=checks['executed']==checks['passed']==checks['planned']
        if (status=='checks_passed')!=successful or (status=='checks_failed' and checks['failed']==0):raise ValueError('Check outcome contradicts observed counts.')
    elif checks is not None:raise ValueError('This status cannot claim check execution.')
    if status in TERMINAL and digest is None:raise ValueError('Terminal observation requires native evidence digest.')
    payload=json.dumps(event,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(payload.encode())>MAX_EVENT_BYTES:raise ValueError('Observation exceeds bound.')
    return payload


def _kind(kind,event):
    async_kind=kind in ASYNC
    if kind not in KINDS or bool(event['job_id'])!=async_kind:raise ValueError('Capability binding does not match approved kind.')
    allowed={'started','unknown'}
    if async_kind:allowed|={'waiting','deferred','unconfirmed','cancelled','expired','checks_passed','checks_failed','tool_failed'}
    elif kind in {'runtime_config','idle_config'}:allowed.add('settings_saved')
    elif kind=='integration_access':allowed.add('access_granted')
    elif kind=='integration':allowed.add('adapter_returned')
    if event['status'] not in allowed:raise ValueError('Observation does not match approved capability.')


def _same(a,b):
    return all(a[k]==b[k] for k in _FIELDS-{'event_id','observed_at'})


def _sequence(prior,event):
    if not prior:
        if event['phase']!='started':raise ValueError('Observation requires recorded start.')
        return
    if (event['job_id'],event['request_digest'])!=(prior[0]['job_id'],prior[0]['request_digest']):raise ValueError('Observation binding differs.')
    if event['phase']=='started' or prior[-1]['status'] in TERMINAL:raise ValueError('Capability result is immutable.')
    if len(prior)>=MAX_PER_DECISION:raise ValueError('Capability observation capacity reached; maintenance required.')


class CapabilityOutcomes(DecisionHistory):
    def __init__(self,history):
        if history is None:raise ValueError('Decision history is not configured.')
        self.history_store=history;self.root=history.root/'CapabilityLinks';self.path=self.root/'outcomes.sqlite3';self._plain()

    @staticmethod
    def _schema(db,*,create=False):
        rows=db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if not rows and version==0 and create:
            db.execute(_SCHEMA);db.execute('PRAGMA user_version=1');return
        if version!=1 or rows!=[('outcomes',_SCHEMA)]:raise ValueError('Invalid capability history schema.')

    def _decisions(self):
        history=self.history_store;history._plain()
        if not history.path.exists():return {}
        with closing(sqlite3.connect(history.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            history._schema(db)
            return {(r['owner'],r['id'],r['fingerprint']):r['kind'] for r in history._rows(db) if r['decision']=='approve' and r['kind'] in KINDS}

    @staticmethod
    def _rows(db,approved):
        rows=db.execute('SELECT owner,approval_id,fingerprint,event_id,payload FROM outcomes ORDER BY sequence LIMIT ?', (MAX_EVENTS+1,)).fetchall()
        if len(rows)>MAX_EVENTS:raise ValueError('Capability history capacity exceeded.')
        groups={};events=[]
        for owner,ident,fingerprint,eid,payload in rows:
            _identity(owner,64);_identity(ident,32);_identity(fingerprint,64)
            key=(owner,ident,fingerprint)
            if key not in approved:raise ValueError('Capability observation has no exact approved decision.')
            if type(payload) is not str or len(payload)>MAX_EVENT_BYTES:raise ValueError('Invalid bounded capability observation.')
            event=json.loads(payload)
            if validate(event)!=payload or event['event_id']!=eid:raise ValueError('Capability observation bytes differ.')
            _kind(approved[key],event);prior=groups.setdefault(key,[])
            if any(_same(p,event) for p in prior):raise ValueError('Duplicate stored observation.')
            _sequence(prior,event);prior.append(event);events.append((owner,ident,fingerprint,event))
        return events

    def read(self):
        self._plain()
        if not self.path.exists():return []
        try:
            approved=self._decisions()
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db);return self._rows(db,approved)
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Capability history unavailable.') from error

    def record(self,owner,approval_id,fingerprint,event):
        _identity(owner,64);_identity(approval_id,32);_identity(fingerprint,64)
        payload=validate(event);self._plain()
        if not self.path.exists() and event['phase']!='started':raise ValueError('Observation requires recorded start.')
        try:
            approved=self._decisions();key=(owner,approval_id,fingerprint)
            if key not in approved:raise ValueError('Exact approved capability decision required.')
            _kind(approved[key],event)
            self.root.mkdir(parents=True,exist_ok=True);self._plain()
            with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
                db.execute('PRAGMA synchronous=FULL');page=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//page));db.execute('BEGIN IMMEDIATE')
                self._schema(db,create=True);rows=self._rows(db,approved)
                prior=[]
                for o,i,f,e in rows:
                    if e['event_id']==event['event_id'] and ((o,i,f)!=key or not _same(e,event)):raise ValueError('Conflicting capability observation identity.')
                    if (o,i,f)==key:prior.append(e)
                if any(_same(e,event) for e in prior):return False
                _sequence(prior,event)
                if len(rows)>=MAX_EVENTS:raise ValueError('Capability history full; maintenance required.')
                db.execute('INSERT INTO outcomes(owner,approval_id,fingerprint,event_id,payload) VALUES (?,?,?,?,?)',(*key,event['event_id'],payload))
            return True
        except sqlite3.Error as error:raise ValueError('Capability observation could not be saved; storage maintenance required.') from error

    def links_for_job(self,*,owner,job_id,request_digest):
        _identity(owner,64);_identity(job_id,32);_identity(request_digest,64)
        return [dict(approval_id=i,fingerprint=f) for o,i,f,e in self.read() if o==owner and e['phase']=='started' and e['job_id']==job_id and e['request_digest']==request_digest]


def _digest(evidence):
    if evidence is None:return None
    # Only fixed selected facts. No provider text, paths, logs or credential data.
    allowed={'saved','granted','returned','waiting','deferred','cancelled','expired','confirmed','exit_code','executed','passed','failed','timed_out','planned','revision','enabled','interval_minutes','source_unchanged','tool_failed','lint_findings','cpu'}
    if type(evidence) is not dict or not evidence or not set(evidence)<=allowed or any(type(v) not in (bool,int,type(None)) for v in evidence.values()):raise ValueError('Evidence must contain only selected bounded native facts.')
    if any(type(v) is int and not -2147483648<=v<=2147483647 for v in evidence.values()):raise ValueError('Evidence number exceeds bounds.')
    for key in ('enabled','source_unchanged','tool_failed','lint_findings','cpu'):
        if key in evidence and type(evidence[key]) is not bool:raise ValueError('Evidence flag must be boolean.')
    for key in ('revision','interval_minutes'):
        if key in evidence and (type(evidence[key]) is not int or not 0<=evidence[key]<=2147483647):raise ValueError('Evidence count must be nonnegative.')
    return hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class CapabilityOutcome:
    def __init__(self,approvals,owner,row,job_id='',request_digest=''):
        self.store=CapabilityOutcomes(approvals.decision_history) if approvals.decision_history is not None else None
        self.owner=owner;self.approval_id=row['id'];self.fingerprint=row['fingerprint']
        self.job_id=job_id;self.request_digest=request_digest

    def _event(self,status,evidence=None,checks=None):
        if status=='tool_failed' and (type(evidence) is not dict or evidence.get('confirmed') is not True or evidence.get('tool_failed') is not True):raise ValueError('Tool failure requires confirmed native evidence.')
        return dict(event_id=secrets.token_hex(16),phase='started' if status=='started' else 'observed',observed_at=time.time(),status=status,job_id=self.job_id,request_digest=self.request_digest,checks=checks,evidence_sha256=_digest(evidence))

    def start(self):
        if self.store is None:return None
        return self.store.record(self.owner,self.approval_id,self.fingerprint,self._event('started'))

    def observe(self,status,evidence=None,checks=None):
        if self.store is None:return None
        try:
            event=self._event(status,evidence,checks)
            added=self.store.record(self.owner,self.approval_id,self.fingerprint,event)
            return dict(recorded=True,added=added,approval_id=self.approval_id)
        except Exception:
            return dict(recorded=False,added=False,approval_id=self.approval_id,error='Capability observation unavailable; inspect the native result.')

    def unknown(self):
        return self.observe('unknown')
