"""Append-only interpretation changes; historical explanations, never authority."""
from contextlib import closing
import copy
import json
import sqlite3

from .decision_history import DecisionHistory, MAX_DATABASE_BYTES, _identity
from .evidence_resolution import (EvidenceResolutions, canonical, digest, policy_text,
                                  reason_text, stamp, validate_payload as resolution_payload)

MAX_CHANGES = 4096
MAX_ITEMS = 16
MAX_RECORD_BYTES = 128 * 1024
KIND = 'evidence_interpretation_change'
FIELDS = {'interpretation_id', 'interpretation_fingerprint', 'expected_state_sha256',
          'operation', 'reason', 'replacement'}
REVIEW = {'summary': 'Change a recorded owner interpretation.',
          'effect': 'Original interpretations, observations and failed checks remain in history. No execution or retry is authorized.'}
SCHEMA = 'CREATE TABLE changes (sequence INTEGER PRIMARY KEY, owner TEXT NOT NULL, approval_id TEXT NOT NULL, phase TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(owner,approval_id,phase))'


def validate_payload(value):
    if type(value) is not dict or set(value) != FIELDS:
        raise ValueError('Invalid interpretation change payload.')
    for key in ('interpretation_id', 'interpretation_fingerprint', 'expected_state_sha256'):
        _identity(value[key], 32 if key == 'interpretation_id' else 64)
    if type(value['operation']) is not str or value['operation'] not in {'revise', 'withdraw'}:
        raise ValueError('Choose revise or withdraw.')
    if reason_text(value['reason']) != value['reason']:
        raise ValueError('Interpretation change reason must be trimmed.')
    if value['operation'] == 'withdraw':
        if value['replacement'] is not None:
            raise ValueError('Withdrawal cannot replace native facts.')
    else:
        resolution_payload(value['replacement'])
        if value['replacement']['reason'] != value['reason']:
            raise ValueError('Replacement explanation differs from the reviewed reason.')
    return value


def review_document(payload):
    validate_payload(payload)
    return {**copy.deepcopy(REVIEW), **copy.deepcopy(payload)}


def completed_roots(resolutions):
    return {(p['owner'], p['approval_id']): (p, result) for p, result in resolutions
            if result and result['decision'] == 'approve'}


def initial_state(root):
    proposal, completion = root
    return dict(head=digest({'root': proposal, 'completion': completion}), state='active',
                payload=copy.deepcopy(proposal['document']['payload']),
                review_id=proposal['approval_id'], review_fingerprint=proposal['fingerprint'],
                latest=None, changes=[])


def advance(state, phase, value):
    state['head'] = digest({'previous': state['head'], 'phase': phase, 'value': value})
    if phase == 'proposal':
        state['latest'] = value['approval_id']
        state['changes'].append([value, None])
    else:
        proposal = state['changes'][-1][0]
        state['changes'][-1][1] = value
        change = proposal['document']['payload']
        if value['decision'] == 'approve':
            state['review_id'], state['review_fingerprint'] = value['approval_id'], value['fingerprint']
            if change['operation'] == 'withdraw':
                state['state'] = 'withdrawn'
            else:
                state['payload'] = copy.deepcopy(change['replacement'])


class EvidenceInterpretationLifecycle(DecisionHistory):
    def __init__(self, history):
        if history is None:
            raise ValueError('Decision history is not configured.')
        self.history = history
        self.root = history.root / 'EvidenceInterpretationLifecycle'
        self.path = self.root / 'changes.sqlite3'
        self._plain()

    @staticmethod
    def _schema(db, *, create=False):
        rows = db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3").fetchall()
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if not rows and version == 0 and create:
            db.execute(SCHEMA)
            db.execute('PRAGMA user_version=1')
            return
        if version != 1 or rows != [('changes', SCHEMA)]:
            raise ValueError('Invalid interpretation lifecycle schema.')

    @staticmethod
    def _proposal(value, receipts, roots):
        if (type(value) is not dict or set(value) != {'schema_version', 'owner', 'approval_id', 'fingerprint', 'document', 'created_at'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Invalid lifecycle proposal schema.')
        for key, size in (('owner', 64), ('approval_id', 32), ('fingerprint', 64)):
            _identity(value[key], size)
        stamp(value['created_at'])
        document = value['document']
        if type(document) is not dict or set(document) != {'kind', 'payload', 'review', 'session', 'policy_revision'}:
            raise ValueError('Invalid lifecycle review document.')
        payload = validate_payload(document['payload'])
        if (document['kind'] != KIND or document['review'] != review_document(payload)
                or digest(document) != value['fingerprint']):
            raise ValueError('Interpretation change fingerprint differs.')
        _identity(document['session'], 32)
        policy_text(document['policy_revision'])
        key = value['owner'], payload['interpretation_id']
        root = roots.get(key)
        if not root or root[0]['fingerprint'] != payload['interpretation_fingerprint']:
            raise ValueError('Only an exact completed approved interpretation can be changed.')
        if value['approval_id'] == key[1] or (value['owner'], value['approval_id']) in roots:
            raise ValueError('A lifecycle attempt cannot reuse an interpretation identity.')
        original = root[0]['document']['payload']
        if payload['replacement'] is not None and any(payload['replacement'][k] != original[k]
                for k in ('target_id', 'target_fingerprint', 'source', 'reference')):
            raise ValueError('Replacement must retain the original native target and evidence namespace.')
        saved = receipts.get((value['owner'], value['approval_id']))
        if saved and (saved['fingerprint'] != value['fingerprint'] or saved['session'] != document['session']
                or saved['kind'] != 'other' or saved['decision'] not in {'approve', 'reject'}):
            raise ValueError('Lifecycle canonical decision differs.')
        return key

    @staticmethod
    def _completion(value, proposal, receipts):
        if (type(value) is not dict or set(value) != {'schema_version', 'owner', 'approval_id', 'fingerprint', 'decision', 'recorded_at'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Invalid lifecycle completion schema.')
        stamp(value['recorded_at'])
        if not proposal or any(value[k] != proposal[k] for k in ('owner', 'approval_id', 'fingerprint')):
            raise ValueError('Lifecycle completion has no exact linked attempt.')
        receipt = receipts.get((value['owner'], value['approval_id']))
        if (type(value['decision']) is not str or value['decision'] not in {'approve', 'reject'}
                or not receipt or receipt['decision'] != value['decision']):
            raise ValueError('Lifecycle completion requires its exact consumed decision.')

    def _rows(self, db, receipts, roots):
        rows = db.execute('SELECT owner,approval_id,phase,payload FROM changes ORDER BY sequence LIMIT ?',
                          (2 * MAX_CHANGES + 1,)).fetchall()
        if len(rows) > 2 * MAX_CHANGES:
            raise ValueError('Interpretation lifecycle capacity exceeded.')
        states = {key: initial_state(root) for key, root in roots.items()}
        proposals, completed, records = {}, set(), []
        for owner, ident, phase, raw in rows:
            if type(raw) is not str or len(raw.encode()) > MAX_RECORD_BYTES:
                raise ValueError('Invalid bounded lifecycle record.')
            value = json.loads(raw)
            if phase == 'proposal':
                key = self._proposal(value, receipts, roots)
                if (owner, ident) in proposals:
                    raise ValueError('Duplicate lifecycle attempt.')
                state = states[key]
                if state['state'] != 'active' or value['document']['payload']['expected_state_sha256'] != state['head']:
                    raise ValueError('Lifecycle attempt does not match its historical pre-link state.')
                proposals[owner, ident] = key, value
            elif phase == 'decision':
                pair = proposals.get((owner, ident))
                self._completion(value, pair[1] if pair else None, receipts)
                key, proposal = pair
                state = states[key]
                if (owner, ident) in completed or state['latest'] != ident:
                    raise ValueError('Lifecycle completion is duplicate or superseded by a newer linked attempt.')
                completed.add((owner, ident))
            else:
                raise ValueError('Unknown lifecycle record phase.')
            if canonical(value) != raw or (owner, ident) != (value['owner'], value['approval_id']):
                raise ValueError('Lifecycle canonical record differs.')
            advance(state, phase, value)
            records.append((phase, value))
        if len(proposals) > MAX_CHANGES:
            raise ValueError('Interpretation lifecycle capacity exceeded.')
        return records, states, proposals, completed

    def read(self, *, decisions=None, resolutions=None):
        self._plain()
        if not self.path.exists():
            return []
        decisions = self.history._all_rows() if decisions is None else decisions
        resolutions = EvidenceResolutions(self.history).read(decisions=decisions) if resolutions is None else resolutions
        roots = completed_roots(resolutions)
        receipts = {(r['owner'], r['id']): r for r in decisions}
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                self._schema(db)
                return self._rows(db, receipts, roots)[0]
        except (sqlite3.Error, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('Interpretation lifecycle history is unavailable.') from error

    def _append(self, phase, value, check=None):
        if type(value) is not dict:
            raise ValueError('Invalid lifecycle record.')
        raw = canonical(value)
        if len(raw.encode()) > MAX_RECORD_BYTES:
            raise ValueError('Lifecycle record exceeds its size bound.')
        # Reject malformed direct inputs before creating a storage directory.
        decisions = self.history._all_rows()
        receipts = {(r['owner'], r['id']): r for r in decisions}
        roots = completed_roots(EvidenceResolutions(self.history).read(decisions=decisions))
        if phase == 'proposal':
            self._proposal(value, receipts, roots)
        elif phase == 'decision':
            prior = next((p for kind, p in self.read(decisions=decisions) if kind == 'proposal'
                          and p['owner'] == value.get('owner') and p['approval_id'] == value.get('approval_id')), None)
            self._completion(value, prior, receipts)
        else:
            raise ValueError('Invalid lifecycle phase.')
        self._plain()
        self.root.mkdir(parents=True, exist_ok=True)
        self._plain()
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.execute('PRAGMA synchronous=FULL')
                page = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute('PRAGMA max_page_count=' + str(MAX_DATABASE_BYTES // page))
                db.execute('BEGIN IMMEDIATE')
                self._schema(db, create=True)
                decisions = self.history._all_rows()
                receipts = {(r['owner'], r['id']): r for r in decisions}
                roots = completed_roots(EvidenceResolutions(self.history).read(decisions=decisions))
                records, states, proposals, completed = self._rows(db, receipts, roots)
                attempt = value['owner'], value['approval_id']
                if phase == 'proposal':
                    key = self._proposal(value, receipts, roots)
                    state = states[key]
                    if attempt in proposals:
                        prior = proposals[attempt][1]
                        if (state['latest'] != value['approval_id'] or attempt in completed
                                or any(prior[k] != v for k, v in value.items() if k != 'created_at')):
                            raise ValueError('Lifecycle attempt is already completed, changed or superseded.')
                        if check is not None:
                            check()
                        return False
                    if len(proposals) >= MAX_CHANGES:
                        raise ValueError('Interpretation lifecycle capacity full; maintenance required.')
                    if (attempt in receipts or state['state'] != 'active'
                            or value['document']['payload']['expected_state_sha256'] != state['head']):
                        raise ValueError('Interpretation state changed; prepare a fresh exact review.')
                else:
                    pair = proposals.get(attempt)
                    self._completion(value, pair[1] if pair else None, receipts)
                    state = states[pair[0]]
                    if state['latest'] != value['approval_id'] or attempt in completed:
                        raise ValueError('Lifecycle completion is superseded or already recorded.')
                if check is not None:
                    check()
                db.execute('INSERT INTO changes (owner,approval_id,phase,payload) VALUES (?,?,?,?)',
                           (value['owner'], value['approval_id'], phase, raw))
            return True
        except (sqlite3.Error, UnicodeError) as error:
            raise ValueError('Interpretation lifecycle could not be saved.') from error

    def link(self, value, *, check=None):
        return self._append('proposal', value, check)

    def finish(self, value, *, check=None):
        return self._append('decision', value, check)


def empty_projection():
    return dict(schema_version=1, read_only=True, freshness='historical', total=0, truncated=False, items=[])


def for_owner(owner, *, records, resolutions, decisions, conflicts):
    _identity(owner, 64)
    roots = {key: value for key, value in completed_roots(resolutions).items() if key[0] == owner}
    states = {key: initial_state(root) for key, root in roots.items()}
    for phase, value in records:
        if value['owner'] != owner:
            continue
        if phase == 'proposal':
            key = owner, value['document']['payload']['interpretation_id']
        else:
            key = next(k for k, state in states.items() if state['latest'] == value['approval_id'])
        advance(states[key], phase, value)
    original_groups, effective_groups = {}, {}
    for key, (proposal, _) in roots.items():
        original = proposal['document']['payload']
        original_groups.setdefault(tuple(original[k] for k in ('source', 'reference', 'snapshot_sha256')), set()).add(original['fact_sha256'])
        state = states[key]
        payload = state['payload']
        if state['state'] == 'active':
            effective_groups.setdefault(tuple(payload[k] for k in ('source', 'reference', 'snapshot_sha256')), set()).add(payload['fact_sha256'])
    receipts = {(r['owner'], r['id']): r for r in decisions}
    result = {}
    for key, (proposal, _) in sorted(roots.items(), key=lambda pair: (-pair[1][0]['created_at'], pair[0][1])):
        state = states[key]
        original, payload = proposal['document']['payload'], state['payload']
        current = conflicts.get((original['target_id'], original['target_fingerprint']))
        matching = bool(current and all(current[k] == payload[k] for k in ('source', 'reference', 'snapshot_sha256'))
                        and any(v['fact_sha256'] == payload['fact_sha256'] for v in current['variants']))
        changes = []
        for attempt, completion in reversed(state['changes']):
            change = attempt['document']['payload']
            receipt = receipts.get((owner, attempt['approval_id']))
            changes.append(dict(approval_id=attempt['approval_id'], fingerprint=attempt['fingerprint'],
                operation=change['operation'], reason=change['reason'], decision=receipt['decision'] if receipt else 'unrecorded',
                recording_state='recorded' if completion else 'unrecorded', created_at=attempt['created_at']))
        item = dict(interpretation_id=proposal['approval_id'], interpretation_fingerprint=proposal['fingerprint'],
            **{k: original[k] for k in ('target_id', 'target_fingerprint', 'source', 'reference')},
            state_sha256=state['head'], state=state['state'], effective_payload=copy.deepcopy(payload),
            effective_review_id=state['review_id'], effective_review_fingerprint=state['review_fingerprint'],
            effective_change=next((copy.deepcopy(change) for change in changes
                if (change['approval_id'], change['fingerprint']) ==
                (state['review_id'], state['review_fingerprint'])), None),
            currency='matching_inputs' if matching else 'stale',
            interpretation_conflict=bool(state['state'] == 'active' and len(effective_groups.get(
                tuple(payload[k] for k in ('source', 'reference', 'snapshot_sha256')), ())) > 1),
            history_conflict=len(original_groups[tuple(original[k] for k in ('source', 'reference', 'snapshot_sha256'))]) > 1,
            total_changes=len(changes), changes_truncated=len(changes) > MAX_ITEMS,
            changes=changes[:MAX_ITEMS], completion_unconfirmed=any(completion is None for _, completion in state['changes']))
        associated = {(original['target_id'], original['target_fingerprint']), (proposal['approval_id'], proposal['fingerprint'])}
        associated.update((p['approval_id'], p['fingerprint']) for p, _ in state['changes'])
        for target in associated:
            related = next((c for c in changes if (c['approval_id'], c['fingerprint']) == target), None)
            contextual = item
            if related is not None and related not in item['changes']:
                # An old change receipt must retain inspectable proof of its
                # association even when the current root has many newer events.
                contextual = dict(item, changes=changes[:MAX_ITEMS - 1] + [related])
            result.setdefault(target, []).append(contextual)
    return {key: dict(schema_version=1, read_only=True, freshness='historical', total=len(items),
                      truncated=len(items) > MAX_ITEMS, items=copy.deepcopy(items[:MAX_ITEMS])) for key, items in result.items()}
