"""Append-only owner interpretations; never native evidence or execution authority."""
from contextlib import closing
import copy
import hashlib
import json
import math
import sqlite3

from .decision_history import DecisionHistory, MAX_DATABASE_BYTES, _identity

MAX_PROPOSALS = 4096
MAX_ITEMS = 16
MAX_PAYLOAD_BYTES = 64 * 1024
PAYLOAD_FIELDS = {'target_id','target_fingerprint','source','reference','snapshot_sha256',
                  'fact_sha256','selected_fact','reason'}
FACT_FIELDS = {'status','checks','rollback','evidence_sha256'}
REVIEW = {'summary':'Record an owner interpretation of conflicting historical evidence.',
          'effect':'Original observations and conflict warnings remain unchanged. No execution or retry is authorized.'}
SCHEMA = 'CREATE TABLE resolutions (sequence INTEGER PRIMARY KEY, owner TEXT NOT NULL, approval_id TEXT NOT NULL, phase TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(owner,approval_id,phase))'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def review_document(payload):
    if type(payload) is not dict:raise ValueError('Invalid interpretation review payload.')
    return dict(copy.deepcopy(REVIEW), **copy.deepcopy(payload))


def reason_text(value):
    if type(value) is not str or not value.strip() or len(value)>2000 or any(ord(c)<32 and c not in '\n\t\r' for c in value):
        raise ValueError('Interpretation reason must be nonempty text, at most 2000 characters, without control characters.')
    try:value.encode('utf-8')
    except UnicodeError as error:raise ValueError('Interpretation reason must be valid Unicode text.') from error
    return value.strip()


def policy_text(value):
    if type(value) is not str or not value or len(value)>256 or any(ord(c)<32 for c in value):
        raise ValueError('An exact bounded application policy is required.')
    return value


def validate_payload(payload):
    if type(payload) is not dict or set(payload)!=PAYLOAD_FIELDS:raise ValueError('Invalid evidence interpretation payload.')
    for name in PAYLOAD_FIELDS-{'source','selected_fact','reason'}:_identity(payload[name],32 if name=='target_id' else 64)
    source=payload['source'];fact=payload['selected_fact']
    if type(source) is not str or source not in {'project','worker','application'}:raise ValueError('Invalid evidence source.')
    if reason_text(payload['reason'])!=payload['reason']:raise ValueError('Interpretation reason must be trimmed.')
    if type(fact) is not dict or set(fact)!=FACT_FIELDS or digest(fact)!=payload['fact_sha256']:
        raise ValueError('Selected native fact does not match its digest.')
    if type(fact['status']) is not str:raise ValueError('Invalid native fact status.')
    _identity(fact['evidence_sha256'],64)
    if source=='project':
        from .decision_outcomes import validate
        validate(dict(event_id='0'*32,phase='result',observed_at=0,project_hash='0'*64,edit_id='0'*32,**fact))
    elif source=='worker':
        from .capability_outcomes import validate
        if fact['rollback'] is not None or fact['status'] not in {'checks_passed','checks_failed','tool_failed','cancelled','expired'}:
            raise ValueError('Invalid worker interpretation fact.')
        validate(dict(event_id='0'*32,phase='observed',observed_at=0,job_id='0'*32,request_digest='0'*64,
                      **{k:v for k,v in fact.items() if k!='rollback'}))
    elif fact['status'] not in {'active','failed','rolled_back','recovery_required'} or fact['checks'] is not None or fact['rollback'] is not None:
        raise ValueError('Invalid application interpretation fact.')
    return payload


def stamp(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=253402300799:
        raise ValueError('Invalid interpretation timestamp.')


def proposal(owner,row,created_at):
    """Capture a checked live document, excluding expiry and browser authority."""
    return dict(schema_version=1,owner=owner,approval_id=row['id'],fingerprint=row['fingerprint'],
        document={key:copy.deepcopy(row[key]) for key in ('kind','payload','review','session','policy_revision')},created_at=created_at)


class EvidenceResolutions(DecisionHistory):
    def __init__(self,history):
        if history is None:raise ValueError('Decision history is not configured.')
        self.history=history;self.root=history.root/'EvidenceResolutions';self.path=self.root/'resolutions.sqlite3';self._plain()

    @staticmethod
    def _schema(db,*,create=False):
        rows=db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if not rows and version==0 and create:
            db.execute(SCHEMA);db.execute('PRAGMA user_version=1');return
        if version!=1 or rows!=[('resolutions',SCHEMA)]:raise ValueError('Invalid evidence interpretation schema.')

    @staticmethod
    def _validate_proposal(value,receipts):
        if (type(value) is not dict or set(value)!={'schema_version','owner','approval_id','fingerprint','document','created_at'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Invalid interpretation proposal schema.')
        for key,size in [('owner',64),('approval_id',32),('fingerprint',64)]:_identity(value[key],size)
        stamp(value['created_at']);document=value['document']
        if type(document) is not dict or set(document)!={'kind','payload','review','session','policy_revision'}:
            raise ValueError('Invalid interpretation review document.')
        if document['kind']!='evidence_resolution' or document['review']!=review_document(document['payload']) or digest(document)!=value['fingerprint']:
            raise ValueError('Interpretation review fingerprint differs.')
        _identity(document['session'],32);policy_text(document['policy_revision']);payload=validate_payload(document['payload'])
        target=receipts.get((value['owner'],payload['target_id']))
        kinds={'project':{'project_edit','project_task'},'worker':{'remote_syntax','scheduled_syntax'},'application':{'self_improve'}}
        if (not target or target['fingerprint']!=payload['target_fingerprint'] or target['decision']!='approve'
                or target['kind'] not in kinds[payload['source']] or target['id']==value['approval_id']):
            raise ValueError('Interpretation requires an exact owned native decision.')
        saved=receipts.get((value['owner'],value['approval_id']))
        if saved and (saved['fingerprint']!=value['fingerprint'] or saved['session']!=document['session']
                or saved['kind']!='other' or saved['decision'] not in {'approve','reject'}):
            raise ValueError('Interpretation decision receipt differs.')

    @staticmethod
    def _validate_result(value,proposal,receipts):
        if (type(value) is not dict or set(value)!={'schema_version','owner','approval_id','fingerprint','decision','recorded_at'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Invalid interpretation completion schema.')
        stamp(value['recorded_at'])
        if not proposal or any(value[k]!=proposal[k] for k in ('owner','approval_id','fingerprint')):
            raise ValueError('Interpretation completion has no exact proposal.')
        saved=receipts.get((value['owner'],value['approval_id']))
        if type(value['decision']) is not str or value['decision'] not in {'approve','reject'} or not saved or saved['decision']!=value['decision']:
            raise ValueError('Interpretation completion lacks its consumed decision.')

    def _rows(self,db,receipts):
        rows=db.execute('SELECT owner,approval_id,phase,payload FROM resolutions ORDER BY sequence LIMIT ?',
                        (2*MAX_PROPOSALS+1,)).fetchall()
        if len(rows)>2*MAX_PROPOSALS:raise ValueError('Evidence interpretation capacity exceeded.')
        proposals={};results={}
        for owner,ident,phase,payload in rows:
            if type(payload) is not str or len(payload.encode())>MAX_PAYLOAD_BYTES:raise ValueError('Invalid interpretation record size.')
            value=json.loads(payload);key=(owner,ident)
            if phase=='proposal':
                self._validate_proposal(value,receipts)
                if key in proposals:raise ValueError('Duplicate interpretation proposal.')
                proposals[key]=value
            elif phase=='decision':
                self._validate_result(value,proposals.get(key),receipts)
                if key in results:raise ValueError('Duplicate interpretation completion.')
                results[key]=value
            else:raise ValueError('Unknown interpretation record phase.')
            if canonical(value)!=payload or owner!=value['owner'] or ident!=value['approval_id']:
                raise ValueError('Interpretation canonical bytes differ.')
        if len(proposals)>MAX_PROPOSALS:raise ValueError('Evidence interpretation capacity exceeded.')
        return [(value,results.get(key)) for key,value in proposals.items()]

    def read(self,*,decisions=None):
        self._plain()
        if not self.path.exists():return []
        receipts={(r['owner'],r['id']):r for r in (self.history._all_rows() if decisions is None else decisions)}
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db);return self._rows(db,receipts)
        except (sqlite3.Error,UnicodeError,json.JSONDecodeError) as error:raise ValueError('Evidence interpretation history unavailable.') from error

    def _append(self,phase,value):
        if type(value) is not dict:raise ValueError('Invalid interpretation record.')
        payload=canonical(value)
        if len(payload.encode())>MAX_PAYLOAD_BYTES:raise ValueError('Interpretation record exceeds its size limit.')
        receipts={(r['owner'],r['id']):r for r in self.history._all_rows()}
        if phase=='proposal':self._validate_proposal(value,receipts)
        else:
            prior=next((p for p,_ in self.read() if p['owner']==value.get('owner') and p['approval_id']==value.get('approval_id')),None)
            self._validate_result(value,prior,receipts)
        self._plain();self.root.mkdir(parents=True,exist_ok=True);self._plain()
        try:
            with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
                db.execute('PRAGMA synchronous=FULL');page=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//page))
                db.execute('BEGIN IMMEDIATE');self._schema(db,create=True)
                receipts={(r['owner'],r['id']):r for r in self.history._all_rows()}
                rows=self._rows(db,receipts);prior=next((pair for pair in rows if pair[0]['owner']==value['owner'] and pair[0]['approval_id']==value['approval_id']),None)
                if phase=='proposal':
                    self._validate_proposal(value,receipts)
                    if prior:
                        # Retrying a failed pre-consume audit write never changes
                        # the initial attempt timestamp or its frozen document.
                        if any(prior[0][k]!=v for k,v in value.items() if k!='created_at'):
                            raise ValueError('Conflicting interpretation proposal.')
                        return False
                    if len(rows)>=MAX_PROPOSALS:raise ValueError('Evidence interpretation capacity full; maintenance required.')
                    if (value['owner'],value['approval_id']) in receipts:raise ValueError('Interpretation link requires a fresh decision attempt.')
                else:
                    self._validate_result(value,prior[0] if prior else None,receipts)
                    if prior[1]:
                        if prior[1]!=value:raise ValueError('Conflicting interpretation completion.')
                        return False
                db.execute('INSERT INTO resolutions (owner,approval_id,phase,payload) VALUES (?,?,?,?)',
                           (value['owner'],value['approval_id'],phase,payload))
            return True
        except (sqlite3.Error,UnicodeError) as error:raise ValueError('Evidence interpretation could not be saved.') from error

    def link(self,value):return self._append('proposal',value)
    def finish(self,value):return self._append('decision',value)


def for_owner(owner,*,records,decisions,conflicts):
    """Historical interpretations only; matching inputs are not current validity."""
    _identity(owner,64);receipts={(r['owner'],r['id']):r for r in decisions};accepted={};result={}
    own=[(p,r) for p,r in records if p['owner']==owner]
    for p,r in own:
        q=p['document']['payload']
        if r and r['decision']=='approve':accepted.setdefault((q['source'],q['reference'],q['snapshot_sha256']),set()).add(q['fact_sha256'])
    for p,r in sorted(own,key=lambda pair:(-pair[0]['created_at'],pair[0]['approval_id'])):
        q=p['document']['payload'];target=(q['target_id'],q['target_fingerprint']);current=conflicts.get(target)
        matches=(current is not None and all(current[k]==q[k] for k in ('source','reference','snapshot_sha256'))
                 and any(v['fact_sha256']==q['fact_sha256'] for v in current['variants']))
        saved=receipts.get((owner,p['approval_id']))
        item={k:q[k] for k in ('target_id','target_fingerprint','source','reference','snapshot_sha256','fact_sha256','reason')}
        item.update(approval_id=p['approval_id'],fingerprint=p['fingerprint'],decision=saved['decision'] if saved else 'unrecorded',
            recording_state='recorded' if r else 'unrecorded',currency='matching_inputs' if matches else 'stale',
            interpretation_conflict=bool(r and r['decision']=='approve' and
                len(accepted.get((q['source'],q['reference'],q['snapshot_sha256']),()))>1))
        for key in (target,(p['approval_id'],p['fingerprint'])):result.setdefault(key,[]).append(item)
    return {key:dict(schema_version=1,read_only=True,freshness='historical',total=len(items),
                     truncated=len(items)>MAX_ITEMS,items=copy.deepcopy(items[:MAX_ITEMS])) for key,items in result.items()}


def empty_projection():
    return dict(schema_version=1,read_only=True,freshness='historical',total=0,truncated=False,items=[])
