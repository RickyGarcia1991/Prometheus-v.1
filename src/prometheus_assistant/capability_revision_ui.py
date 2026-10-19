"""Live owner feedback closes a proposal; a replacement always needs a fresh review."""
import hashlib
import json
import re

from .application_policy import require_dispatch, revision
from .ollama import GenerationCancelled

KINDS = frozenset({'runtime_config', 'idle_config', 'integration', 'integration_access',
                   'remote_syntax', 'scheduled_syntax'})
PREPARE = {'runtime_config': 'runtime_config', 'idle_config': 'idle_config',
           'integration': 'integration', 'integration_access': 'integration_access',
           'remote_syntax_prepare': 'remote_syntax', 'scheduled_syntax_prepare': 'scheduled_syntax'}
FIELD = 'capability_revision_token'


def validate(body):
    if FIELD not in body:
        return
    if (body.get('mode') not in PREPARE or type(body[FIELD]) is not str
            or not re.fullmatch('[0-9a-f]{32}', body[FIELD])
            or type(body.get('session')) is not str
            or not re.fullmatch('[0-9a-f]{32}', body['session'])
            or 'task_grant' in body or 'revision_token' in body
            or (body['mode'] == 'idle_config' and body.get('enabled') is not True)):
        raise ValueError('A capability revision requires its explicit fresh review in the same conversation.')


def subject(kind, *parts):
    """Only handlers supply these canonical scope components, never HTTP overrides."""
    if kind not in KINDS or any(type(value) is not str or not value for value in parts):
        raise ValueError('Unknown capability revision scope.')
    return hashlib.sha256(json.dumps(['capability-subject-v1', kind, *parts],
        ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def review_options(app, request, *, owner, binding, kind, subject_hash):
    validate(request)
    token = request.get(FIELD, '')
    if token:
        if PREPARE.get(request.get('mode')) != kind:
            raise ValueError('The revision context belongs to a different capability.')
        app.action_approvals.validate_capability_revision_context(owner=owner, binding=binding,
            session=request.get('session'), kind=kind, subject_hash=subject_hash,
            capability_revision_token=token)
    return dict(capability_subject=subject_hash, capability_revision_token=token)


def without_context(request):
    return {key: value for key, value in request.items() if key != FIELD}


def execute(app, request, *, grant_owner=None, binding=None):
    if request.get('mode') != 'approval_decision' or request.get('approval_decision') != 'request_changes':
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock Prometheus before requesting changes.')
    require_dispatch('ui.approval_decision')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
        if not pending or pending['kind'] not in KINDS:
            return None
        if app.cancel_event.is_set():
            raise GenerationCancelled('Changes request cancelled.')
        # The central route runs before any adapter, queue, resource probe or setting write.
        # consume validates the exact owner/session/id/fingerprint and bounded feedback.
        row = app.action_approvals.consume(owner=owner, binding=binding, session=request.get('session'),
            approval_id=request['approval_id'], fingerprint=request['approval_fingerprint'],
            policy_revision=revision(), decision='request_changes', feedback=request.get('approval_feedback', ''))
        context = app.action_approvals.capability_revision_context(owner=owner, binding=binding,
            session=row['session'], kind=row['kind'], subject_hash=row['capability_subject'], parent_row=row)
        saved = app.save(without_context(request), {
            'reply': 'Changes requested. The proposal is closed and its action was not performed. '
                     'Adjust its settings or inputs and explicitly prepare a fresh review.',
            'approval_resolved': row['id']}, 'Changes requested', model='owner-reviewed-capability')
        # Live response only. Never write the one-use context to conversation history.
        return {**saved, 'action': 'approval_decision', 'details': {**saved['details'],
            'capability_revision': {**context, 'kind': row['kind'], 'session': row['session'],
                                    'feedback': request['approval_feedback'].strip()}}}
