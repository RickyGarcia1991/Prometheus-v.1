"""Authenticated, deterministic chat and interface approval for tested edits."""
import re

from .action_approvals import decision_text
from .application_policy import require_dispatch, revision
from .ollama import GenerationCancelled

MODES = {'self_improve', 'approval_decision'}


def goal_from_chat(text):
    match = re.fullmatch(r'(?:please\s+)?(?:improve|update|fix)\s+(?:prometheus|yourself)\s*(?::|,|by\b|to\b)\s*(.+)',
                        text.strip(), flags=re.I | re.S)
    return match.group(1).strip() if match else None


def _engine(app):
    from .self_improvement_runtime import get_self_improvement
    return get_self_improvement(app)


def activation_result(app, *, owner, job_id=None):
    row = (app.action_approvals.approved_job(owner=owner, job_id=job_id) if job_id is not None
           else app.action_approvals.latest_approved(owner=owner))
    if row is None:
        return {'state': 'none', 'summary': 'No approved application change is recorded for this owner.'}
    try:
        result = _engine(app).promoter.result(row['job_id'])
    except Exception:
        # Record only uncertainty for an already linked attempt. Reading a
        # malformed/missing native status never creates a legacy decision link.
        if app.action_approvals.decision_history is not None:
            app.action_approvals.application_outcomes().unconfirmed(owner=owner, job_id=row['job_id'],
                candidate_fingerprint=row['candidate_fingerprint'])
        raise
    if result.get('state') == 'active' and result.get('approval_fingerprint') != row['candidate_fingerprint']:
        raise ValueError('The activation result does not match this owner-approved candidate.')
    if app.action_approvals.decision_history is not None:
        app.action_approvals.application_outcomes().observe_native(owner=owner, job_id=row['job_id'],
            candidate_fingerprint=row['candidate_fingerprint'], result=result)
    if result.get('state') == 'promotion_pending':
        if row.get('dispatch_state') == 'failed':
            result = {'state': 'failed', 'error': row.get('dispatch_error', 'The activation helper was not confirmed.')}
        elif row.get('dispatch_state') == 'dispatching':
            result = {'state': 'unconfirmed', 'error': 'Dispatch was interrupted before activation could be confirmed.'}
    return {**result, 'job_id': row['job_id']}


def _result(app, request, data, label):
    return {**app.save(request, data, label, model='owner-review'), 'action': 'self_improve'}


def _review(receipt):
    checks = receipt['checks']
    tests = sum(row.get('tests_run', 0) for row in checks)
    return {'action': 'Install the tested change to ' + receipt['target'],
            'reason': receipt['reason'], 'target': receipt['target'], 'diff': receipt['diff'],
            'checks': checks,
            'effects': 'Build and sign a new local application release, switch the active release, and restart Prometheus. '
                       'The previous release is retained for recovery. Conversations and private data are not edited.',
            'check_summary': f'{len(checks)} required check groups passed; {tests} tests reported.'}


def execute(app, request, *, grant_owner=None, binding=None):
    """Return None for ordinary chat. Authority never comes from request fields."""
    mode = request['mode']
    goal = request['prompt'] if mode == 'self_improve' else goal_from_chat(request['prompt']) if mode == 'chat' else None
    decision = decision_text(request['prompt']) if mode == 'chat' else request.get('approval_decision')
    pending_chat = mode == 'chat' and decision and app.action_approvals.has_binding(binding)
    if mode not in MODES and not goal and not pending_chat:
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your enrolled owner passkey before reviewing application changes.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
    if pending and pending['kind'] != 'self_improve' and decision:
        return None
    if goal:
        require_dispatch('ui.self_improve')
        if pending:
            raise ValueError('Approve or reject your pending proposal before preparing another.')
        app.progress('Preparing and testing a candidate application change')
        receipt = _engine(app).propose(goal, request.get('target') or None, cancel_event=app.cancel_event)
        if app.cancel_event.is_set() or receipt.get('state') == 'cancelled':
            raise GenerationCancelled('Self-improvement cancelled. No change was installed.')
        if receipt.get('state') != 'ready_for_approval':
            return _result(app, request, {'reply': 'No change was installed. The candidate did not qualify for approval. '
                + receipt.get('error', 'Review the test result.'), 'self_improvement': receipt}, 'Change not qualified')
        review = _review(receipt)
        reply = (f"A tested change is ready for your approval.\n\nAction: {review['action']}\nWhy: {review['reason']}\n"
                 f"Effects: {review['effects']}\nChecks: {review['check_summary']}\n\n"
                 'Review the exact diff in Maintenance. Say or type “yes, approve” to install this change, '
                 'or “reject” to leave it unapplied. This approval expires in ten minutes.')
        # A generated candidate is untrusted data. Only the current owner can open
        # its exact approval, and the engine rechecks bytes/tests before promotion.
        with grant_owner() as current_owner:
            if current_owner != owner:
                raise ValueError('The owner changed while preparing the proposal.')
            if app.cancel_event.is_set():
                raise GenerationCancelled('Self-improvement cancelled. No change was installed.')
            result = _result(app, request, {'reply': reply, 'self_improvement': review}, 'Tested change · needs your approval')
            pending = app.action_approvals.review(owner=owner, binding=binding, session=result['session'],
                kind='self_improve', payload={'job_id': receipt['job_id'], 'approval_fingerprint': receipt['approval_fingerprint']},
                review=review, policy_revision=revision())
            result['details']['approval'] = pending
            return result
    require_dispatch('ui.approval_decision')
    if not pending:
        raise ValueError('There is no pending approval in this owner session.')
    if request.get('session') != pending['session']:
        raise ValueError('Return to the conversation containing the proposal before approving or rejecting it.')
    if mode == 'chat':
        approval_id, fingerprint = pending['id'], pending['fingerprint']
    else:
        approval_id, fingerprint = request['approval_id'], request['approval_fingerprint']
    with grant_owner() as current_owner:
        if current_owner != owner:
            raise ValueError('The owner changed before approval.')
        if app.cancel_event.is_set():
            raise GenerationCancelled('Approval cancelled before dispatch.')
        row = app.action_approvals.consume(owner=owner, binding=binding, session=request['session'],
            approval_id=approval_id, fingerprint=fingerprint, policy_revision=revision(), decision=decision)
        if decision == 'reject':
            return _result(app, request, {'reply': 'The proposal was rejected. No application change was installed.',
                'approval_resolved': row['id']}, 'Proposal rejected')
        require_dispatch('ui.self_improve')
        app.progress('Rechecking the approved candidate before signed activation')
        app.action_approvals.record_approved(owner=owner, job_id=row['payload']['job_id'],
            candidate_fingerprint=row['payload']['approval_fingerprint'])
        app.action_approvals.start_application_outcome(owner=owner, row=row)
        try:
            result = _engine(app).promote(row['payload']['job_id'], row['payload']['approval_fingerprint'], cancel_event=app.cancel_event)
        except Exception as error:
            app.action_approvals.record_dispatch(owner=owner, job_id=row['payload']['job_id'], state='failed', error=error)
            app.action_approvals.application_outcomes().unconfirmed(owner=owner, job_id=row['payload']['job_id'],
                candidate_fingerprint=row['payload']['approval_fingerprint'])
            raise
        app.action_approvals.record_dispatch(owner=owner, job_id=row['payload']['job_id'],
            state='promotion_pending' if result.get('state') == 'promotion_pending' else 'failed', error=result.get('error', ''))
        # The synchronous return only confirms queuing. A failed return or an
        # exception cannot prove that the detached process did not start.
        observation = (result if result.get('state') == 'promotion_pending'
            and result.get('job_id') == row['payload']['job_id']
            and result.get('approval_fingerprint') == row['payload']['approval_fingerprint']
            else {'job_id':row['payload']['job_id'], 'state':'unconfirmed'})
        app.action_approvals.application_outcomes().observe_native(owner=owner, job_id=row['payload']['job_id'],
            candidate_fingerprint=row['payload']['approval_fingerprint'], result=observation)
    queued = result.get('state') == 'promotion_pending'
    reply = ('Approved. The signed activation helper has queued this exact tested change. Prometheus will restart when it is ready. '
             'Activation is not complete yet.' if queued else 'The approved change was not activated. ' + result.get('error', 'Review the activation result.'))
    return _result(app, request, {'reply': reply, 'self_improvement': result, 'approval_resolved': row['id']},
                   'Approved change · activation queued' if queued else 'Activation needs attention')
