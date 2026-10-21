"""Fresh owner reviews for historical interpretation changes, never execution."""
import re
from .application_policy import require_dispatch, revision
from .evidence_resolution import reason_text
from .ollama import GenerationCancelled

MODES={'interpretation_change_prepare'}
BASE_FIELDS={'interpretation_id','interpretation_fingerprint','interpretation_state_sha256',
             'interpretation_operation','interpretation_reason'}
REPLACEMENT_FIELDS={'interpretation_snapshot_sha256','interpretation_fact_sha256'}
FIELDS=BASE_FIELDS|REPLACEMENT_FIELDS
KIND='evidence_interpretation_change'


def validate(body):
    if body.get('mode') not in MODES:
        if FIELDS & body.keys():raise ValueError('Interpretation lifecycle fields require their dedicated review.')
        return
    operation=body.get('interpretation_operation')
    if type(operation) is not str or operation not in {'revise','withdraw'}:
        raise ValueError('Choose revise or withdraw for a saved interpretation.')
    fields=BASE_FIELDS|(REPLACEMENT_FIELDS if operation=='revise' else set())
    if set(body)-(fields|{'mode','prompt','session'}) or not fields<=body.keys():
        raise ValueError('Use exactly the selected interpretation and change fields.')
    for key in fields-{'interpretation_operation','interpretation_reason'}:
        size=32 if key=='interpretation_id' else 64
        if type(body[key]) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}',body[key]):
            raise ValueError('Select the exact saved interpretation and current state.')
    reason_text(body['interpretation_reason'])
    if type(body.get('prompt','')) is not str or body.get('prompt','').strip():
        raise ValueError('Use the dedicated change reason.')


def execute(app, request, *, grant_owner=None, binding=None):
    mode=request['mode']
    if mode not in MODES and mode!='approval_decision':return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your owner passkey to change an interpretation.')
    with grant_owner() as owner:
        pending=app.action_approvals.pending(owner=owner,binding=binding)
        if mode=='approval_decision' and (not pending or pending['kind']!=KIND):return None
        require_dispatch('ui.interpretation_change_prepare')
        if app.cancel_event.is_set():raise GenerationCancelled('Interpretation change cancelled.')
        if mode=='approval_decision':
            require_dispatch('ui.approval_decision')
            result=app.action_approvals.decide_interpretation_change(owner=owner,binding=binding,
                session=request.get('session'),approval_id=request['approval_id'],
                fingerprint=request['approval_fingerprint'],decision=request['approval_decision'],policy_revision=revision())
            data=dict(reply=('Interpretation change recorded.' if request['approval_decision']=='approve'
                             else 'Interpretation change rejected.')+
                      ' Original evidence and failed checks remain visible. No work was executed.',
                      approval_resolved=result['approval']['id'],interpretation_change=result['interpretation_change'])
            return {**app.save(request,data,'Interpretation lifecycle',model='owner-reviewed-evidence'),'action':mode}
        validate(request)
        if pending:raise ValueError('Approve or deny the pending proposal before preparing another review.')
        saved=app.save(request,{'reply':'Preparing an exact change to the saved interpretation.'},
                       'Interpretation lifecycle',model='owner-reviewed-evidence')
        row=app.action_approvals.prepare_interpretation_change(owner=owner,binding=binding,
            session=saved['session'],interpretation_id=request['interpretation_id'],
            interpretation_fingerprint=request['interpretation_fingerprint'],
            expected_state_sha256=request['interpretation_state_sha256'],operation=request['interpretation_operation'],
            reason=request['interpretation_reason'],snapshot_sha256=request.get('interpretation_snapshot_sha256'),
            fact_sha256=request.get('interpretation_fact_sha256'),policy_revision=revision())
        return {**saved,'action':mode,'reply':'Review the exact interpretation change before approving or denying it.',
                'details':{**saved['details'],'approval':row}}
