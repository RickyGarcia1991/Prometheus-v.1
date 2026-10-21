"""Fresh bounded checks supplement interpretations; they never resolve conflicts.

A separate, append-only association binds a live review to the dedicated queue.
Canonical decisions alone cannot reconstruct a missing job. Existing queue
claims and receipts remain the sole execution/recovery protocol.
"""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .application_policy import require_dispatch, revision
from .decision_history import DecisionHistory, MAX_DATABASE_BYTES, _identity
from .evidence_resolution import canonical, digest, proposal, reason_text, stamp, validate_payload
from .remote_job_protocol import normalize_request, validate_receipt
from .scheduled_syntax import ScheduledSyntax, WAIT_SECONDS, public_job
from .syntax_waiting_store import WaitingStore, _record

KIND = 'evidence_followup'
MAX_LINKS = 100
MAX_LINK_BYTES = 256 * 1024
MAX_ITEMS = 16
CONTEXT_KEYS = {'interpretation_id','interpretation_fingerprint','state_sha256',
                'snapshot_sha256','fact_sha256','reason'}
PROVENANCE_KEYS = CONTEXT_KEYS | {'schema_version','project','interpretation',
    'interpretation_review_id','interpretation_review_fingerprint','source_kind',
    'source_ref','conflict_targets','evidence_snapshot'}
SCHEMA = 'CREATE TABLE followups (owner TEXT NOT NULL, approval_id TEXT NOT NULL, job_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,approval_id), UNIQUE(owner,job_id))'


def context(value):
    if type(value) is not dict or set(value)!=CONTEXT_KEYS:
        raise ValueError('Exact follow-up interpretation context is required.')
    for key in CONTEXT_KEYS-{'reason'}:_identity(value[key],32 if key=='interpretation_id' else 64)
    if reason_text(value['reason'])!=value['reason']:
        raise ValueError('Follow-up reason must be trimmed.')
    return copy.deepcopy(value)


def _provenance(value):
    if (type(value) is not dict or set(value)!=PROVENANCE_KEYS
            or type(value['schema_version']) is not int or value['schema_version']!=1):
        raise ValueError('Invalid follow-up provenance.')
    context({key:value[key] for key in CONTEXT_KEYS})
    for key,size in (('interpretation_review_id',32),('interpretation_review_fingerprint',64)):
        _identity(value[key],size)
    project=value['project']
    if (type(project) is not str or not project or len(project)>4096 or '\x00' in project
            or not Path(project).is_absolute()):
        raise ValueError('A bounded absolute project identity is required.')
    effective=validate_payload(value['interpretation'])
    if (value['source_kind']!=effective['source'] or value['source_ref']!=effective['reference']
            or value['snapshot_sha256']!=effective['snapshot_sha256']
            or value['fact_sha256']!=effective['fact_sha256']
            or value['conflict_targets']!=[dict(id=effective['target_id'],fingerprint=effective['target_fingerprint'])]
            or type(value['evidence_snapshot']) is not dict
            or any(value['evidence_snapshot'].get(k)!=effective[k] for k in ('source','reference','snapshot_sha256'))):
        raise ValueError('Follow-up provenance disagrees with its selected interpretation.')
    return value


def _payload(value):
    if type(value) is not dict or set(value)!={'request','target','placement','followup'}:
        raise ValueError('Invalid follow-up review payload.')
    if normalize_request(value['request'])!=value['request']:
        raise ValueError('Follow-up request is not canonical.')
    _provenance(value['followup'])
    # The unchanged queue validator also checks exact destination and bounds.
    _record(dict(**{k:value[k] for k in ('request','target','placement')},
        policy_revision='0'*64,approval_id='0'*32,fingerprint='0'*64,approved_at=0,expires_at=WAIT_SECONDS))
    return value


def _queue_record(link):
    doc=link['document']
    return dict(**{k:doc['payload'][k] for k in ('request','target','placement')},
        policy_revision=doc['policy_revision'],approval_id=link['approval_id'],fingerprint=link['fingerprint'],
        approved_at=link['created_at'],expires_at=link['created_at']+WAIT_SECONDS)


def public_provenance(value, request_digest):
    return dict(**{key:copy.deepcopy(value[key]) for key in CONTEXT_KEYS},
        schema_version=1,source_kind=value['source_kind'],source_ref=value['source_ref'],
        interpretation_review_id=value['interpretation_review_id'],
        interpretation_review_fingerprint=value['interpretation_review_fingerprint'],
        conflict_targets=copy.deepcopy(value['conflict_targets']),request_digest=request_digest,
        project_sha256=hashlib.sha256(value['project'].encode()).hexdigest())


class EvidenceFollowups(DecisionHistory):
    def __init__(self, approvals, owner):
        _identity(owner,64)
        if approvals.decision_history is None:raise ValueError('Decision history is not configured.')
        self.approvals,self.history,self.owner=approvals,approvals.decision_history,owner
        self.root=self.history.root/'EvidenceFollowups';self.path=self.root/'followups.sqlite3'
        self._plain()
        self.queue=WaitingStore(self.root/'queues'/owner/'waiting.sqlite3',owner)

    @staticmethod
    def _schema(db, *, create=False):
        tables=db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if not tables and version==0 and create:
            db.execute(SCHEMA);db.execute('PRAGMA user_version=1');return
        if tables!=[('followups',SCHEMA)] or version!=1:
            raise ValueError('Invalid follow-up history schema.')

    @staticmethod
    def _validate(value, receipts):
        if (type(value) is not dict or set(value)!={'schema_version','owner','approval_id','fingerprint','document','created_at'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Invalid follow-up association.')
        for key,size in (('owner',64),('approval_id',32),('fingerprint',64)):_identity(value[key],size)
        stamp(value['created_at'])
        doc=value['document']
        if (type(doc) is not dict or set(doc)!={'kind','payload','review','session','policy_revision'}
                or doc['kind']!=KIND or digest(doc)!=value['fingerprint']):
            raise ValueError('Follow-up review fingerprint differs.')
        _identity(doc['session'],32);_identity(doc['policy_revision'],64)
        payload=_payload(doc['payload']);review=doc['review']
        if (type(review) is not dict or review.get('followup')!=payload['followup']
                or review.get('request_digest')!=payload['request']['request_digest']
                or review.get('operation')!=payload['request']['operation']
                or review.get('destination')!=payload['target'] or review.get('placement')!=payload['placement']):
            raise ValueError('Follow-up review does not describe its exact request.')
        receipt=receipts.get((value['owner'],value['approval_id']))
        if receipt and (receipt['fingerprint']!=value['fingerprint'] or receipt['session']!=doc['session']
                or receipt['kind']!='other' or receipt['decision'] not in {'approve','reject'}):
            raise ValueError('Follow-up canonical decision differs.')
        _record(_queue_record(value))
        raw=canonical(value)
        if len(raw.encode())>MAX_LINK_BYTES:raise ValueError('Follow-up association exceeds its bound.')
        return raw

    def _rows(self, db, receipts):
        rows=db.execute('SELECT owner,approval_id,job_id,payload FROM followups ORDER BY owner,approval_id LIMIT ?',
                        (MAX_LINKS+1,)).fetchall()
        if len(rows)>MAX_LINKS:raise ValueError('Follow-up history capacity exceeded.')
        result=[]
        for owner,ident,job,raw in rows:
            if type(raw) is not str or len(raw.encode())>MAX_LINK_BYTES:raise ValueError('Invalid bounded follow-up association.')
            value=json.loads(raw)
            if (self._validate(value,receipts)!=raw or (owner,ident,job)!=(value['owner'],value['approval_id'],
                    value['document']['payload']['request']['job_id'])):
                raise ValueError('Follow-up association identity differs.')
            result.append(value)
        return result

    def read(self, *, decisions=None):
        self._plain()
        if not self.path.exists():return []
        decisions=self.history._all_rows() if decisions is None else decisions
        receipts={(r['owner'],r['id']):r for r in decisions}
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                self._schema(db)
                return self._rows(db,receipts)
        except (sqlite3.Error,json.JSONDecodeError,UnicodeError) as error:
            raise ValueError('Follow-up history is unavailable.') from error

    def link(self, value):
        receipts={(r['owner'],r['id']):r for r in self.history._all_rows()}
        raw=self._validate(value,receipts)
        if value['owner']!=self.owner:raise ValueError('Follow-up belongs to another owner.')
        self._plain();self.root.mkdir(parents=True,exist_ok=True);self._plain()
        try:
            with closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//db.execute('PRAGMA page_size').fetchone()[0]))
                db.execute('BEGIN IMMEDIATE');self._schema(db,create=True)
                receipts={(r['owner'],r['id']):r for r in self.history._all_rows()}
                rows=self._rows(db,receipts);self._validate(value,receipts)
                prior=next((r for r in rows if (r['owner'],r['approval_id'])==(self.owner,value['approval_id'])),None)
                if prior:
                    if canonical(prior)!=raw:raise ValueError('Follow-up association changed.')
                    return False
                if (self.owner,value['approval_id']) in receipts:
                    raise ValueError('A new follow-up association cannot be attached after a decision.')
                job=value['document']['payload']['request']['job_id']
                if any(r['owner']==self.owner and r['document']['payload']['request']['job_id']==job for r in rows):
                    raise ValueError('This follow-up job identity is already linked.')
                if len(rows)>=MAX_LINKS:raise ValueError('Follow-up history is full.')
                db.execute('INSERT INTO followups VALUES (?,?,?,?)',(self.owner,value['approval_id'],job,raw))
            return True
        except sqlite3.Error as error:raise ValueError('Follow-up association could not be saved.') from error

    def checked_link(self, row, *, links=None, decisions=None):
        decisions=self.history._all_rows() if decisions is None else decisions
        links=self.read(decisions=decisions) if links is None else links
        link=next((r for r in links if (r['owner'],r['approval_id'])==(self.owner,row['approval_id'])),None)
        receipt=next((r for r in decisions if (r['owner'],r['id'])==(self.owner,row['approval_id'])),None)
        if (link is None or receipt is None or receipt['decision']!='approve'
                or receipt['fingerprint']!=row['fingerprint']
                or any(row.get(k)!=v for k,v in _queue_record(link).items())):
            raise ValueError('Follow-up queue lacks its exact approved provenance; no execution is permitted.')
        return link

    def public_jobs(self, *, decisions=None):
        decisions=self.history._all_rows() if decisions is None else decisions
        links=[r for r in self.read(decisions=decisions) if r['owner']==self.owner]
        queue=self.queue.list();receipts={(r['owner'],r['id']):r for r in decisions}
        for row in queue:self.checked_link(row,links=links,decisions=decisions)
        for row in queue:
            outcome=row['result']
            if outcome is not None:
                if type(outcome) is not dict or set(outcome)!={'state','receipt','evidence'}:
                    raise ValueError('Follow-up result has unexpected fields.')
                expected=validate_receipt(row['request'],outcome['receipt']) if outcome['receipt'] is not None else None
                if outcome['evidence']!=expected:
                    raise ValueError('Follow-up result evidence differs from its exact receipt.')
        result=[]
        for link in links:
            req=link['document']['payload']['request'];payload=link['document']['payload']
            queued=next((r for r in queue if r['request']['job_id']==req['job_id']),None)
            decision=receipts.get((self.owner,link['approval_id']),{}).get('decision','unrecorded')
            public=public_job(queued) if queued else dict(job_id=req['job_id'],
                state='rejected' if decision=='reject' else 'unconfirmed',destination=payload['target']['device_name'],
                device_id=payload['target']['device_id'],approved_at=link['created_at'] if decision=='approve' else None,
                expires_at=link['created_at']+WAIT_SECONDS,next_attempt_at=None,
                reasons=['Follow-up execution was not durably queued. A fresh review is required.'] if decision!='reject' else [],
                result=None,files=[f['path'] for f in req['files']],placement=payload['placement'],operation=req['operation'])
            public.update(approval_id=link['approval_id'],fingerprint=link['fingerprint'],decision=decision,
                provenance=public_provenance(payload['followup'],req['request_digest']),
                worker_digest=req['worker_digest'],tool=copy.deepcopy(req.get('tool')),
                profile=req.get('profile'),queued=queued is not None)
            result.append(public)
        return sorted(result,key=lambda r:(-(r['approved_at'] or 0),r['job_id']))

    def public_job(self, job_id):
        _identity(job_id,32)
        result=next((r for r in self.public_jobs() if r['job_id']==job_id),None)
        if result is None:raise ValueError('No owned follow-up has that identity.')
        return result

    def cancel(self, job_id, *, now):
        row=self.queue.get(job_id)
        if row is None:raise ValueError('No approved follow-up waiting job has that identity.')
        self.checked_link(row);self.queue.cancel(job_id,now=now)
        return self.public_job(job_id)

    def history_projection(self, *, decisions=None):
        result={}
        for row in self.public_jobs(decisions=decisions):
            p=row['provenance'];targets={(row['approval_id'],row['fingerprint']),
                (p['interpretation_id'],p['interpretation_fingerprint']),
                (p['interpretation_review_id'],p['interpretation_review_fingerprint'])}
            targets.update((r['id'],r['fingerprint']) for r in p['conflict_targets'])
            for target in targets:result.setdefault(target,[]).append(copy.deepcopy(row))
        return {key:dict(schema_version=1,read_only=True,freshness='historical',total=len(items),
                        truncated=len(items)>MAX_ITEMS,items=items[:MAX_ITEMS]) for key,items in result.items()}


class FollowupScheduler(ScheduledSyntax):
    prepare_mode='ui.evidence_followup_prepare'
    tick_mode='ui.evidence_followup_tick'
    recover_mode='ui.evidence_followup_recover'

    def __init__(self, approvals, store, *, workspace_factory, **kwargs):
        super().__init__(approvals,store.queue,**kwargs)
        self.store,self.workspace_factory=store,workspace_factory
        self._preparing=threading.local()

    def _snapshot(self, project, files):
        workspace=self.workspace_factory(project)
        root=str(workspace.root)
        if root!=project:raise ValueError('The exact resolved project identity changed.')
        with workspace.lock:
            observed=[]
            for row in files:
                found=workspace.read_file(row['path'])
                observed.append({k:found[k] for k in ('path','content')})
        if observed!=[{k:row[k] for k in ('path','content')} for row in files]:
            raise ValueError('Saved source files changed; prepare a fresh follow-up review.')
        return root

    def _current(self, frozen, *, state=None):
        if state is None:
            item,conflicts=self.approvals._interpretation_change_inputs(self.queue.owner,
                frozen['interpretation_id'],frozen['interpretation_fingerprint'])
            if item['state']!='active' or item['state_sha256']!=frozen['state_sha256']:
                raise ValueError('Interpretation state changed; prepare a fresh follow-up review.')
            effective=item['effective_payload'];review_id=item['effective_review_id'];review_fp=item['effective_review_fingerprint']
        else:
            _,conflicts=self.approvals._evidence_conflict_inputs(self.queue.owner)
            effective=state['payload'];review_id=state['review_id'];review_fp=state['review_fingerprint']
        if (effective!=frozen['interpretation'] or review_id!=frozen['interpretation_review_id']
                or review_fp!=frozen['interpretation_review_fingerprint']):
            raise ValueError('Interpretation changed; prepare a fresh follow-up review.')
        self.approvals._checked_resolution_target(conflicts,effective)
        if conflicts.get((effective['target_id'],effective['target_fingerprint']))!=frozen['evidence_snapshot']:
            raise ValueError('Evidence changed; prepare a fresh follow-up review.')

    def _check_tool(self, payload):
        request=payload['request']
        if request['schema_version']==2:
            adapter=self._adapter(payload['target'])
            if adapter is not None and getattr(adapter,'lint_tool',lambda:None)()!=request['tool']:
                raise ValueError('The reviewed lint tool identity changed; prepare a fresh review.')

    def prepare(self, project, files, job_id, *, session, context, strategy='available-headroom-v1',operation='python-syntax-check'):
        ctx=globals()['context'](context)
        with self.grant_owner() as owner:
            if owner!=self.queue.owner:raise ValueError('Follow-up belongs to another owner.')
            require_dispatch(self.prepare_mode)
            item,conflicts=self.approvals._interpretation_change_inputs(owner,ctx['interpretation_id'],ctx['interpretation_fingerprint'])
            effective=item['effective_payload']
            if (item['state']!='active' or item['state_sha256']!=ctx['state_sha256']
                    or any(effective[k]!=ctx[k] for k in ('snapshot_sha256','fact_sha256'))):
                raise ValueError('Exact active interpretation state is required.')
            self.approvals._checked_resolution_target(conflicts,effective)
            self._snapshot(project,files)
            frozen=dict(schema_version=1,**ctx,project=project,interpretation=copy.deepcopy(effective),
                interpretation_review_id=item['effective_review_id'],interpretation_review_fingerprint=item['effective_review_fingerprint'],
                source_kind=effective['source'],source_ref=effective['reference'],
                conflict_targets=[dict(id=effective['target_id'],fingerprint=effective['target_fingerprint'])],
                evidence_snapshot=copy.deepcopy(conflicts[effective['target_id'],effective['target_fingerprint']]))
            _provenance(frozen)
            self.store.public_jobs() # malformed provenance cannot become a new empty start
        self._preparing.value=frozen
        try:return super().prepare(files,job_id,session=session,strategy=strategy,operation=operation)
        finally:self._preparing.value=None

    def _review(self, **kwargs):
        frozen=copy.deepcopy(getattr(self._preparing,'value',None))
        if frozen is None:raise ValueError('A follow-up context is required.')
        self._current(frozen);self._snapshot(frozen['project'],kwargs['payload']['request']['files'])
        self._check_tool(kwargs['payload'])
        kwargs['kind']=KIND;kwargs['payload']['followup']=frozen
        kwargs['review'].update(followup=copy.deepcopy(frozen),
            operation=kwargs['payload']['request']['operation'],request_digest=kwargs['payload']['request']['request_digest'],
            action='Approve this exact supplemental evidence check and bounded waiting',
            benefit='Collect a fresh check while preserving the original conflicting facts and interpretations.')
        kwargs['review']['effects']+=' The result is supplemental evidence, not a conflict winner or proof of runtime correctness.'
        _payload(kwargs['payload'])
        document={key:kwargs[key] for key in ('kind','payload','review','session','policy_revision')}
        self.store._validate(dict(schema_version=1,owner=kwargs['owner'],approval_id='0'*32,
            fingerprint=digest(document),document=document,created_at=self.clock()),{})
        return self.approvals.review(**kwargs)

    def decide(self, *, session, approval_id, fingerprint, decision):
        if decision not in {'approve','reject'}:raise ValueError('Choose approve or reject for a follow-up.')
        with self.grant_owner() as owner, self.approvals._lock:
            if owner!=self.queue.owner:raise ValueError('Follow-up belongs to another owner.')
            require_dispatch('ui.approval_decision')
            row=self.approvals.pending(owner=owner,binding=self.binding)
            if (not row or row['kind']!=KIND or row['session']!=session or row['id']!=approval_id
                    or row['fingerprint']!=fingerprint):raise ValueError('Exact follow-up review is unavailable.')
            payload=_payload(row['payload'])
            if decision=='approve':
                if self.cancelled() or row['policy_revision']!=revision():raise ValueError('Follow-up authority changed.')
                self._current(payload['followup']);self._snapshot(payload['followup']['project'],payload['request']['files'])
                self._check_tool(payload)
                adapter=self._adapter(payload['target'])
                if adapter is not None:getattr(adapter,'authorize',lambda req:None)(payload['request'])
            link=next((value for value in self.store.read() if value['owner']==owner
                and value['approval_id']==approval_id),None) or proposal(owner,row,self.clock())
            self.store.link(link)
            self.approvals.consume(owner=owner,binding=self.binding,session=session,approval_id=approval_id,
                fingerprint=fingerprint,decision=decision,policy_revision=revision())
            if decision=='approve':
                # No restart path recreates this enqueue if it is interrupted.
                self.queue.enqueue(_queue_record(link),now=self.clock())
            return dict(self.store.public_job(payload['request']['job_id']),approval_resolved=row['id'])

    def _claim(self, row, *, now):
        link=self.store.checked_link(row);frozen=link['document']['payload']['followup']
        try:
            self._check_tool(link['document']['payload'])
            with self.approvals.interpretation_lifecycle().claim_guard(self.queue.owner,
                    frozen['interpretation_id'],frozen['interpretation_fingerprint'],frozen['state_sha256']) as state:
                self.store.checked_link(row)
                self._current(frozen,state=state)
                self._snapshot(frozen['project'],row['request']['files'])
                if self.cancelled() or revision()!=row['policy_revision']:
                    raise ValueError('Follow-up authority changed before its durable claim.')
                return self.queue.claim(row['request']['job_id'],now=self.clock())
        except (ValueError,OSError):
            # Cancelling a waiting row cannot alter a concurrent completed claim.
            return self.queue.cancel(row['request']['job_id'],now=self.clock(),
                reason='Follow-up context or saved source changed or is unavailable; prepare a fresh review.')

    def run_one(self, job_id=None, *, recover=False):
        # Validate every retained association before the shared scheduler touches
        # its queue. Recovery queries an existing receipt without currency checks.
        self.store.public_jobs()
        result=self._run_one(job_id,recover=recover)
        return self.store.public_job(result['job_id']) if result is not None else None
