"""Authenticated automatic syntax placement and bounded durable waiting."""
from contextlib import contextmanager
import hashlib
import re
import secrets
import time
from types import SimpleNamespace

from .action_approvals import decision_text
from .application_policy import require_dispatch
from .remote_job_envelope import seal_request
from .remote_job_protocol import LINT_OPERATION
from .remote_worker_client import RemoteWorkerClient, OwnerLedger
from .scheduled_syntax import ScheduledSyntax, public_job, PLACEMENT_STRATEGIES
from . import ui_workspace

MODES={'scheduled_syntax_prepare','scheduled_syntax_tick','scheduled_syntax_cancel','scheduled_syntax_recover'}
FIELDS={'scheduled_job_id','scheduled_placement','scheduled_operation'}


def validate(body):
    mode=body.get('mode')
    if mode not in MODES:
        if FIELDS & body.keys(): raise ValueError('Scheduled options require a scheduled syntax task.')
        return
    if mode=='scheduled_syntax_prepare':
        operation=body.get('scheduled_operation','python-syntax-check')
        if not isinstance(operation,str) or operation not in ('python-syntax-check',LINT_OPERATION):
            raise ValueError('Choose a supported verification operation.')
        strategy=body.get('scheduled_placement','available-headroom-v1')
        if not isinstance(strategy,str) or strategy not in PLACEMENT_STRATEGIES:
            raise ValueError('Choose a supported placement strategy.')
        files=body.get('remote_files')
        if not body.get('project') or not isinstance(files,str) or not 1<=len(files.splitlines())<=8 or len(files)>4000 or any(not f.strip() for f in files.splitlines()):
            raise ValueError('Choose one to eight saved Python files from an open project.')
    elif 'remote_files' in body: raise ValueError('File selection belongs to a new placement review.')
    if mode!='scheduled_syntax_prepare' and {'scheduled_placement','scheduled_operation'} & body.keys():
        raise ValueError('Placement belongs to a new review.')
    if mode in {'scheduled_syntax_cancel','scheduled_syntax_recover'}:
        if not isinstance(body.get('scheduled_job_id'),str) or not re.fullmatch('[0-9a-f]{32}',body['scheduled_job_id']):
            raise ValueError('Select the exact waiting job identifier.')
    elif 'scheduled_job_id' in body: raise ValueError('Job identifiers belong to cancellation or recovery.')


def gateway(app):
    return getattr(app,'_remote_worker_client',None) or RemoteWorkerClient(app.remote)


def queue(app,owner):
    from .syntax_waiting_store import WaitingStore
    return WaitingStore(gateway(app).root/'owners'/owner/'waiting.sqlite3',owner)


def adapters(app,owner):
    from .local_syntax_worker import local_target,local_admission,execute_local_syntax
    remote=gateway(app)
    def local_authorize(req):
        require_dispatch('workspace.tests')
    def local_execute(req):
        local_authorize(req)
        return execute_local_syntax(req)
    local=getattr(app,'_local_syntax_adapter',None) or SimpleNamespace(
        target=local_target,admission=local_admission,execute=local_execute,recover=lambda req:None,authorize=local_authorize)
    def remote_authorize(req):
        target=remote.target()
        require_dispatch('remote.action')
        require_dispatch('remote.dispatch',scope=dict(account=target['account_id'],device=target['device_id'],
            tool='prometheus.lint' if req['operation']==LINT_OPERATION else 'prometheus.syntax',request_digest=req['request_digest']))
    def remote_execute(req):
        remote_authorize(req)
        target=remote.target()
        ledger=OwnerLedger(remote.root,owner,authority_digest=target['configuration_digest'])
        claim=ledger.claim(req)
        if not claim['claimed']:
            if claim.get('receipt'): return claim['receipt']
            raise ValueError('An uncertain remote claim cannot be repeated.')
        now=int(remote.clock())
        receipt=remote.dispatch(seal_request(req,remote.signer,issued_at=now,expires_at=now+300))
        return ledger.finish(req,receipt)['receipt']
    def recover(req):
        ledger=OwnerLedger(remote.root,owner,authority_digest=remote.target()['configuration_digest'])
        return remote.recover(req,ledger)
    return [local,SimpleNamespace(target=remote.target,admission=remote.admission,execute=remote_execute,recover=recover,
        authorize=remote_authorize,lint_tool=getattr(remote,'lint_tool',lambda:None))]


def arm(app,grant_owner,binding):
    """Remember only a live owner grant; durable rows hold exact work authority."""
    with app.lock:
        app._scheduled_syntax_owner=(grant_owner,binding)


def status(app, *, grant_owner, binding):
    with grant_owner() as owner:
        store=queue(app,owner)
        store.expire(now=getattr(app,'_scheduled_syntax_clock',time.time)())
        rows=[public_job(row) for row in store.list()]
    arm(app,grant_owner,binding)
    return dict(jobs=rows,auto_resume='Approved waiting work resumes while this owner is unlocked and the app is idle.',
        interval_seconds=30)


def idle(app):
    """Called by the existing server loop; no additional thread or busy polling."""
    now=time.monotonic()
    with app.lock:
        auth=getattr(app,'_scheduled_syntax_owner',None)
        if (not auth or app.closing or app.current is not None or app.cancel_event.is_set()
                or now<getattr(app,'_scheduled_syntax_next',0)): return False
        app._scheduled_syntax_next=now+30
    grant,binding=auth
    try:
        with grant() as owner:
            if app.action_approvals.has_binding(binding): return False
            store=queue(app,owner)
            wall=getattr(app,'_scheduled_syntax_clock',time.time)()
            store.expire(now=wall)
            due=any(row['state']=='waiting' and row['next_attempt_at']<=wall for row in store.list())
        if due:
            app.submit(dict(mode='scheduled_syntax_tick',prompt=''),grant_owner=grant,
                approval_binding=binding,background=True)
            return True
    except (ValueError,OSError,RuntimeError):
        # Authentication, configuration and busy changes never start work.
        return False
    return False


def execute(app,request,*,grant_owner=None,binding=None):
    mode=request['mode']
    choice=decision_text(request.get('prompt','')) if mode=='chat' else request.get('approval_decision')
    possible=bool(choice and app.action_approvals.has_binding(binding))
    if mode not in MODES and not possible:return None
    if grant_owner is None or binding is None:raise ValueError('Unlock Prometheus to use approved waiting work.')
    with grant_owner() as owner:
        pending=app.action_approvals.pending(owner=owner,binding=binding)
        if possible and (not pending or pending['kind']!='scheduled_syntax'):return None
    @contextmanager
    def current_owner():
        with grant_owner() as current:
            if current!=owner:raise ValueError('Authenticated owner changed.')
            yield current
    store=queue(app,owner)
    clock=getattr(app,'_scheduled_syntax_clock',time.time)
    controller=ScheduledSyntax(app.action_approvals,store,adapters=adapters(app,owner),
        grant_owner=current_owner,binding=binding,clock=clock,cancelled=app.cancel_event.is_set)
    arm(app,current_owner,binding)
    if possible:
        result=controller.decide(session=request.get('session'),
            approval_id=pending['id'] if mode=='chat' else request['approval_id'],
            fingerprint=pending['fingerprint'] if mode=='chat' else request['approval_fingerprint'],decision=choice)
        resolved=result['approval_resolved']
        if result['state']=='waiting':
            app.progress('Checking resources for the exact approved destination')
            result=controller.run_one(result['job_id']) or result
        details=dict(reply='Scheduled verification: '+result['state']+'.',scheduled_syntax=result,approval_resolved=resolved)
    elif mode=='scheduled_syntax_prepare':
        if pending:raise ValueError('Approve or deny the existing review first.')
        require_dispatch('ui.scheduled_syntax_prepare')
        with current_owner():
            workspace=ui_workspace.workspace(app,request)
            files=[]
            with workspace.lock:
                for relative in request['remote_files'].splitlines():
                    path,raw,content=workspace._read(relative.strip())
                    files.append(dict(path=path.relative_to(workspace.root).as_posix(),content=content))
            saved=app.save(request,dict(reply='Review the automatic destination, exact source and one-hour waiting permission.'),
                'Automatic verification placement',model='owner-reviewed-worker')
        app.progress('Checking eligible destinations for the selected verification')
        row=controller.prepare(files,secrets.token_hex(16),session=saved['session'],
            strategy=request.get('scheduled_placement','available-headroom-v1'),
            operation=request.get('scheduled_operation','python-syntax-check'))
        saved['details']['approval']=row
        return {**saved,'action':mode}
    elif mode=='scheduled_syntax_cancel':
        require_dispatch('ui.scheduled_syntax_cancel')
        with current_owner():result=public_job(store.cancel(request['scheduled_job_id'],now=clock()))
        details=dict(reply='Waiting job: '+result['state']+'.',scheduled_syntax=result)
    else:
        app.progress('Recovering the original result without replay' if mode=='scheduled_syntax_recover' else 'Checking one approved waiting job')
        result=controller.run_one(request.get('scheduled_job_id'),recover=mode=='scheduled_syntax_recover')
        details=dict(reply='Approved queue checked.',scheduled_syntax=result,
            scheduled_queue=[public_job(row) for row in store.list()])
        if mode=='scheduled_syntax_tick':
            return dict(action=mode,label='Approved waiting work',reply='',details=details)
    with current_owner():
        saved=app.save({**request,'mode':'scheduled_syntax_prepare'},details,'Approved waiting work',model='owner-reviewed-worker')
        return {**saved,'action':mode}
