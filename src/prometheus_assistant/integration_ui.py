"""Owner-reviewed integrations through the existing text, voice and button path.

The model can supply task ideas, never authority. Only authenticated owner input
reaches this dispatcher; provider text is saved as results rather than executed.
"""
from __future__ import annotations

import re
import secrets

from .action_approvals import decision_text
from .application_policy import require_dispatch, revision
from .hardware import resource_root
from .ollama import GenerationCancelled
from .capability_outcome_hooks import tracking, gap_notice

MODES = {'integration', 'integration_access'}
FIELDS = {'adapter', 'operation', 'arguments', 'task_grant', 'task'}
ADAPTERS = {'agentmemory', 'browser_use', 'agentmemory_service'}
MEMORY_ADAPTERS = {'agentmemory', 'agentmemory_service'}
LABELS = {'agentmemory': 'AgentMemory classic', 'agentmemory_service': 'AgentMemory semantic service',
          'browser_use': 'Browser Use · public page'}
KINDS = {'integration', 'integration_access'}


def provider(adapter):
    if adapter == 'agentmemory':
        from . import agent_memory
        return agent_memory
    if adapter == 'agentmemory_service':
        from . import agent_memory_service
        return agent_memory_service
    if adapter == 'browser_use':
        from . import browser_agent
        return browser_agent
    raise ValueError('Choose an installed integration.')


def validate(body):
    mode = body.get('mode')
    if mode not in MODES:
        if FIELDS & body.keys():
            raise ValueError('Integration fields require an integration task.')
        return
    allowed = {'mode', 'prompt', 'session'} | ({'task'} if mode == 'integration_access'
                                             else {'adapter', 'operation', 'arguments', 'task_grant'})
    if set(body) - allowed:
        raise ValueError('Use only the selected integration task fields.')
    if mode == 'integration_access':
        from .integration_contracts import canonical_task
        canonical_task(body.get('task'))
    else:
        if not isinstance(body.get('adapter'), str) or body['adapter'] not in ADAPTERS:
            raise ValueError('Choose an installed integration.')
        provider(body['adapter']).canonical_arguments(body.get('operation'), body.get('arguments'))
        if 'task_grant' in body and (not isinstance(body['task_grant'], str)
                or not re.fullmatch('[a-f0-9]{48}', body['task_grant'])):
            raise ValueError('Select an approved task or review this action first.')


def from_chat(prompt):
    """Conservative complete owner commands; quoted/provider text cannot approve."""
    match = re.fullmatch(r'(?:please\s+)?(remember|recall)\s+with\s+agent\s*memory\s+semantic(?:\s+service)?\s+'
                        r'(?:in|from)\s+project\s+([a-z0-9_-]+)\s*:\s*(.+)', prompt.strip(), re.I | re.S)
    if match:
        operation = match[1].lower()
        return {'adapter': 'agentmemory_service', 'operation': operation,
                'arguments': {'project': match[2].lower(), 'content' if operation == 'remember' else 'query': match[3]}}
    match = re.fullmatch(r'(?:please\s+)?remember\s+in\s+project\s+([a-z0-9_-]+)\s*:\s*(.+)',
                        prompt.strip(), re.I | re.S)
    if match:
        return {'adapter': 'agentmemory', 'operation': 'remember',
                'arguments': {'project': match[1].lower(), 'content': match[2]}}
    match = re.fullmatch(r'(?:please\s+)?recall\s+from\s+project\s+([a-z0-9_-]+)\s*:\s*(.+)',
                        prompt.strip(), re.I | re.S)
    if match:
        return {'adapter': 'agentmemory', 'operation': 'recall',
                'arguments': {'project': match[1].lower(), 'query': match[2]}}
    match = re.fullmatch(r'(?:please\s+)?(?:use\s+(?:the\s+)?browser(?:\s+use)?\s+to\s+read|browse)\s+(https://\S+)',
                        prompt.strip(), re.I)
    if match:
        return {'adapter': 'browser_use', 'operation': 'read', 'arguments': {'url': match[1]}}
    return None


def access_from_chat(prompt):
    semantic = re.subn(r'^(\s*(?:please\s+)?(?:give|allow)\s+agent\s*memory)\s+semantic(?:\s+service)?(?=\s+full access)',
                       r'\1', prompt, count=1, flags=re.I)
    if semantic[1]:
        task = access_from_chat(semantic[0])
        if task:
            task['adapter'] = 'agentmemory_service'
        return task
    duration = r'(until i (?:revoke it|say stop)|for ([0-9]{1,9}) (seconds?|minutes?|hours?|days?))'
    match = re.fullmatch(r'(?:please\s+)?(?:give|allow)\s+agent\s*memory\s+full access\s+(?:to|for)\s+project\s+'
                        r'([a-z0-9_-]+)\s+' + duration + r'(?:\s+to\s+(.+))?[.!]?', prompt.strip(), re.I)
    if match:
        adapter, resources, actions = 'agentmemory', {'project': match[1].lower()}, ['remember', 'recall', 'forget']
        description = match[5] or 'Maintain memory for project ' + match[1].lower()
    else:
        match = re.fullmatch(r'(?:please\s+)?(?:give|allow)\s+browser\s+use\s+full access\s+to\s+'
                            r'(all public websites|[a-z0-9.-]+)\s+' + duration + r'(?:\s+to\s+(.+))?[.!]?',
                            prompt.strip(), re.I)
        if not match:
            return None
        adapter, actions = 'browser_use', ['read']
        resources = {'public_web': True} if match[1].casefold() == 'all public websites' else {'hosts': [match[1].lower()]}
        description = match[5] or 'Read public websites for my requests'
    seconds = None if match[2].lower().startswith('until') else int(match[3]) * {
        'second': 1, 'minute': 60, 'hour': 3600, 'day': 86400}[match[4].lower().rstrip('s')]
    from .integration_contracts import canonical_task
    return canonical_task({'task_id': secrets.token_hex(16), 'task': description,
        'adapter': adapter, 'actions': actions, 'resources': resources, 'duration_seconds': seconds})


def _result(app, request, data, label):
    return {**app.save(request, data, label, model='owner-reviewed-integration'), 'action': request['mode']}


def _check_cancel(app):
    if app.cancel_event.is_set():
        raise GenerationCancelled('Integration stopped by the owner.')


def _call(app, adapter, operation, arguments):
    _check_cancel(app)
    require_dispatch('integrations.' + adapter)
    app.progress('Using ' + LABELS[adapter])
    try:
        return provider(adapter).execute(operation, arguments, data_root=app.data_root,
            resource_root=resource_root(), cancel_event=app.cancel_event)
    except Exception:
        # Native adapters report their own bounded error types. An owner Stop
        # takes precedence so a normal cancellation is not shown as a fault.
        _check_cancel(app)
        raise


def _prepare_dispatch(app, grant_owner, owner):
    # Do not hold the owner lock during a child process: Lock, Stop and Revoke
    # must remain responsive. Mark the job while serialized with owner logout.
    with grant_owner() as current_owner:
        if current_owner != owner:
            raise ValueError('Owner changed before dispatch.')
        _check_cancel(app)
        with app.lock:
            if app.current:
                app.jobs[app.current]['integration_active'] = True


def _response(app, request, adapter, operation, data, *, grant_id=None, resolved=None, outcome=None):
    _check_cancel(app)
    details = {'integration': {'adapter': adapter, 'operation': operation, 'result': data},
               'task_grant': grant_id}
    if resolved:
        details['approval_resolved'] = resolved
    if outcome is not None:
        details['decision_outcome'] = outcome
    # Bounded provider output is evidence, not further instructions or approval.
    from .ui_server import readable_result
    details['reply'] = readable_result(data)
    if adapter in MEMORY_ADAPTERS and isinstance(data.get('result'), dict):
        result = data['result']
        if operation == 'remember' and result.get('saved'):
            details['reply'] = 'Saved in project ' + data['project'] + '. Memory ID: ' + result['saved']
        elif operation == 'forget' and type(result.get('deleted')) is int:
            details['reply'] = f"Forgot {result['deleted']} selected memories in project {data['project']}."
        elif operation == 'recall' and isinstance(result.get('results'), list):
            details['reply'] = '\n\n'.join(str(row.get('content', '')) + '\nMemory ID: ' + str(row.get('id', ''))
                                          for row in result['results']) or 'No matching memories in this project.'
    details['reply'] += gap_notice(outcome)
    return _result(app, request, details, LABELS[adapter])


def select_task(app, ident, *, owner, binding, session=None):
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{48}', ident):
        raise ValueError('Choose an existing approved task.')
    if session is not None and (not isinstance(session, str) or not re.fullmatch('[a-f0-9]{32}', session)):
        raise ValueError('Choose a valid conversation.')
    app.action_approvals._key(owner, binding)
    grant = next((row for row in app.grants.inspect_tasks(owner=owner)
                  if row['id'] == ident and row['state'] == 'active'), None)
    if grant is None:
        raise ValueError('This task access is no longer active.')
    from .memory import MemoryStore
    with MemoryStore(app.memory_path) as memory:
        if session:
            memory.session(session)
        else:
            session = memory.create_session('owner-reviewed-integration')
    with app.lock:
        app.integration_tasks[(owner, binding, session)] = ident
    return {'id': ident, 'contract': grant['contract'], 'session': session}


def _review_action(adapter, operation, arguments):
    if adapter in MEMORY_ADAPTERS:
        verb = {'remember': 'Save a note', 'recall': 'Find saved notes', 'forget': 'Delete selected notes'}[operation]
        return {'action': verb + ' in project ' + arguments['project'],
                'reason': 'Use the project memory to carry out your requested task.',
                'effects': ('Starts the local semantic memory service for this action, then stops it. ' if adapter == 'agentmemory_service' else '') +
                           ('Stores the supplied note on your portable data drive.' if operation == 'remember' else
                            'Reads matching notes from this project only.' if operation == 'recall' else
                            'Permanently removes the selected memory IDs from this project.'),
                'resources': arguments, 'duration': 'This action once'}
    return {'action': 'Read this page with Browser Use',
            'reason': 'Use a real browser to extract the public page requested for your task.',
            'effects': 'Starts a temporary browser, fetches this URL and same-host public page assets, '
                       'returns text and links, then closes its browser. No account profile is used.',
            'resources': arguments, 'duration': 'This action once'}


def _review_task(task):
    duration = ('Until you revoke it' if task['duration_seconds'] is None else
                str(task['duration_seconds']) + ' seconds from approval')
    return {'action': 'Allow continuing access for: ' + task['task'],
            'reason': 'Let the selected integration complete this task without asking again for each permitted operation.',
            'effects': 'Prometheus may choose resources within this reviewed scope. Access outside it requires a new review. '
                       'You can stop a running action or revoke the task in Integrations.',
            'resources': {'adapter': task['adapter'], 'actions': task['actions'], **task['resources']},
            'duration': duration}


def _propose(app, request, owner, binding, kind, payload, review):
    from .ui_server import readable_result
    reply = (f"Action: {review['action']}\nWhy: {review['reason']}\nEffects: {review['effects']}\n"
             f"Resources: {readable_result(review['resources'])}\nAccess lasts: {review['duration']}\n\n"
             'Say or type “yes, approve” to proceed, or “reject” to cancel. '
             'This pending review expires in ten minutes; the approved access lasts for the duration shown above.')
    result = _result(app, request, {'reply': reply, 'integration_review': review}, 'Integration · needs your approval')
    pending = app.action_approvals.review(owner=owner, binding=binding, session=result['session'],
        kind=kind, payload=payload, review=review, policy_revision=revision())
    result['details']['approval'] = pending
    return result


def execute(app, request, *, grant_owner=None, binding=None):
    mode = request['mode']
    command = from_chat(request['prompt']) if mode == 'chat' else None
    task_command = access_from_chat(request['prompt']) if mode == 'chat' else None
    decision = decision_text(request['prompt']) if mode == 'chat' else request.get('approval_decision')
    possible_decision = bool(decision and app.action_approvals.has_binding(binding))
    if mode not in MODES and not command and not task_command and not possible_decision:
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your enrolled owner passkey before using integrations.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
    if possible_decision and pending and pending['kind'] not in KINDS:
        return None
    if mode == 'approval_decision' and not pending:
        return None  # The existing change adapter reports the missing review.
    if possible_decision and pending:
        if request.get('session') != pending['session']:
            raise ValueError('Return to the conversation containing this proposal before deciding.')
        ident = pending['id'] if mode == 'chat' else request['approval_id']
        fingerprint = pending['fingerprint'] if mode == 'chat' else request['approval_fingerprint']
        require_dispatch('ui.approval_decision')
        with grant_owner() as current_owner:
            if current_owner != owner:
                raise ValueError('Owner changed before approval.')
            _check_cancel(app)
            row = app.action_approvals.consume(owner=owner, binding=binding, session=request['session'],
                approval_id=ident, fingerprint=fingerprint, policy_revision=revision(), decision=decision)
            if decision == 'reject':
                return _result(app, request, {'reply': 'The integration proposal was rejected. No action ran.',
                    'approval_resolved': row['id']}, 'Proposal rejected')
            if row['kind'] == 'integration_access':
                with tracking(app.action_approvals, owner, row) as outcome:
                    grant = app.grants.issue_task(row['payload']['task'], owner=owner)
                    observed = outcome.observe('access_granted', evidence={'granted': True})
                with app.lock:
                    app.integration_tasks[(owner, binding, request['session'])] = grant['id']
                return _result(app, request, {'reply': 'Approved. Continuing access is active for this task. '
                    'You can revoke it in Integrations.' + gap_notice(observed), 'task_grant': grant, 'approval_resolved': row['id'],
                    **({'decision_outcome': observed} if observed is not None else {})},
                    'Task access approved')
        payload = row['payload']
        with tracking(app.action_approvals, owner, row) as outcome:
            _prepare_dispatch(app, grant_owner, owner)
            data = _call(app, payload['adapter'], payload['operation'], payload['arguments'])
            observed = outcome.observe('adapter_returned', evidence={'returned': True})
        return _response(app, request, payload['adapter'], payload['operation'], data,
                         resolved=row['id'], outcome=observed)
    if pending:
        raise ValueError('Approve or reject your existing proposal before preparing another.')
    if mode == 'integration_access' or task_command:
        from .integration_contracts import canonical_task
        task = task_command or canonical_task(request['task'])
        require_dispatch('integrations.' + task['adapter'])
        review = _review_task(task)
        with grant_owner() as current_owner:
            if current_owner != owner:
                raise ValueError('Owner changed before review.')
            _check_cancel(app)
            return _propose(app, request, owner, binding, 'integration_access', {'task': task}, review)
    command = command or {key: request[key] for key in ('adapter', 'operation', 'arguments')}
    adapter, operation = command['adapter'], command['operation']
    arguments = provider(adapter).canonical_arguments(operation, command['arguments'])
    require_dispatch('integrations.' + adapter)
    ident = request.get('task_grant')
    if mode == 'chat' and ident is None:
        from .integration_contracts import allows_task
        with app.lock:
            selected = app.integration_tasks.get((owner, binding, request.get('session')))
        match = next((row for row in app.grants.inspect_tasks(owner=owner)
                      if row['id'] == selected and row['state'] == 'active'
                      and allows_task(row['contract'], adapter, operation, arguments)), None)
        if match:
            ident = match['id']
    if ident:
        _prepare_dispatch(app, grant_owner, owner)
        with app.lock:
            if app.current:
                app.jobs[app.current]['task_grant'] = ident
        data = app.grants.run_task(ident, secrets.token_hex(16), adapter, operation, arguments,
            owner=owner, execute=lambda selected, canonical: _call(app, adapter, selected, canonical))
        result = _response(app, request, adapter, operation, data, grant_id=ident)
        with app.lock:
            app.integration_tasks[(owner, binding, result['session'])] = ident
        return result
    payload = {'adapter': adapter, 'operation': operation, 'arguments': arguments}
    with grant_owner() as current_owner:
        if current_owner != owner:
            raise ValueError('Owner changed before review.')
        _check_cancel(app)
        return _propose(app, request, owner, binding, 'integration', payload, _review_action(**payload))
