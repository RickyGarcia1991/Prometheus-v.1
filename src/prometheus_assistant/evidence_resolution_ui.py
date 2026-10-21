"""Exact owner reviews of historical evidence interpretations, never execution."""
import re

from .application_policy import require_dispatch, revision
from .ollama import GenerationCancelled

MODES = {'evidence_resolution_prepare'}
FIELDS = {'resolution_target_id', 'resolution_target_fingerprint',
          'resolution_snapshot_sha256', 'resolution_fact_sha256', 'resolution_reason'}
KIND = 'evidence_resolution'


def validate(body):
    if body.get('mode') not in MODES:
        if FIELDS & body.keys():
            raise ValueError('Resolution fields belong to an evidence interpretation review.')
        return
    if set(body) - (FIELDS | {'mode', 'prompt', 'session'}) or not FIELDS <= body.keys():
        raise ValueError('Use exactly the selected conflict and interpretation fields.')
    for key in FIELDS - {'resolution_reason'}:
        length = 32 if key == 'resolution_target_id' else 64
        if type(body[key]) is not str or not re.fullmatch('[0-9a-f]{'+str(length)+'}', body[key]):
            raise ValueError('Select the exact evidence from saved history.')
    reason = body['resolution_reason']
    if type(reason) is not str or not reason.strip() or len(reason) > 2000 or '\x00' in reason:
        raise ValueError('Explain the interpretation in 1 to 2000 characters.')
    if type(body.get('prompt', '')) is not str or body.get('prompt', '').strip():
        raise ValueError('Use the dedicated interpretation reason.')


def execute(app, request, *, grant_owner=None, binding=None):
    mode = request['mode']
    if mode not in MODES and mode != 'approval_decision':
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your owner passkey to review evidence interpretations.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
        if mode == 'approval_decision' and (not pending or pending['kind'] != KIND):
            return None
        require_dispatch('ui.evidence_resolution_prepare')
        if app.cancel_event.is_set():
            raise GenerationCancelled('Evidence interpretation cancelled.')
        if mode == 'approval_decision':
            require_dispatch('ui.approval_decision')
            result = app.action_approvals.decide_evidence_resolution(owner=owner, binding=binding,
                session=request.get('session'), approval_id=request['approval_id'],
                fingerprint=request['approval_fingerprint'], decision=request['approval_decision'],
                policy_revision=revision())
            data = {'reply': ('Interpretation recorded.' if request['approval_decision'] == 'approve'
                             else 'Interpretation rejected.') +
                            ' Original evidence and failed checks remain visible. No work was executed.',
                    'approval_resolved': result['approval']['id'],
                    'evidence_resolution': result['resolution']}
            return {**app.save(request, data, 'Evidence interpretation', model='owner-reviewed-evidence'),
                    'action': mode}
        validate(request)
        if pending:
            raise ValueError('Approve or deny the pending proposal before preparing another review.')
        # A conversation provides the review session. No pending authority is saved to it.
        saved = app.save(request, {'reply': 'Preparing an interpretation of the exact saved evidence.'},
                         'Evidence interpretation', model='owner-reviewed-evidence')
        row = app.action_approvals.prepare_evidence_resolution(owner=owner, binding=binding,
            session=saved['session'], target_id=request['resolution_target_id'],
            target_fingerprint=request['resolution_target_fingerprint'],
            snapshot_sha256=request['resolution_snapshot_sha256'],
            fact_sha256=request['resolution_fact_sha256'], reason=request['resolution_reason'],
            policy_revision=revision())
        return {**saved, 'action': mode, 'reply': 'Review this exact evidence interpretation before approving or denying it.',
                'details': {**saved['details'], 'approval': row}}
