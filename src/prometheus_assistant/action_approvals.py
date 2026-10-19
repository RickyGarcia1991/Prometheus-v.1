"""Short-lived, exact owner approvals. Model output never enters this module."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

from .coding_workspace import _no_links, _write_atomic


def decision_text(text):
    """Recognize a complete confirmation, not a quoted or embedded instruction."""
    if not isinstance(text, str) or len(text) > 100:
        return None
    phrase = re.sub(r'\s+', ' ', text.strip().casefold()).rstrip('.!')
    phrase = re.sub(r',\s*', ' ', phrase)
    if phrase in {'yes', 'yes please', 'approve', 'approve it', 'approve this change',
                  'yes approve', 'yes approve it', 'go ahead', 'yes go ahead',
                  'please go ahead', 'do it', 'proceed', 'allow', 'allow it'}:
        return 'approve'
    if phrase in {'no', 'no thanks', 'reject', 'reject it', 'reject this change',
                  'cancel', 'cancel it', 'cancel the change', 'do not proceed',
                  "don't proceed", 'stop', 'deny', 'deny it'}:
        return 'reject'
    return None


class ActionApprovals:
    """One pending action per authenticated owner/browser session, held in RAM.

    A restart revokes pending approvals. Consuming an approval removes it before
    dispatch, so failures cannot turn a retry into an unintended second action.
    """
    def __init__(self, *, clock=time.monotonic, lifetime=600, receipt_root=None, history_root=None):
        if not 1 <= lifetime <= 600:
            raise ValueError('Approval lifetime must be at most ten minutes.')
        self.clock, self.lifetime = clock, lifetime
        self._pending = {}
        self._revision_intents = {}
        self._capability_revision_intents = {}
        self._application_starts = {}
        self._lock = threading.RLock()
        self.receipt_root = Path(receipt_root) if receipt_root is not None else None
        from .decision_history import DecisionHistory
        self.decision_history = DecisionHistory(history_root) if history_root is not None else None

    def _expire(self):
        now = self.clock()
        self._pending = {key: row for key, row in self._pending.items() if row['expires_at'] > now}
        self._revision_intents = {key: row for key,row in self._revision_intents.items()
                                  if row['created_at'] <= now < row['expires_at']}
        self._capability_revision_intents = {key: row for key,row in self._capability_revision_intents.items()
                                             if row['created_at'] <= now < row['expires_at']}
        self._application_starts = {key: value for key,value in self._application_starts.items()
                                    if value['expires_at'] > now}

    @staticmethod
    def _key(owner, binding):
        if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                   for value in (owner, binding)):
            raise ValueError('A current authenticated owner session is required.')
        return owner, binding

    def has_binding(self, binding):
        with self._lock:
            self._expire()
            return any(key[1] == binding for key in self._pending)

    def pending(self, *, owner, binding):
        key = self._key(owner, binding)
        with self._lock:
            self._expire()
            row = self._pending.get(key)
            return copy.deepcopy(row) if row else None

    def peek(self, *, owner, binding):
        """Project this session's live proposal without expiring any stored row."""
        key = self._key(owner, binding)
        with self._lock:
            row = self._pending.get(key)
            return copy.deepcopy(row) if row and row['expires_at'] > self.clock() else None

    def review(self, *, owner, binding, session, kind, payload, review, policy_revision,
               revision_token='', project_hash='', capability_revision_token='', capability_subject=''):
        key = self._key(owner, binding)
        if not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session):
            raise ValueError('An exact conversation is required for approval.')
        if type(revision_token) is not str or type(project_hash) is not str:
            raise ValueError('Invalid revision context.')
        if type(capability_revision_token) is not str or type(capability_subject) is not str:
            raise ValueError('Invalid capability revision context.')
        if revision_token and capability_revision_token:
            raise ValueError('A proposal cannot combine project and capability revision contexts.')
        if capability_subject or capability_revision_token:
            from .decision_history import CAPABILITY_REVISION_KINDS, _identity
            if kind not in CAPABILITY_REVISION_KINDS:
                raise ValueError('This capability does not support revision relationships.')
            _identity(capability_subject,64)
        document = json.loads(json.dumps({'kind': kind, 'payload': payload, 'review': review,
            'session': session, 'policy_revision': policy_revision,
            **({'capability_subject':capability_subject} if capability_subject else {})}, allow_nan=False, sort_keys=True))
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self._lock:
            self._expire()
            if key in self._pending:
                raise ValueError('Approve or reject the existing proposal before preparing another.')
            row = {**document, 'id': secrets.token_hex(16), 'fingerprint': digest,
                   'expires_at': self.clock() + self.lifetime, 'expires_in_seconds': self.lifetime}
            if revision_token:
                if kind!='project_edit':raise ValueError('Only project edits can revise a proposal.')
                self.validate_revision_context(owner=owner,binding=binding,session=session,
                    project_hash=project_hash,revision_token=revision_token)
                intent=self._revision_intents[revision_token]
                self._revisions().record(dict(owner=owner,session=session,project_hash=project_hash,
                    parent_id=intent['parent_id'],parent_fingerprint=intent['parent_fingerprint'],
                    child_id=row['id'],child_fingerprint=digest,created_at=time.time()))
                row['origin_approval']=intent['parent_id']
                del self._revision_intents[revision_token]
            if capability_revision_token:
                self.validate_capability_revision_context(owner=owner,binding=binding,session=session,
                    kind=kind,subject_hash=capability_subject,capability_revision_token=capability_revision_token)
                intent=self._capability_revision_intents[capability_revision_token]
                self._capability_revisions().record(dict(owner=owner,session=session,kind=kind,
                    subject_hash=capability_subject,parent_id=intent['parent_id'],
                    parent_fingerprint=intent['parent_fingerprint'],child_id=row['id'],
                    child_fingerprint=digest,created_at=time.time()))
                row['origin_approval']=intent['parent_id']
                del self._capability_revision_intents[capability_revision_token]
            self._pending[key] = row
            return copy.deepcopy(row)

    def consume(self, *, owner, binding, session, approval_id, fingerprint, policy_revision, decision, feedback=''):
        key = self._key(owner, binding)
        if type(decision) is not str or decision not in {'approve', 'reject', 'request_changes'}:
            raise ValueError('Choose approve, reject or request changes.')
        with self._lock:
            self._expire()
            row = self._pending.get(key)
            if (not row or row['session'] != session or row['id'] != approval_id
                    or not secrets.compare_digest(row['fingerprint'], fingerprint)):
                raise ValueError('This exact approval is missing, expired, or belongs to another conversation.')
            from .decision_history import KINDS, CAPABILITY_REVISION_KINDS, _identity, validate_feedback
            feedback = validate_feedback(decision, row['kind'], feedback)
            if decision=='request_changes' and row['kind'] in CAPABILITY_REVISION_KINDS:
                _identity(row.get('capability_subject'),64)
            if decision in {'approve', 'request_changes'} and row['policy_revision'] != policy_revision:
                del self._pending[key]
                raise ValueError('Application policy changed. Prepare a new review before approval.')
            if decision == 'approve' and row['kind'] == 'self_improve' and len(self._application_starts) >= 128:
                raise ValueError('Too many unfinished application evidence starts.')
            if self.decision_history is not None:
                self.decision_history.record(dict(schema_version=1,id=row['id'],owner=owner,
                    fingerprint=row['fingerprint'],session=row['session'],
                    kind=row['kind'] if row['kind'] in KINDS else 'other',
                    decision=decision,feedback=feedback,decided_at=time.time()))
            del self._pending[key]
            if decision == 'approve' and row['kind'] == 'self_improve':
                self._application_starts[(owner,row['id'],row['fingerprint'])] = dict(
                    row=copy.deepcopy(row), binding=binding, expires_at=self.clock()+self.lifetime)
            return copy.deepcopy(row)

    def history(self, *, owner, limit=50):
        self._key(owner, '0'*64)
        if self.decision_history is None:
            raise ValueError('Decision history is not configured.')
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Decision history limit must be between 1 and 50.')
        all_rows = self.decision_history._all_rows()
        rows = [{key:value for key,value in row.items() if key!='owner'}
                for row in all_rows if row['owner']==owner][:limit]
        events = self._outcomes().read()
        capability_events = self.capability_outcomes().read()
        application_events = self.application_outcomes().read(decisions=all_rows)
        revisions,recorded_children = self._revisions().read_with_states()
        capability_revisions,capability_children = self._capability_revisions().read_with_states()
        from .shared_capability_evidence import for_owner
        from .evidence_lifecycle import for_owner as lifecycle_for_owner
        shared = for_owner(owner,capability_events,project_events=events)
        # Compare the complete bounded owner ledger, not just the displayed page.
        # A contradictory original result may be older than the latest 50 rows.
        lifecycle = lifecycle_for_owner(owner,decisions=all_rows,
            project_events=events,capability_events=capability_events,application_events=application_events)
        from .evidence_conflicts import for_owner as conflicts_for_owner
        conflicts = conflicts_for_owner(owner,decisions=all_rows,
            project_events=events,capability_events=capability_events,application_events=application_events)
        from .evidence_resolution import for_owner as resolutions_for_owner, empty_projection
        resolution_records = self.evidence_resolutions().read(decisions=all_rows)
        interpretations = resolutions_for_owner(owner,records=resolution_records,
            decisions=all_rows,conflicts=conflicts)
        from .evidence_interpretation_lifecycle import for_owner as changes_for_owner, empty_projection as empty_changes
        interpretation_changes = changes_for_owner(owner,
            records=self.interpretation_lifecycle().read(decisions=all_rows,resolutions=resolution_records),
            resolutions=resolution_records,decisions=all_rows,conflicts=conflicts)
        for row in rows:
            row['outcomes'] = [event for o,i,f,event in events if o==owner and i==row['id'] and f==row['fingerprint']]
            row['capability_outcomes'] = [event for o,i,f,event in capability_events
                                         if o==owner and i==row['id'] and f==row['fingerprint']]
            row['application_outcomes'] = [event for o,i,f,event in application_events
                                          if o==owner and i==row['id'] and f==row['fingerprint']]
            row['revisions'] = [{**{k:link[k] for k in ('parent_id','parent_fingerprint','child_id','child_fingerprint','created_at')},
                'child_state':'recorded' if (owner,link['child_id']) in recorded_children else 'unrecorded'}
                for link in revisions if link['owner']==owner and
                ((link['parent_id']==row['id'] and link['parent_fingerprint']==row['fingerprint']) or
                 (link['child_id']==row['id'] and link['child_fingerprint']==row['fingerprint']))]
            row['capability_revisions'] = [{**{k:link[k] for k in ('kind','parent_id','parent_fingerprint',
                'child_id','child_fingerprint','created_at')},
                'child_state':'recorded' if (owner,link['child_id']) in capability_children else 'unrecorded'}
                for link in capability_revisions if link['owner']==owner and
                ((link['parent_id']==row['id'] and link['parent_fingerprint']==row['fingerprint']) or
                 (link['child_id']==row['id'] and link['child_fingerprint']==row['fingerprint']))]
            row['shared_evidence'] = shared.get((row['id'],row['fingerprint']),[])
            row['evidence_lifecycle'] = lifecycle[(row['id'],row['fingerprint'])]
            row['evidence_conflict'] = conflicts.get((row['id'],row['fingerprint']))
            row['evidence_resolutions'] = interpretations.get((row['id'],row['fingerprint']),empty_projection())
            row['interpretation_lifecycle'] = interpretation_changes.get((row['id'],row['fingerprint']),empty_changes())
        return rows

    def evidence_resolutions(self):
        from .evidence_resolution import EvidenceResolutions
        return EvidenceResolutions(self.decision_history)

    def interpretation_lifecycle(self):
        from .evidence_interpretation_lifecycle import EvidenceInterpretationLifecycle
        return EvidenceInterpretationLifecycle(self.decision_history)

    def _interpretation_change_inputs(self, owner, interpretation_id, interpretation_fingerprint):
        from .evidence_interpretation_lifecycle import for_owner
        decisions, conflicts = self._evidence_conflict_inputs(owner)
        resolutions = self.evidence_resolutions().read(decisions=decisions)
        projection = for_owner(owner,
            records=self.interpretation_lifecycle().read(decisions=decisions,resolutions=resolutions),
            resolutions=resolutions,decisions=decisions,conflicts=conflicts)
        candidates = projection.get((interpretation_id,interpretation_fingerprint),{}).get('items',[])
        item = next((row for row in candidates if row['interpretation_id']==interpretation_id
                     and row['interpretation_fingerprint']==interpretation_fingerprint),None)
        if item is None:
            raise ValueError('Only an exact completed approved interpretation can be changed.')
        return item, conflicts

    def prepare_interpretation_change(self, *, owner, binding, session, interpretation_id,
                                     interpretation_fingerprint, expected_state_sha256, operation,
                                     reason, snapshot_sha256=None, fact_sha256=None, policy_revision):
        """Prepare an exact RAM-only lifecycle review without changing history."""
        self._key(owner,binding)
        from .decision_history import _identity
        from .evidence_resolution import FACT_FIELDS, reason_text, policy_text
        from .evidence_interpretation_lifecycle import KIND, review_document, validate_payload
        for value,size in ((session,32),(interpretation_id,32),(interpretation_fingerprint,64),(expected_state_sha256,64)):
            _identity(value,size)
        reason=reason_text(reason);policy_text(policy_revision)
        if type(operation) is not str or operation not in {'revise','withdraw'}:
            raise ValueError('Choose revise or withdraw.')
        if operation=='withdraw' and (snapshot_sha256 is not None or fact_sha256 is not None):
            raise ValueError('Withdrawal cannot specify a replacement snapshot or fact.')
        if operation=='revise':
            _identity(snapshot_sha256,64);_identity(fact_sha256,64)
        with self._lock:
            item,conflicts=self._interpretation_change_inputs(owner,interpretation_id,interpretation_fingerprint)
            if item['state']!='active' or item['state_sha256']!=expected_state_sha256:
                raise ValueError('Interpretation state changed or is withdrawn; prepare a fresh exact review.')
            replacement=None
            if operation=='revise':
                conflict=conflicts.get((item['target_id'],item['target_fingerprint']))
                if (not conflict or conflict['snapshot_sha256']!=snapshot_sha256
                        or any(conflict[k]!=item[k] for k in ('source','reference'))):
                    raise ValueError('Exact current owned conflict inputs are required for revision.')
                variant=next((v for v in conflict['variants'] if v['fact_sha256']==fact_sha256),None)
                if variant is None:
                    raise ValueError('Choose a shown native fact variant from this conflict.')
                replacement={k:item[k] for k in ('target_id','target_fingerprint','source','reference')}
                replacement.update(snapshot_sha256=snapshot_sha256,fact_sha256=fact_sha256,
                    selected_fact={k:copy.deepcopy(variant[k]) for k in FACT_FIELDS},reason=reason)
            payload=dict(interpretation_id=interpretation_id,interpretation_fingerprint=interpretation_fingerprint,
                expected_state_sha256=expected_state_sha256,operation=operation,reason=reason,replacement=replacement)
            validate_payload(payload)
            return self.review(owner=owner,binding=binding,session=session,kind=KIND,payload=payload,
                               review=review_document(payload),policy_revision=policy_revision)

    def decide_interpretation_change(self, *, owner, binding, session, approval_id, fingerprint,
                                    decision, policy_revision):
        """Link before consume; only the latest exact attempt may complete."""
        key=self._key(owner,binding)
        from .decision_history import _identity
        from .evidence_resolution import proposal, policy_text
        from .evidence_interpretation_lifecycle import KIND, validate_payload
        for value,size in ((session,32),(approval_id,32),(fingerprint,64)):
            _identity(value,size)
        policy_text(policy_revision)
        if type(decision) is not str or decision not in {'approve','reject'}:
            raise ValueError('Interpretation changes support approve or reject only.')
        with self._lock:
            self._expire();row=self._pending.get(key)
            if (not row or row['kind']!=KIND or row['session']!=session or row['id']!=approval_id
                    or not secrets.compare_digest(row['fingerprint'],fingerprint)):
                raise ValueError('Exact current interpretation-change review is missing, expired or belongs to another session.')
            if row['policy_revision']!=policy_revision:
                del self._pending[key]
                raise ValueError('Application policy changed. Prepare a new interpretation-change review.')
            payload=validate_payload(row['payload'])
            def current_inputs():
                if decision=='approve' and payload['operation']=='revise':
                    _decisions,conflicts=self._evidence_conflict_inputs(owner)
                    self._checked_resolution_target(conflicts,payload['replacement'])
            current_inputs()
            store=self.interpretation_lifecycle()
            store.link(proposal(owner,row,time.time()),check=current_inputs)
            # A later link can win between this consume and completion. Its
            # SQLite sequence permanently prevents this attempt taking effect.
            current_inputs()
            consumed=self.consume(owner=owner,binding=binding,session=session,approval_id=approval_id,
                fingerprint=fingerprint,policy_revision=policy_revision,decision=decision)
            try:
                store.finish(dict(schema_version=1,owner=owner,approval_id=approval_id,fingerprint=fingerprint,
                                  decision=decision,recorded_at=time.time()),check=current_inputs)
            except (ValueError,OSError) as error:
                raise ValueError('The owner decision was saved, but interpretation-change completion is unconfirmed. Prepare a fresh review; this attempt grants no authority.') from error
            item,_conflicts=self._interpretation_change_inputs(owner,payload['interpretation_id'],payload['interpretation_fingerprint'])
            return {'approval':consumed,'interpretation_change':item}

    def _evidence_conflict_inputs(self, owner):
        """Collect complete bounded history; no native refresh, action or writes."""
        if self.decision_history is None:raise ValueError('Decision history is not configured.')
        from .evidence_conflicts import for_owner
        decisions=self.decision_history._all_rows()
        conflicts=for_owner(owner,decisions=decisions,project_events=self._outcomes().read(),
            capability_events=self.capability_outcomes().read(),
            application_events=self.application_outcomes().read(decisions=decisions))
        return decisions,conflicts

    @staticmethod
    def _checked_resolution_target(conflicts, payload):
        conflict=conflicts.get((payload['target_id'],payload['target_fingerprint']))
        if (not conflict or any(conflict[key]!=payload[key] for key in ('source','reference','snapshot_sha256'))
                or not any(variant['fact_sha256']==payload['fact_sha256'] and
                    all(variant[k]==v for k,v in payload['selected_fact'].items()) for variant in conflict['variants'])):
            raise ValueError('Conflict inputs changed or the selected fact is unavailable. Prepare a fresh interpretation review.')

    def prepare_evidence_resolution(self, *, owner, binding, session, target_id, target_fingerprint,
                                    snapshot_sha256, fact_sha256, reason, policy_revision):
        """Prepare one exact RAM-only interpretation; no sidecar writes on open."""
        self._key(owner,binding)
        from .decision_history import _identity
        from .evidence_resolution import review_document, FACT_FIELDS, reason_text, policy_text, validate_payload
        for value,size in ((session,32),(target_id,32),(target_fingerprint,64),(snapshot_sha256,64),(fact_sha256,64)):
            _identity(value,size)
        reason=reason_text(reason);policy_text(policy_revision)
        with self._lock:
            _decisions,conflicts=self._evidence_conflict_inputs(owner)
            conflict=conflicts.get((target_id,target_fingerprint))
            if not conflict or conflict['snapshot_sha256']!=snapshot_sha256:
                raise ValueError('Exact current owned conflict inputs are required.')
            variant=next((v for v in conflict['variants'] if v['fact_sha256']==fact_sha256),None)
            if variant is None:raise ValueError('Choose a shown native fact variant from this conflict.')
            payload=dict(target_id=target_id,target_fingerprint=target_fingerprint,source=conflict['source'],
                reference=conflict['reference'],snapshot_sha256=snapshot_sha256,fact_sha256=fact_sha256,
                selected_fact={k:copy.deepcopy(variant[k]) for k in FACT_FIELDS},reason=reason)
            validate_payload(payload)
            # A malformed/full sidecar is checked again on the decision attempt;
            # merely opening this review never creates an audit or native result.
            self.evidence_resolutions().read(decisions=_decisions)
            return self.review(owner=owner,binding=binding,session=session,kind='evidence_resolution',
                               payload=payload,review=review_document(payload),policy_revision=policy_revision)

    def decide_evidence_resolution(self, *, owner, binding, session, approval_id, fingerprint, decision, policy_revision):
        """Record only a historical owner interpretation, without executing work."""
        key=self._key(owner,binding)
        from .decision_history import _identity
        from .evidence_resolution import proposal, validate_payload, policy_text, for_owner
        for value,size in ((session,32),(approval_id,32),(fingerprint,64)):_identity(value,size)
        if type(decision) is not str or decision not in {'approve','reject'}:
            raise ValueError('Interpretations support approve or reject only.')
        policy_text(policy_revision)
        with self._lock:
            self._expire();row=self._pending.get(key)
            if (not row or row['kind']!='evidence_resolution' or row['session']!=session
                    or row['id']!=approval_id or not secrets.compare_digest(row['fingerprint'],fingerprint)):
                raise ValueError('Exact current interpretation review is missing, expired or belongs to another session.')
            if row['policy_revision']!=policy_revision:
                del self._pending[key]
                raise ValueError('Application policy changed. Prepare a new interpretation review.')
            payload=validate_payload(row['payload'])
            _decisions,conflicts=self._evidence_conflict_inputs(owner)
            if decision=='approve':self._checked_resolution_target(conflicts,payload)
            store=self.evidence_resolutions()
            # Durable association precedes consumption: a later write failure
            # stays visibly unrecorded and cannot restore the consumed approval.
            store.link(proposal(owner,row,time.time()))
            _decisions,conflicts=self._evidence_conflict_inputs(owner)
            if decision=='approve':self._checked_resolution_target(conflicts,payload)
            consumed=self.consume(owner=owner,binding=binding,session=session,approval_id=approval_id,
                fingerprint=fingerprint,policy_revision=policy_revision,decision=decision)
            try:
                store.finish(dict(schema_version=1,owner=owner,approval_id=approval_id,fingerprint=fingerprint,
                                  decision=decision,recorded_at=time.time()))
            except (ValueError,OSError) as error:
                raise ValueError('The owner decision was saved, but interpretation completion is unconfirmed. History remains unrecorded; prepare a new review rather than replaying this approval.') from error
            decisions,conflicts=self._evidence_conflict_inputs(owner)
            projection=for_owner(owner,records=store.read(decisions=decisions),decisions=decisions,conflicts=conflicts)
            item=projection[(approval_id,fingerprint)]['items'][0]
            return {'approval':consumed,'resolution':item}

    def _revisions(self):
        if self.decision_history is None:raise ValueError('Decision history is not configured.')
        from .decision_revisions import DecisionRevisions
        return DecisionRevisions(self.decision_history)

    def revision_context(self, *, owner, binding, session, parent_row, project_hash):
        self._key(owner,binding)
        from .decision_history import _identity
        _identity(session,32);_identity(project_hash,64)
        if type(parent_row) is not dict:raise ValueError('Exact saved parent required.')
        with self._lock:
            self._expire()
            store=self._revisions()
            parent=store.history.lookup(owner,parent_row.get('id'))
            if (not parent or parent['session']!=session or parent['kind']!='project_edit'
                    or parent['decision']!='request_changes' or parent['fingerprint']!=parent_row.get('fingerprint')):
                raise ValueError('Exact saved change request required.')
            if len(self._revision_intents)>=128:raise ValueError('Too many revision contexts.')
            token=secrets.token_hex(16);now=self.clock()
            self._revision_intents[token]=dict(owner=owner,binding=binding,session=session,project_hash=project_hash,
                parent_id=parent['id'],parent_fingerprint=parent['fingerprint'],created_at=now,expires_at=now+self.lifetime)
            return dict(revision_token=token,origin_approval=parent['id'])

    def validate_revision_context(self, *, owner, binding, session, project_hash, revision_token):
        """Read-only early validation; review repeats this under its publication lock."""
        self._key(owner,binding)
        from .decision_history import _identity
        _identity(session,32);_identity(project_hash,64);_identity(revision_token,32)
        with self._lock:
            intent=self._revision_intents.get(revision_token);now=self.clock()
            if (not intent or not intent['created_at']<=now<intent['expires_at'] or
                    any(intent[k]!=v for k,v in [('owner',owner),('binding',binding),
                        ('session',session),('project_hash',project_hash)])):
                raise ValueError('Revision context is missing, expired or mismatched.')
            parent=self._revisions().history.lookup(owner,intent['parent_id'])
            if (not parent or parent['kind']!='project_edit' or parent['decision']!='request_changes'
                    or parent['session']!=session or parent['fingerprint']!=intent['parent_fingerprint']):
                raise ValueError('Revision parent no longer matches its saved receipt.')
            return dict(origin_approval=parent['id'])

    def capability_outcomes(self):
        if self.decision_history is None:raise ValueError('Decision history is not configured.')
        from .capability_outcomes import CapabilityOutcomes
        return CapabilityOutcomes(self.decision_history)

    def application_outcomes(self):
        from .application_outcomes import ApplicationOutcomes
        return ApplicationOutcomes(self.decision_history)

    def start_application_outcome(self, *, owner, row):
        """Only this process's freshly consumed row can establish a new link."""
        self._key(owner, '0'*64)
        if type(row) is not dict:raise ValueError('Fresh consumed application decision required.')
        from .decision_history import _identity
        _identity(row.get('id'),32); _identity(row.get('fingerprint'),64)
        with self._lock:
            self._expire()
            key = owner, row.get('id'), row.get('fingerprint')
            fresh = self._application_starts.pop(key, None)
            if fresh is None or fresh['row'] != row:
                raise ValueError('Fresh consumed application decision required.')
            receipt = self.approved_job(owner=owner,job_id=row['payload']['job_id'])
            return self.application_outcomes().start(owner=owner,row=row,approved_job=receipt)

    def _capability_revisions(self):
        if self.decision_history is None:raise ValueError('Decision history is not configured.')
        from .capability_revisions import CapabilityRevisions
        return CapabilityRevisions(self.decision_history)

    def capability_revision_context(self, *, owner, binding, session, kind, subject_hash, parent_row):
        """Issue a RAM-only link context from an exact consumed change request."""
        self._key(owner,binding)
        from .decision_history import CAPABILITY_REVISION_KINDS, _identity
        _identity(session,32);_identity(subject_hash,64)
        if kind not in CAPABILITY_REVISION_KINDS or type(parent_row) is not dict:
            raise ValueError('Exact supported capability parent required.')
        if (parent_row.get('kind')!=kind or parent_row.get('session')!=session
                or parent_row.get('capability_subject')!=subject_hash):
            raise ValueError('Capability parent subject or kind differs.')
        try:
            document={key:parent_row[key] for key in ('kind','payload','review','session','policy_revision','capability_subject')}
            digest=hashlib.sha256(json.dumps(document,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        except (KeyError,TypeError,ValueError) as error:
            raise ValueError('Exact capability parent document required.') from error
        if digest!=parent_row.get('fingerprint'):
            raise ValueError('Capability parent document differs from its fingerprint.')
        with self._lock:
            self._expire()
            parent=self._capability_revisions().history.lookup(owner,parent_row.get('id'))
            if (not parent or parent['kind']!=kind or parent['session']!=session
                    or parent['decision']!='request_changes' or parent['fingerprint']!=digest):
                raise ValueError('Exact saved capability change request required.')
            if len(self._capability_revision_intents)>=128:raise ValueError('Too many capability revision contexts.')
            token=secrets.token_hex(16);now=self.clock()
            self._capability_revision_intents[token]=dict(owner=owner,binding=binding,session=session,kind=kind,
                subject_hash=subject_hash,parent_id=parent['id'],parent_fingerprint=parent['fingerprint'],
                created_at=now,expires_at=now+self.lifetime)
            return dict(capability_revision_token=token,origin_approval=parent['id'])

    def validate_capability_revision_context(self, *, owner, binding, session, kind, subject_hash, capability_revision_token):
        """Read-only early check; publication repeats it under the approval lock."""
        self._key(owner,binding)
        from .decision_history import CAPABILITY_REVISION_KINDS, _identity
        _identity(session,32);_identity(subject_hash,64);_identity(capability_revision_token,32)
        if kind not in CAPABILITY_REVISION_KINDS:raise ValueError('Unsupported capability revision kind.')
        with self._lock:
            intent=self._capability_revision_intents.get(capability_revision_token);now=self.clock()
            if (not intent or not intent['created_at']<=now<intent['expires_at'] or
                    any(intent[k]!=v for k,v in [('owner',owner),('binding',binding),('session',session),
                        ('kind',kind),('subject_hash',subject_hash)])):
                raise ValueError('Capability revision context is missing, expired or mismatched.')
            parent=self._capability_revisions().history.lookup(owner,intent['parent_id'])
            if (not parent or parent['kind']!=kind or parent['decision']!='request_changes'
                    or parent['session']!=session or parent['fingerprint']!=intent['parent_fingerprint']):
                raise ValueError('Capability revision parent no longer matches its saved receipt.')
            return dict(origin_approval=parent['id'])

    def _outcomes(self):
        if self.decision_history is None:raise ValueError('Decision history is not configured.')
        from .decision_outcomes import DecisionOutcomes
        return DecisionOutcomes(self.decision_history)

    def record_outcome(self, *, owner, approval_id, fingerprint, event):
        return self._outcomes().record(owner,approval_id,fingerprint,event)

    def outcomes_for_edit(self, *, owner, project_hash, edit_id):
        from .decision_history import _identity
        _identity(owner,64);_identity(project_hash,64)
        if type(edit_id) is not str or not re.fullmatch(r'(?:group-)?[0-9a-f]{32}',edit_id):raise ValueError('Exact edit identity required.')
        store=self._outcomes();result=[]
        for o,i,f,event in store.read():
            if o==owner and event['phase']=='result' and event['edit_id']==edit_id and event['project_hash']==project_hash:
                store._approved(o,i,f)
                result.append({'approval_id':i,'fingerprint':f})
        return result

    def revoke_binding(self, binding):
        with self._lock:
            self._pending = {key: row for key, row in self._pending.items() if key[1] != binding}
            self._revision_intents = {key:row for key,row in self._revision_intents.items() if row['binding']!=binding}
            self._capability_revision_intents = {key:row for key,row in self._capability_revision_intents.items() if row['binding']!=binding}
            self._application_starts = {key:row for key,row in self._application_starts.items() if row['binding']!=binding}

    def _receipt_path(self, job_id):
        if self.receipt_root is None:
            raise ValueError('Approved-change receipts are not configured.')
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('Invalid approved change identifier.')
        path = self.receipt_root / (job_id + '.json')
        _no_links(path)
        return path

    def record_approved(self, *, owner, job_id, candidate_fingerprint):
        """Minimal ownership needed to read activation status after a restart."""
        self._key(owner, candidate_fingerprint)
        path = self._receipt_path(job_id)
        row = {'job_id': job_id, 'owner': owner, 'candidate_fingerprint': candidate_fingerprint,
               'approved_at_ns': time.time_ns(), 'dispatch_state': 'dispatching'}
        with self._lock:
            if path.exists():
                existing = self.approved_job(owner=owner, job_id=job_id)
                if existing['candidate_fingerprint'] != candidate_fingerprint:
                    raise ValueError('This approved job has different candidate bytes.')
                return existing
            self.receipt_root.mkdir(parents=True, exist_ok=True)
            _no_links(path)
            _write_atomic(path, (json.dumps(row, sort_keys=True)+'\n').encode())
            return row

    def record_dispatch(self, *, owner, job_id, state, error=''):
        if state not in {'promotion_pending', 'failed'}:
            raise ValueError('Invalid activation dispatch state.')
        with self._lock:
            row = self.approved_job(owner=owner, job_id=job_id)
            row.update(dispatch_state=state, dispatch_error=str(error)[:256])
            _write_atomic(self._receipt_path(job_id), (json.dumps(row, sort_keys=True)+'\n').encode())

    def approved_job(self, *, owner, job_id):
        self._key(owner, '0'*64)
        row = self._read_approved(job_id)
        if row['owner'] != owner:
            raise ValueError('No approved change belongs to this owner with that identifier.')
        return row

    def _read_approved(self, job_id):
        path = self._receipt_path(job_id)
        if not path.is_file() or path.stat().st_size > 2048:
            raise ValueError('No approved change belongs to this owner with that identifier.')
        with path.open('rb') as stream:
            data = stream.read(2049)
        if len(data) > 2048:
            raise ValueError('Approved-change receipt exceeds its limit.')
        row = json.loads(data)
        if (not isinstance(row, dict) or not isinstance(row.get('owner'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['owner']) or row.get('job_id') != job_id
                or type(row.get('approved_at_ns')) is not int
                or not isinstance(row.get('candidate_fingerprint'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['candidate_fingerprint'])):
            raise ValueError('No approved change belongs to this owner with that identifier.')
        return row

    def latest_approved(self, *, owner):
        self._key(owner, '0'*64)
        if self.receipt_root is None:
            raise ValueError('Approved-change receipts are not configured.')
        _no_links(self.receipt_root)
        latest = None
        for count, path in enumerate(self.receipt_root.glob('*.json')):
            if count >= 4096:
                raise ValueError('Too many approved-change receipts to select one safely.')
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            row = self._read_approved(path.stem)
            if row['owner'] != owner:
                continue
            if latest is None or row['approved_at_ns'] > latest['approved_at_ns']:
                latest = row
        return latest
