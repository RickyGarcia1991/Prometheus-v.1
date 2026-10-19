"""Bounded historical evidence projection. No I/O, adoption or execution authority."""
import hashlib
import json

from .decision_history import MAX_RECEIPTS, _event, _identity
from .decision_outcomes import MAX_EVENTS as PROJECT_MAX, validate as project_validate, sequence
from .capability_outcomes import MAX_EVENTS as CAPABILITY_MAX, validate as capability_validate, _sequence, _kind

MAX_RELATED = 16
MAX_DECISIONS = 2 * MAX_RECEIPTS
PROJECT_KINDS = frozenset({'project_edit', 'project_task'})
WORKER_KINDS = frozenset({'remote_syntax', 'scheduled_syntax'})
CAPABILITY_KINDS = frozenset({'runtime_config', 'idle_config', 'integration', 'integration_access'})
PROJECT_RESULTS = frozenset({'passed', 'failed', 'unchanged', 'applied_without_checks', 'cancelled'})
WORKER_RESULTS = frozenset({'checks_passed', 'checks_failed', 'tool_failed'})
_FIELDS = {'schema_version', 'state', 'source', 'freshness', 'reference', 'reason',
           'linked_decisions', 'related_decisions', 'truncated', 'read_only'}
_REASONS = {
    'not_applicable': {'decision_not_approved'},
    'missing': {'no_recorded_start'},
    'pending': {'execution_pending'},
    'unconfirmed': {'outcome_unconfirmed', 'restoration_pending', 'restoration_unconfirmed'},
    'recorded': {'native_result_recorded'},
    'restored': {'project_restored'},
    'conflicting': {'native_identity_conflict'},
}


def validate_projection(value):
    """Validate the public projection without consulting any live state."""
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError('Invalid evidence lifecycle schema.')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['freshness'] != 'historical' or value['read_only'] is not True):
        raise ValueError('Evidence lifecycle is historical and read-only.')
    state, source, reason = value['state'], value['source'], value['reason']
    if (type(state) is not str or state not in _REASONS or type(reason) is not str
            or reason not in _REASONS[state] or type(source) is not str
            or source not in {'none', 'project', 'worker', 'capability'}):
        raise ValueError('Invalid evidence lifecycle state.')
    if (state == 'not_applicable' and source != 'none'
            or source == 'none' and state not in {'not_applicable', 'missing'}
            or reason in {'restoration_pending', 'restoration_unconfirmed', 'project_restored'} and source != 'project'
            or state == 'conflicting' and source not in {'project', 'worker'}):
        raise ValueError('Evidence lifecycle source differs.')
    count, related, reference = value['linked_decisions'], value['related_decisions'], value['reference']
    if (type(count) is not int or not 0 <= count <= MAX_DECISIONS
            or type(related) is not list or len(related) != min(max(count - 1, 0), MAX_RELATED)
            or type(value['truncated']) is not bool or value['truncated'] != (count > MAX_RELATED + 1)):
        raise ValueError('Invalid bounded evidence references.')
    if reference is None:
        if count or state in {'restored', 'conflicting'}:
            raise ValueError('Native identity is missing.')
    else:
        _identity(reference, 64)
        if count < 1 or source not in {'project', 'worker'} or state in {'missing', 'not_applicable'}:
            raise ValueError('Unexpected native identity.')
    if state == 'conflicting' and count < 2:
        raise ValueError('Conflict requires distinct linked decisions.')
    ids = set()
    for row in related:
        if type(row) is not dict or set(row) != {'id', 'fingerprint'}:
            raise ValueError('Invalid related decision.')
        _identity(row['id'], 32); _identity(row['fingerprint'], 64)
        if row['id'] in ids:
            raise ValueError('Repeated related decision.')
        ids.add(row['id'])
    return value


def _events(owner, rows, *, project):
    """Validate owned sequences first; a malformed sequence is not a conflict."""
    if type(rows) is not list or len(rows) > (PROJECT_MAX if project else CAPABILITY_MAX):
        raise ValueError('Invalid bounded native evidence collection.')
    grouped, fingerprints, event_ids = {}, {}, set()
    for item in rows:
        if type(item) not in (tuple, list) or len(item) != 4:
            raise ValueError('Invalid native evidence link.')
        actual_owner, ident, fingerprint, event = item
        if actual_owner != owner:
            continue
        _identity(ident, 32); _identity(fingerprint, 64)
        if ident in fingerprints and fingerprints[ident] != fingerprint:
            raise ValueError('Native decision fingerprint differs.')
        fingerprints[ident] = fingerprint
        (project_validate if project else capability_validate)(event)
        if event['event_id'] in event_ids:
            raise ValueError('Repeated native evidence event.')
        event_ids.add(event['event_id'])
        prior = grouped.setdefault((ident, fingerprint), [])
        (sequence if project else _sequence)(prior, event)
        prior.append(event)
    return grouped


def _native_groups(owner, projects, capabilities):
    """Only execution identities join facts; namespaces never intersect."""
    groups, memberships = {}, {}
    for project, streams in ((True, projects), (False, capabilities)):
        for decision, events in streams.items():
            if project:
                result = next((e for e in events if e['phase'] == 'result'), None)
                if result is None or not result['edit_id'] or result['status'] not in PROJECT_RESULTS:
                    continue
                binding = ('project', result['project_hash'], result['edit_id'])
                facts = {key: result[key] for key in ('status', 'checks', 'rollback', 'evidence_sha256')}
            else:
                if not events[0]['job_id']:
                    continue
                binding = ('worker', events[0]['job_id'], events[0]['request_digest'])
                result = events[-1] if events[-1]['status'] in WORKER_RESULTS else None
                facts = {key: result[key] for key in ('status', 'checks', 'evidence_sha256')} if result else None
            group = groups.setdefault(binding, {'members': set(), 'results': {}, 'facts': set()})
            group['members'].add(decision); memberships[decision] = binding
            if result is not None:
                text = json.dumps(facts, sort_keys=True, separators=(',', ':'), allow_nan=False)
                group['facts'].add(text); group['results'][decision] = result
    for binding, group in groups.items():
        group['ordered'] = sorted(group['members'])
        group['conflicting'] = len(group['facts']) > 1
        # Keep the established worker reference for an unchanged consistent result.
        if binding[0] == 'worker' and len(group['facts']) == 1:
            identity = ['owner-worker-evidence-v1', owner, *binding[1:], next(iter(group['facts']))]
        else:
            identity = ['owner-native-evidence-v1', owner, *binding]
        group['reference'] = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
    return groups, memberships


def _base(row, events, source):
    if row['decision'] != 'approve':
        return 'not_applicable', 'none', 'decision_not_approved'
    if not events:
        return 'missing', source, 'no_recorded_start'
    last = events[-1]
    if source == 'project':
        if last['phase'] == 'undo_result' and last['status'] == 'restored':
            return 'restored', source, 'project_restored'
        if last['phase'] == 'undo_started':
            return 'unconfirmed', source, 'restoration_pending'
        if last['status'] == 'restore_failed' or last['rollback'] == 'needs_attention':
            return 'unconfirmed', source, 'restoration_unconfirmed'
    if last['status'] in {'started', 'waiting', 'deferred'}:
        return 'pending', source, 'execution_pending'
    if last['status'] in {'unknown', 'unconfirmed'}:
        return 'unconfirmed', source, 'outcome_unconfirmed'
    return 'recorded', source, 'native_result_recorded'


def for_owner(owner, *, decisions, project_events, capability_events):
    """Project complete saved receipts/events, before any history-page truncation."""
    _identity(owner, 64)
    if type(decisions) is not list or len(decisions) > MAX_DECISIONS:
        raise ValueError('Invalid bounded decision collection.')
    owned, identifiers = {}, set()
    for row in decisions:
        if type(row) is not dict:
            raise ValueError('Invalid decision receipt.')
        if row.get('owner') != owner:
            continue
        _event(row)
        if row['id'] in identifiers:
            raise ValueError('Repeated owner decision.')
        identifiers.add(row['id']); owned[(row['id'], row['fingerprint'])] = row
    projects = _events(owner, project_events, project=True)
    capabilities = _events(owner, capability_events, project=False)
    if projects.keys() & capabilities.keys():
        raise ValueError('A decision cannot mix native evidence namespaces.')
    for project, streams in ((True, projects), (False, capabilities)):
        for decision, events in streams.items():
            row = owned.get(decision)
            if row is None or row['decision'] != 'approve':
                raise ValueError('Native evidence lacks an exact approved receipt.')
            if project:
                if row['kind'] not in PROJECT_KINDS:
                    raise ValueError('Project evidence kind differs.')
            else:
                for event in events:
                    _kind(row['kind'], event)
    groups, memberships = _native_groups(owner, projects, capabilities)
    result = {}
    for decision, row in owned.items():
        source = ('project' if row['kind'] in PROJECT_KINDS else 'worker' if row['kind'] in WORKER_KINDS
                  else 'capability' if row['kind'] in CAPABILITY_KINDS else 'none')
        events = projects.get(decision, capabilities.get(decision, []))
        state, source, reason = _base(row, events, source)
        group = groups.get(memberships.get(decision))
        related, count, reference = [], 0, None
        if group is not None:
            members = group['ordered']; count = len(members); reference = group['reference']
            related = [dict(id=i, fingerprint=f) for i, f in members[:MAX_RELATED + 1] if (i, f) != decision][:MAX_RELATED]
            if group['conflicting']:
                state, reason = 'conflicting', 'native_identity_conflict'
        projection = dict(schema_version=1, state=state, source=source, freshness='historical',
                          reference=reference, reason=reason, linked_decisions=count,
                          related_decisions=related, truncated=count > MAX_RELATED + 1, read_only=True)
        result[decision] = validate_projection(projection)
    return result
