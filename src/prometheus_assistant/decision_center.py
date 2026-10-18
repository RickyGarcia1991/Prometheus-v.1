"""Bounded read-only attention projection; never authorizes or probes anything.

Call only after owner/session authorization. Inputs are a pending approval (None
means no proposal), public_job records, activity records and resource status.
Missing list/resource inputs mean unavailable. Approval {'unavailable': True}
marks a failed read. All output text and navigation are fixed allowlisted values;
no prompt, source, file, tool output, reason text or identifier is copied.
"""
import math

MAX_JOBS = 100
MAX_ACTIVITY = 50
MAX_DECISIONS = 50
NAVIGATION = frozenset({'project', 'scheduled', 'remote', 'activity', 'self_improvement', 'integrations', 'research', 'history'})
_APPROVAL_ROUTES = {'project_edit': 'project', 'project_task': 'project',
    'scheduled_syntax': 'scheduled', 'remote_syntax': 'remote',
    'self_improve': 'self_improvement', 'integration': 'integrations',
    'integration_access': 'integrations', 'idle_config': 'research'}
_MODES = {'Balanced', 'Performance', 'Idle', 'Battery Saver', 'Critical Recovery'}
_SEVERITY = {'critical': 0, 'warning': 1, 'info': 2}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253402300799


def _source(rows, limit):
    if not isinstance(rows, list):
        return [], {'state': 'unavailable', 'freshness': 'unavailable', 'truncated': False, 'omitted': 0}
    selected = rows[:limit]
    valid = [row for row in selected if isinstance(row, dict)]
    return valid, {'state': 'available' if len(valid) == len(selected) else 'partial',
        'freshness': 'saved_records', 'truncated': len(rows) > limit, 'omitted': max(0, len(rows)-limit)}


def _freshness(resources, now):
    # Never interpret monotonic sample times or an age alone as an epoch date.
    observed, expires = resources.get('observed_at'), resources.get('expires_at')
    if not (_number(now) and _number(observed) and _number(expires) and observed <= expires):
        return 'unknown'
    if observed > now:
        return 'unknown'
    return 'current' if now < expires else 'stale'


def _decision_state(row):
    """Validate saved evidence without interpreting payloads or performing I/O."""
    from .decision_outcomes import validate as project_validate, sequence
    from .capability_outcomes import validate as capability_validate, _sequence, _kind
    decision = row.get('decision')
    kind = row.get('kind')
    if decision not in {'approve', 'reject', 'request_changes'} or not isinstance(kind, str):
        raise ValueError('Invalid decision')
    project, capability = row.get('outcomes', []), row.get('capability_outcomes', [])
    if any(not isinstance(events, list) or len(events)>16 for events in (project, capability)) or project and capability:
        raise ValueError('Invalid evidence')
    if decision != 'approve':
        if project or capability:raise ValueError('Unapproved evidence')
        return 'no_execution'
    prior=[];identities=set()
    for event in project or capability:
        if project:
            if kind not in {'project_edit','project_task'}:raise ValueError('Invalid project kind')
            project_validate(event);sequence(prior,event)
        else:
            capability_validate(event);_kind(kind,event);_sequence(prior,event)
        if event['event_id'] in identities:raise ValueError('Repeated event')
        identities.add(event['event_id']);prior.append(event)
    if not prior:return 'unknown'
    last=prior[-1];status=last['status']
    if project:
        if last['phase']=='undo_result' and status=='restored':return 'restored'
        if last['phase']=='undo_started' or status=='restore_failed' or last['rollback']=='needs_attention':return 'restoration_unknown'
        if status in {'started','unknown'}:return 'unknown'
        if status=='failed':return 'historical_failure'
        if status=='passed':return 'verified'
        return 'unverified'
    if status in {'started','unknown','unconfirmed'}:return 'unknown'
    if status in {'checks_failed','tool_failed'}:return 'historical_failure'
    if status=='checks_passed':return 'verified'
    if status in {'cancelled','expired','deferred'}:return 'no_execution'
    if status=='waiting':return 'waiting'
    return 'recorded_effect'


def build_snapshot(approval=None, jobs=None, activity=None, resources=None, now=None, decisions=None):
    """Return schema-v1 grouped fixed-text summaries with navigation only.

    Sources distinguish current saved records from live telemetry. Approval
    expires_at uses a monotonic clock and is deliberately not compared to now.
    Resource freshness requires explicit top-level epoch observed_at/expires_at.
    Queue expiry is projected from wall time without writing WaitingStore.
    Counts count represented records, not buttons or execution priority.
    """
    now = now if _number(now) else None
    groups = {}
    def add(source, kind, label, severity, state, navigation, freshness='saved_records'):
        key = (source, kind, state, navigation, severity, freshness)
        if key in groups:
            groups[key]['count'] += 1
        else:
            groups[key] = dict(id='.'.join((source,kind,state,navigation)), kind=kind,
                label=label,severity=severity,state=state,count=1,source=source,
                freshness=freshness,navigation=navigation)
    queue, queue_source = _source(jobs, MAX_JOBS)
    recent, activity_source = _source(activity, MAX_ACTIVITY)
    approval_valid = approval is None or isinstance(approval, dict) and approval.get('unavailable') is not True and isinstance(approval.get('kind'), str)
    sources = dict(approvals=dict(state='available' if approval_valid else 'unavailable',
        freshness='saved_records' if approval_valid else 'unavailable',truncated=False,omitted=0),
        queue=queue_source,activity=activity_source)
    records, decision_source = _source(decisions, MAX_DECISIONS)
    # A full bounded history page may omit older records; do not imply completeness.
    if isinstance(decisions,list) and len(decisions)>=MAX_DECISIONS:
        decision_source['truncated']=True
    sources['decisions']=decision_source
    labels={
        'unknown':('Recorded decision has no confirmed outcome; history cannot resume work.','warning'),
        'restoration_unknown':('Recorded restoration is unconfirmed or needs attention; inspect its history.','warning'),
        'historical_failure':('Historical verification or execution reported failure; inspect recorded evidence.','warning'),
        'restored':('Recorded manual restoration completed; original execution evidence remains in history.','info'),
        'verified':('Recorded native checks passed their stated scope.','info'),
        'unverified':('Recorded action has no behavioral pass evidence.','info'),
        'no_execution':('Recorded decision or disposition does not confirm execution.','info'),
        'waiting':('Recorded approval entered waiting; consult the current queue for its status.','info'),
        'recorded_effect':('Recorded configuration, access or adapter observation; later behavior is not verified.','info')}
    for row in records:
        try:state=_decision_state(row)
        except (ValueError,TypeError,KeyError,OverflowError):
            state='unknown';decision_source['state']='partial'
        label,severity=labels[state]
        add('decisions','historical_evidence',label,severity,state,'history')
    if approval_valid and approval is not None:
        route = _APPROVAL_ROUTES.get(approval['kind'], 'activity')
        add('approvals','proposal','An exact proposal is waiting for your decision.','info','pending',route)
    for row in queue:
        state = row.get('state')
        result = row.get('result') if isinstance(row.get('result'), dict) else {}
        evidence = result.get('evidence') if isinstance(result.get('evidence'), dict) else {}
        if state in ('unconfirmed', 'claimed'):
            add('queue','outcome','Outcome unconfirmed; recover the original result only. Never rerun it.','critical','unconfirmed','scheduled')
        elif state == 'failed' or evidence.get('status') == 'failed':
            add('queue','evidence','Saved verification evidence reports a failure.','warning','failed','scheduled')
        elif state == 'waiting':
            deadline = row.get('expires_at')
            expired = now is not None and _number(deadline) and deadline <= now
            if expired:
                add('queue','waiting','Waiting permission has expired; a new review is needed.','warning','expired','scheduled')
            else:
                add('queue','waiting','Approved work is waiting for its exact destination.','info','waiting','scheduled')
        elif state == 'expired':
            add('queue','waiting','Waiting permission has expired; a new review is needed.','warning','expired','scheduled')
        elif state == 'completed':
            if evidence.get('status') == 'passed':
                add('queue','evidence','Saved verification evidence passed its stated checks.','info','passed','scheduled')
            else:
                add('queue','evidence','Work completed; verification evidence is incomplete or unavailable.','warning','unknown','scheduled')
        elif state == 'cancelled':
            add('queue','waiting','Waiting work was cancelled.','info','cancelled','scheduled')
        else:
            add('queue','outcome','Saved job state is unavailable.','warning','unknown','scheduled')
    for row in recent:
        state = row.get('state', row.get('status'))
        if state in ('failed', 'error'):
            add('activity','task','Recent activity reports a failed task.','warning','failed','activity')
        elif state == 'review':
            add('activity','task','Recent activity prepared a review; consult the current proposal status.','info','review','activity')
        elif state in ('running', 'working', 'queued'):
            add('activity','task','Recent activity includes work in progress.','info','active','activity')
        elif state in ('done', 'completed', 'success'):
            add('activity','task','Recent activity includes completed work.','info','completed','activity')
        elif state in ('cancelled', 'stopped'):
            add('activity','task','Recent activity includes stopped work.','info','cancelled','activity')
        else:
            add('activity','task','Recent activity status is unavailable.','info','unknown','activity')
    resource_mode = resources.get('mode') if isinstance(resources, dict) else None
    if isinstance(resource_mode, dict):
        resource_mode = resource_mode.get('effective')
    valid_mode = isinstance(resource_mode, str) and resource_mode in _MODES
    freshness = _freshness(resources, now) if isinstance(resources, dict) else 'unavailable'
    sources['resources'] = dict(state='available' if valid_mode else 'unavailable',
        freshness=freshness if valid_mode else 'unavailable',truncated=False,omitted=0)
    if valid_mode:
        severity = 'critical' if resource_mode == 'Critical Recovery' else 'info'
        if freshness == 'stale':
            add('resources','health','Resource observations are stale; refresh the existing health view.','warning','stale','activity',freshness)
        add('resources','mode','Reported resource mode: '+resource_mode+'.',severity,
            resource_mode.lower().replace(' ', '_'),'activity',freshness)
    for name, source in sources.items():
        if source['state'] in ('unavailable', 'partial'):
            add(name,'availability','This attention source is unavailable or incomplete.','warning',source['state'],
                'scheduled' if name == 'queue' else 'history' if name == 'decisions' else 'activity',source['freshness'])
        if source['truncated']:
            add(name,'limit','This summary is truncated; open the source view for its retained records.','info','truncated',
                'scheduled' if name == 'queue' else 'history' if name == 'decisions' else 'activity')
    items = sorted(groups.values(), key=lambda row: (_SEVERITY[row['severity']],row['source'],row['kind'],row['state'],row['navigation']))
    return dict(schema_version=1,generated_at=now,items=items,sources=sources,
        counts={level:sum(row['count'] for row in items if row['severity']==level) for level in _SEVERITY},read_only=True)
