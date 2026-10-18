"""Authenticated application path for reviewed supplemental Surface checks."""
import json
import secrets
from contextlib import contextmanager

from .action_approvals import decision_text
from .application_policy import require_dispatch
from .remote_job_review import RemoteJobReview
from .remote_job_protocol import normalize_request, validate_receipt
from .remote_worker_client import RemoteWorkerClient, OwnerLedger, MAX_TRANSPORT_BYTES
from . import ui_workspace
from . import scheduled_syntax_ui

MODES = {'remote_syntax_info','remote_syntax_prepare','remote_syntax_recover'} | scheduled_syntax_ui.MODES
FIELDS = {'remote_files','remote_job_id'} | scheduled_syntax_ui.FIELDS


def validate(body):
    scheduled_syntax_ui.validate(body)
    mode = body.get('mode')
    if mode in scheduled_syntax_ui.MODES:
        if 'remote_job_id' in body: raise ValueError('Manual recovery identifiers require a manual recovery task.')
        return
    if mode not in MODES:
        if FIELDS & body.keys(): raise ValueError('Worker options require a worker task.')
        return
    if mode == 'remote_syntax_prepare':
        if not body.get('project') or not isinstance(body.get('remote_files'),str):
            raise ValueError('Open a project and select its Python files first.')
        files = body['remote_files'].splitlines()
        if not 1 <= len(files) <= 8 or len(body['remote_files'])>4000 or any(not f.strip() for f in files):
            raise ValueError('Choose one to eight existing relative Python files.')
    elif 'remote_files' in body:
        raise ValueError('File selection belongs to a new worker review.')
    if mode == 'remote_syntax_recover':
        import re
        if not isinstance(body.get('remote_job_id'),str) or not re.fullmatch('[0-9a-f]{32}',body['remote_job_id']):
            raise ValueError('Use the exact job ID from the previous worker result.')
    elif 'remote_job_id' in body:
        raise ValueError('A prior job identifier belongs to recovery.')


def client(app):
    # Test/preview injection is server-owned, never an HTTP option.
    return getattr(app,'_remote_worker_client',None) or RemoteWorkerClient(app.remote)


def execute(app, request, *, grant_owner=None, binding=None):
    scheduled=scheduled_syntax_ui.execute(app,request,grant_owner=grant_owner,binding=binding)
    if scheduled is not None: return scheduled
    mode = request['mode']
    decision = decision_text(request['prompt']) if mode=='chat' else request.get('approval_decision')
    possible = bool(decision and app.action_approvals.has_binding(binding))
    if mode not in MODES and not possible: return None
    if grant_owner is None or binding is None: raise ValueError('Unlock Prometheus to review worker tasks.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner,binding=binding)
        if possible and (not pending or pending['kind']!='remote_syntax'): return None
    @contextmanager
    def current_owner():
        with grant_owner() as current:
            if current != owner: raise ValueError('The authenticated owner changed.')
            yield current
    gateway = client(app)
    authority = pending['payload']['target'].get('configuration_digest') if possible else None
    if mode=='remote_syntax_recover': authority=gateway.target().get('configuration_digest')
    ledger = OwnerLedger(gateway.root,owner,authority_digest=authority)
    controller = RemoteJobReview(app.action_approvals,ledger,grant_owner=current_owner,binding=binding,
        target=gateway.target,signer=gateway.signer,dispatch=gateway.dispatch,
        admission=gateway.admission,clock=gateway.clock,cancelled=app.cancel_event.is_set)
    if possible:
        require_dispatch('ui.approval_decision')
        if app.cancel_event.is_set(): raise ValueError('Worker task cancelled before the reviewed decision.')
        req = pending['payload']['request']
        app.progress('Processing the exact worker review')
        result = controller.decide(session=request.get('session'),
            approval_id=pending['id'] if mode=='chat' else request['approval_id'],
            fingerprint=pending['fingerprint'] if mode=='chat' else request['approval_fingerprint'],decision=decision)
        result['job_id'] = req['job_id']
        from .capability_outcome_hooks import gap_notice
        details = dict(reply='Remote syntax check: '+result['state']+'.'+gap_notice(result.get('decision_outcome')),remote_syntax=result,
                       approval_resolved=result['approval_resolved'])
    elif mode == 'remote_syntax_info':
        require_dispatch('ui.remote_syntax_info')
        try:
            target = gateway.target()
            resources = gateway.admission()
            status = dict(ready=resources['decision']=='ready',target=target,resource_admission=resources,
                reason='; '.join(resources['reasons']))
        except ValueError as error: status = dict(ready=False,reason=str(error))
        return dict(action=mode,label='Worker availability',reply='Worker availability checked.',details={'remote_worker':status})
    elif mode == 'remote_syntax_recover':
        require_dispatch('ui.remote_syntax_recover')
        with current_owner():
            req = ledger.request(request['remote_job_id'])
            app.progress('Reading the retained worker result without rerunning its code')
            try: result = gateway.recover(req,ledger)
            except Exception:
                result = dict(state='unconfirmed',receipt=None)
            from .capability_outcome_hooks import reconcile_worker, attach, gap_notice
            attach(result, reconcile_worker(app.action_approvals, owner, req, result))
            details = dict(reply='Worker recovery: '+result['state']+'. No execution was retried.'+gap_notice(result.get('decision_outcome')),
                remote_syntax={**result,'job_id':req['job_id'],
                    'evidence':validate_receipt(req,result['receipt']) if result.get('receipt') else None})
    else:
        require_dispatch('ui.remote_syntax_prepare')
        if pending: raise ValueError('Approve or deny the existing review first.')
        with current_owner():
            target = gateway.target()
            workspace = ui_workspace.workspace(app,request)
            files=[]
            with workspace.lock:
                for relative in request['remote_files'].splitlines():
                    path, raw, content = workspace._read(relative.strip())
                    files.append(dict(path=path.relative_to(workspace.root).as_posix(),content=content))
            req = normalize_request(dict(schema_version=1,job_id=secrets.token_hex(16),
                device_id=target['device_id'],worker_digest=target['worker_digest'],operation='python-syntax-check',
                timeout_seconds=10,files=files))
            # Leave room for the signed envelope in this bridge's command budget.
            if len(json.dumps(req,ensure_ascii=False).encode())>MAX_TRANSPORT_BYTES-4096:
                raise ValueError('Select a smaller source set; this connection supports about 58 KiB per review.')
            result = app.save(request,{'reply':'Review the exact files and destination before sending this syntax check.'},
                              'Remote syntax review',model='owner-reviewed-worker')
        row = controller.prepare(req,session=result['session'])
        result['details']['approval']=row
        result['details']['remote_worker']={'ready':True,'target':target,
            'resource_admission':row['review']['resource_admission']}
        return {**result,'action':mode}
    with current_owner():
        result = app.save({**request,'mode':'remote_syntax_prepare'},details,'Remote syntax check',model='owner-reviewed-worker')
        return {**result,'action':'remote_syntax_prepare'}
