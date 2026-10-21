"""Owner-authenticated bounded follow-up checks for interpreted evidence."""
from contextlib import contextmanager
import re
import secrets
import time

from .evidence_resolution import reason_text
from .remote_job_protocol import LINT_OPERATION
from .scheduled_syntax import PLACEMENT_STRATEGIES
from .application_policy import require_dispatch
from . import scheduled_syntax_ui, ui_workspace

MODES = {'evidence_followup_prepare', 'evidence_followup_tick',
         'evidence_followup_cancel', 'evidence_followup_recover'}
CONTEXT = {'interpretation_id', 'interpretation_fingerprint', 'state_sha256',
           'snapshot_sha256', 'fact_sha256', 'reason'}
PREPARE_FIELDS = {'followup_'+key for key in CONTEXT} | {'followup_operation', 'followup_placement'}
FIELDS = PREPARE_FIELDS | {'followup_job_id'}
KIND = 'evidence_followup'


def validate(body):
    mode = body.get('mode')
    if mode not in MODES:
        if FIELDS & body.keys():
            raise ValueError('Follow-up fields require their dedicated evidence review.')
        return
    allowed = {'mode', 'prompt', 'session'}
    if mode == 'evidence_followup_prepare':
        required = PREPARE_FIELDS | {'project', 'remote_files'}
        if not required <= body.keys() or set(body) - (allowed | required):
            raise ValueError('Use the exact interpretation, evidence, files and check selection.')
        for key in CONTEXT - {'reason'}:
            size = 32 if key == 'interpretation_id' else 64
            value = body['followup_'+key]
            if type(value) is not str or not re.fullmatch('[a-f0-9]{'+str(size)+'}', value):
                raise ValueError('Select exact saved interpretation and evidence identities.')
        reason_text(body['followup_reason'])
        if body['followup_operation'] not in ('python-syntax-check', LINT_OPERATION):
            raise ValueError('Choose a supported syntax or lint check.')
        if type(body['followup_placement']) is not str or body['followup_placement'] not in PLACEMENT_STRATEGIES:
            raise ValueError('Choose a supported destination preference.')
        files = body['remote_files']
        if (type(body['project']) is not str or not body['project'].strip()
                or type(files) is not str or len(files)>4000 or not 1<=len(files.splitlines())<=8
                or any(not line.strip() for line in files.splitlines())):
            raise ValueError('Choose one to eight saved Python files in an open project.')
    else:
        required = {'followup_job_id'} if mode in {'evidence_followup_cancel', 'evidence_followup_recover'} else set()
        if not required <= body.keys() or set(body) - (allowed | required):
            raise ValueError('Saved follow-up controls accept only the exact job identity.')
        if required and (type(body['followup_job_id']) is not str
                         or not re.fullmatch('[a-f0-9]{32}', body['followup_job_id'])):
            raise ValueError('Select the exact saved follow-up job.')
    if type(body.get('prompt','')) is not str or body.get('prompt','').strip():
        raise ValueError('Use the dedicated follow-up reason and controls.')


def store(app, owner):
    from .evidence_followups import EvidenceFollowups
    return EvidenceFollowups(app.action_approvals, owner)


def clock(app):
    return getattr(app, '_scheduled_syntax_clock', time.time)


def arm(app, grant_owner, binding):
    with app.lock:
        app._evidence_followup_owner = (grant_owner, binding)


def status(app, *, grant_owner, binding):
    with grant_owner() as owner:
        jobs = store(app, owner).public_jobs()
    arm(app, grant_owner, binding)
    return dict(schema_version=1, read_only=True, jobs=jobs,
                auto_resume='Approved follow-ups resume while unlocked and idle only if their evidence, interpretation and saved files still match.')


def idle(app):
    now = time.monotonic()
    with app.lock:
        auth = getattr(app, '_evidence_followup_owner', None)
        if (not auth or app.closing or app.current is not None or app.cancel_event.is_set()
                or now < getattr(app, '_evidence_followup_next', 0)):
            return False
        app._evidence_followup_next = now + 30
    grant, binding = auth
    try:
        with grant() as owner:
            if app.action_approvals.has_binding(binding):
                return False
            saved = store(app, owner)
            wall = clock(app)()
            saved.queue.expire(now=wall)
            due = any(row['state']=='waiting' and row['next_attempt_at']<=wall for row in saved.queue.list())
        if due:
            app.submit(dict(mode='evidence_followup_tick', prompt=''), grant_owner=grant,
                       approval_binding=binding, background=True)
            return True
    except (ValueError, OSError, RuntimeError):
        return False
    return False


def execute(app, request, *, grant_owner=None, binding=None):
    mode = request['mode']
    if mode not in MODES and mode != 'approval_decision':
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your owner passkey to review evidence follow-up checks.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
        if mode=='approval_decision' and (not pending or pending['kind']!=KIND):
            return None
    @contextmanager
    def current_owner():
        with grant_owner() as current:
            if current != owner:
                raise ValueError('The authenticated owner changed.')
            yield current
    from .evidence_followups import FollowupScheduler
    saved_store = store(app, owner)
    controller = FollowupScheduler(app.action_approvals, saved_store,
        adapters=scheduled_syntax_ui.adapters(app, owner), grant_owner=current_owner,
        binding=binding, workspace_factory=lambda project: ui_workspace.workspace(app, {'project':project}),
        clock=clock(app), cancelled=app.cancel_event.is_set)
    arm(app, current_owner, binding)
    require_dispatch('ui.'+mode)
    if mode=='evidence_followup_prepare':
        validate(request)
        if pending:
            raise ValueError('Approve or deny the existing proposal before preparing a follow-up.')
        workspace = ui_workspace.workspace(app, request)
        with current_owner(), workspace.lock:
            files = []
            for relative in request['remote_files'].splitlines():
                path, raw, content = workspace._read(relative.strip())
                files.append(dict(path=path.relative_to(workspace.root).as_posix(), content=content))
        saved = app.save(request, dict(reply='Preparing the exact evidence, source and destination review.'),
                         'Evidence follow-up', model='owner-reviewed-worker')
        context = {key:request['followup_'+key] for key in CONTEXT}
        context['reason'] = reason_text(context['reason'])
        row = controller.prepare(project=str(workspace.root), files=files, job_id=secrets.token_hex(16),
            session=saved['session'], context=context, strategy=request['followup_placement'],
            operation=request['followup_operation'])
        return {**saved, 'action':mode,
                'details':{**saved['details'], 'approval':row}}
    if mode=='approval_decision':
        result = controller.decide(session=request.get('session'), approval_id=request['approval_id'],
            fingerprint=request['approval_fingerprint'], decision=request['approval_decision'])
        resolved = result['approval_resolved']
        if result['state']=='waiting':
            result = controller.run_one(result['job_id']) or result
        details = dict(reply='Evidence follow-up: '+result['state']+'. Original evidence and failed checks remain unchanged.',
                       evidence_followup=result, approval_resolved=resolved)
    elif mode=='evidence_followup_cancel':
        with current_owner():
            result = saved_store.cancel(request['followup_job_id'], now=clock(app)())
        details = dict(reply='Evidence follow-up: '+result['state']+'.', evidence_followup=result)
    else:
        result = controller.run_one(request.get('followup_job_id'), recover=mode=='evidence_followup_recover')
        details = dict(reply='Follow-up queue checked without replaying uncertain work.', evidence_followup=result)
        if mode=='evidence_followup_tick':
            return dict(action=mode, label='Evidence follow-up', reply='', details=details)
    with current_owner():
        return {**app.save(request, details, 'Evidence follow-up', model='owner-reviewed-worker'), 'action':mode}
