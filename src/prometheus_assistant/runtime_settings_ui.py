"""Exact owner review for host-local runtime launch preferences."""
import re

from .action_approvals import decision_text
from .application_policy import require_dispatch, revision
from .ollama import GenerationCancelled

MODES = {'runtime_status', 'runtime_config'}
FIELDS = {'runtime_backend'}
KIND = 'runtime_config'


def validate(body):
    if body.get('mode') == 'runtime_config':
        if body.get('runtime_backend') not in ('automatic', 'cpu'):
            raise ValueError('Choose Automatic or CPU for new runtime launches.')
    elif 'runtime_backend' in body:
        raise ValueError('Runtime backend belongs to runtime configuration.')


def from_chat(text):
    match = re.fullmatch(r'(?:please\s+)?set\s+(?:the\s+)?runtime\s+backend\s+to\s+(cpu|automatic)[.!]?',
                         text.strip(), re.I)
    return match[1].lower() if match else None


def preferences(app):
    with app.lock:
        if not hasattr(app, '_runtime_preferences'):
            from .runtime_preferences import Preferences
            app._runtime_preferences = Preferences()
        return app._runtime_preferences


def status(app):
    return {**preferences(app).snapshot(), 'scope': 'This account on this computer',
            'applies_to': 'Future runtime launches; an existing server is kept running',
            'running_backend': 'Not established by the saved preference'}


def _result(app, request, data):
    return {**app.save(request, data, 'Runtime startup settings', model='owner-reviewed-setting'),
            'action': 'runtime_config'}


def execute(app, request, *, grant_owner=None, binding=None):
    mode = request['mode']
    selected = from_chat(request['prompt']) if mode == 'chat' else None
    decision = decision_text(request['prompt']) if mode == 'chat' else request.get('approval_decision')
    possible = bool(decision and app.action_approvals.has_binding(binding))
    if mode not in MODES and selected is None and not possible:
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your owner passkey to change runtime settings.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
    if possible and pending and pending['kind'] != KIND:
        return None
    if possible and pending:
        require_dispatch('ui.approval_decision')
        if app.cancel_event.is_set():
            raise GenerationCancelled('Runtime setting change cancelled.')
        with grant_owner() as current:
            if current != owner:
                raise ValueError('Owner changed before runtime configuration.')
            row = app.action_approvals.consume(owner=owner, binding=binding, session=request.get('session'),
                approval_id=pending['id'] if mode == 'chat' else request['approval_id'],
                fingerprint=pending['fingerprint'] if mode == 'chat' else request['approval_fingerprint'],
                policy_revision=revision(), decision=decision)
            if decision == 'reject':
                return _result(app, request, {'reply': 'Denied. Runtime startup settings are unchanged.',
                    'approval_resolved': row['id'], 'runtime_settings': status(app)})
            require_dispatch('ui.runtime_config')
            payload = row['payload']
            preferences(app).configure(payload['backend'], payload['expected_revision'])
        return _result(app, request, {'reply': 'Runtime preference saved for future launches on this computer. '
            'An existing server keeps its current backend. Automatic restores the launcher defaults after another review.',
            'approval_resolved': row['id'], 'runtime_settings': status(app)})
    if mode == 'approval_decision':
        return None
    if mode == 'runtime_status':
        require_dispatch('ui.runtime_status')
        return {'reply': 'Saved runtime startup preference for this computer.',
                'details': {'runtime_settings': status(app)}, 'action': mode, 'label': 'Runtime settings'}
    if pending:
        raise ValueError('Approve or deny the pending proposal before changing runtime settings.')
    require_dispatch('ui.runtime_config')
    backend = selected or request['runtime_backend']
    current = preferences(app).snapshot()
    cpu = backend == 'cpu'
    review = {'action': 'Use CPU for future runtime launches' if cpu else 'Use automatic runtime startup',
        'reason': 'Use the startup backend you selected on this computer.',
        'benefit': ('Avoid GPU library discovery by explicitly selecting the CPU library. '
                    'This may restore startup when GPU discovery stalls.') if cpu else
                   'Let the runtime choose its backend using the launcher environment.',
        'effects': ('CPU inference can be slower than a working GPU. ' if cpu else '') +
                   'Save a preference for this Windows account on this computer. It applies only to new '
                   'runtime launches; the current server continues unchanged. No driver or system setting is changed.',
        'duration': 'Until you approve a different startup preference on this computer',
        'current_backend': current['backend'], 'proposed_backend': backend,
        'resources': {'internet': False, 'runtime_restart': False, 'model_download': False}}
    data = {'reply': review['action'] + '. ' + review['benefit'] + ' ' + review['effects'] +
                    ' Review this change and approve or deny it.', 'runtime_review': review}
    result = _result(app, {**request, 'mode': 'runtime_config'}, data)
    result['details']['approval'] = app.action_approvals.review(owner=owner, binding=binding,
        session=result['session'], kind=KIND,
        payload={'backend': backend, 'expected_revision': current['revision']},
        review=review, policy_revision=revision())
    return result
