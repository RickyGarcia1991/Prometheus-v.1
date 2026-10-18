"""Owner-approved project editing and continuing public research."""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone

from .action_approvals import decision_text
from .application_policy import require_dispatch, revision
from .ollama import GenerationCancelled
from . import ui_workspace

MODES = {'project_edit', 'idle_config', 'idle_status', 'idle_run'}
NO_PROMPT = {'idle_config', 'idle_status', 'idle_run'}
FIELDS = {'edit_policy', 'research_query', 'interval_minutes', 'project_files', 'auto_files', 'specialist_review', 'revision_token'}
KINDS = {'project_edit', 'project_task', 'idle_config'}


def validate(body):
    if 'revision_token' in body:
        if (body.get('mode') != 'project_edit' or type(body['revision_token']) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', body['revision_token'])
                or body.get('edit_policy', 'ask_first') != 'ask_first'
                or body.get('research_query', '').strip()):
            raise ValueError('A revision context requires an exact project review without additional research.')
    if 'specialist_review' in body:
        if type(body['specialist_review']) is not bool:
            raise ValueError('Specialist review must be true or false.')
        if body.get('mode') not in {'project_edit', 'chat'}:
            raise ValueError('Specialist review requires a project editing task.')
    if 'auto_files' in body and type(body['auto_files']) is not bool:
        raise ValueError('Automatic file selection must be true or false.')
    if body.get('auto_files'):
        if body.get('edit_policy') == 'delegated' or body.get('project_files') or body.get('file'):
            raise ValueError('Automatic file selection requires exact review and no manual file selection.')
    if 'project_files' in body:
        files = body['project_files']
        if not isinstance(files, str) or len(files) > 4000 or '\x00' in files:
            raise ValueError('Use at most eight existing relative paths, one per line.')
        paths = [p.strip() for p in files.splitlines() if p.strip()]
        if len(paths) > 8 or len(set(p.casefold() for p in paths)) != len(paths):
            raise ValueError('Use at most eight distinct existing relative paths.')
        if paths and body.get('edit_policy') == 'delegated':
            raise ValueError('Multiple files require exact combined review. Choose Show the exact change.')
    mode = body.get('mode')
    if mode not in MODES and mode != 'chat':
        if FIELDS & body.keys():
            raise ValueError('Task options require a project edit or idle research task.')
        return
    if mode not in {'project_edit', 'chat'} and {'edit_policy', 'research_query'} & body.keys():
        raise ValueError('Editing options require an editing task.')
    if 'interval_minutes' in body and mode != 'idle_config':
        raise ValueError('Research interval belongs to idle configuration.')
    if 'edit_policy' in body and body['edit_policy'] not in ('ask_first', 'delegated'):
        raise ValueError('Choose Ask first or Use my judgment for this task.')
    if 'research_query' in body and (not isinstance(body['research_query'], str)
            or len(body['research_query']) > 400 or '\x00' in body['research_query']):
        raise ValueError('Use a public research query of at most 400 characters.')
    if mode == 'project_edit' and not body.get('project', '').strip():
        raise ValueError('Open a development project before requesting an edit.')
    if mode == 'idle_config':
        if type(body.get('enabled')) is not bool:
            raise ValueError('Choose whether idle public research is enabled.')
        interval = body.get('interval_minutes', 60)
        if type(interval) is not int or not 15 <= interval <= 1440:
            raise ValueError('Choose an idle research interval from 15 minutes to 1 day.')
        if body['enabled'] and not body.get('prompt', '').strip():
            raise ValueError('Enter the public topic Prometheus should research.')
        if len(body.get('prompt', '').strip()) > 400:
            raise ValueError('Use an idle research topic of at most 400 characters.')


def from_chat(prompt):
    match = re.fullmatch(r'(?:please\s+)?(?:edit|change|update|fix|rewrite)\s+'
                        r'(?:the\s+)?(?:selected|opened|current)\s+project\s*:\s*(.+)',
                        prompt.strip(), re.I | re.S)
    return match[1] if match else None


def research_store(app):
    from .idle_research import IdleResearch
    if not hasattr(app, '_idle_research'):
        app._idle_research = IdleResearch(app.data_root / 'Maintenance/IdleResearch')
    return app._idle_research


def editor(app, workspace, goal):
    from .project_edit_runtime import editor as create
    return create(app, workspace, goal)


def _cancel(app):
    if app.cancel_event.is_set():
        raise GenerationCancelled('Task stopped by the owner.')


def _verify(app, grant_owner, owner):
    with grant_owner() as current:
        if current != owner:
            raise ValueError('Owner changed before task execution.')
        _cancel(app)
        with app.lock:
            if app.current:
                app.jobs[app.current]['integration_active'] = True


def _result(app, request, details, label, action='project_edit'):
    # Persist rollback evidence even after Stop; a cancelled chat save normally
    # suppresses output, which would hide the actual recovery result here.
    saved_request = {key: value for key, value in request.items() if key != 'revision_token'}
    return {**app.save({**saved_request, 'mode': action}, details, label,
                       model='owner-reviewed-project-task'), 'action': action}


def _review(app, request, owner, binding, kind, payload, review, resolved=None, *, expected_revision=None):
    review_revision = revision()
    if expected_revision is not None and review_revision != expected_revision:
        raise ValueError('Application policy changed during specialist preparation; prepare a new review.')
    reply = (f"Action: {review['action']}\nWhy: {review['reason']}\nBenefit: {review['benefit']}\n"
             f"Effects: {review['effects']}\nAccess lasts: {review['duration']}\n\n"
             'Approve to continue, or deny to cancel. You can stop a running task.')
    details = {'reply': reply, 'task_review': review}
    if resolved:
        details['approval_resolved'] = resolved
    result = _result(app, request, details, 'Task · needs your approval',
                     'idle_config' if kind == 'idle_config' else 'project_edit')
    revision_options = {}
    if request.get('revision_token'):
        from .project_decision_outcomes import project_hash
        revision_options = dict(revision_token=request['revision_token'],
            project_hash=project_hash(ui_workspace.workspace(app, request)))
        payload = {**payload, 'request': {key: value for key, value in payload['request'].items()
                                        if key != 'revision_token'}}
    pending = app.action_approvals.review(owner=owner, binding=binding, session=result['session'],
        kind=kind, payload=payload, review=review, policy_revision=review_revision, **revision_options)
    result['details']['approval'] = pending
    return result


def _task_review(request):
    query = request.get('research_query', '').strip()
    files = [p.strip() for p in request.get('project_files', '').splitlines() if p.strip()]
    return {'action': 'Use my judgment for this project task' if request.get('edit_policy') == 'delegated'
            else 'Research this project task before preparing a change',
        'reason': request['prompt'],
        'benefit': 'Choose a suitable implementation, explain the choice, and check the result.',
        'effects': 'Read this development project, ' + ('prepare the listed existing files, ' if files else 'choose one existing file, ') + 'generate and apply a change, '
                   'and run the selected or detected test presets. Project tests execute local project code '
                   'with this Windows account’s permissions. An unsuccessful check triggers undo of the edit. '
                   + ('Only an exact change approved afterward will be applied. ' if request.get('edit_policy') != 'delegated' else '')
                   + ('Send the displayed public query to the configured search provider. ' if query else '')
                   + ('Ask local correctness and test-coverage specialists to review the exact proposal sequentially, '
                      'checking resource headroom before each stage. Incomplete or high-risk advice requires exact review. '
                      if request.get('specialist_review') else ''),
        'project': request['project'], 'target': ('Up to eight related existing files for exact review' if request.get('auto_files')
            else ', '.join(files) if files else request.get('file') or 'One relevant existing file selected by Prometheus'),
        'resources': {'project': request['project'], 'file': request.get('file', ''), 'files': files, 'public_query': query},
        'checks': [{'preset': request.get('preset', 'auto'), 'target': request.get('target', '')}],
        'duration': 'This task once; Stop cancels remaining work'}


def _research(query, app):
    from .firecrawl_search import search
    _cancel(app)
    app.progress('Looking up the approved public research query')
    # Same public provider path used by the explicit online research interface.
    return search(query, cancel_event=app.cancel_event)


def _prepare(app, request, *, owner, binding, grant_owner, delegated=False, resolved=None, decision_link=None):
    specialist_policy_revision = revision() if request.get('specialist_review') else None
    workspace = ui_workspace.workspace(app, request)
    research = None
    if request.get('research_query', '').strip():
        research = _research(request['research_query'].strip(), app)
        # Supply bounded source data to generation, never execute provider text.
        rows = research.get('results', research.get('sources', []))
        research = [{'title': str(row.get('title', ''))[:250], 'url': str(row.get('url', ''))[:1000],
                     'snippet': str(row.get('excerpt', row.get('snippet', row.get('description', row.get('text', '')))))[:900]}
                    for row in rows[:3] if isinstance(row, dict)]
    _verify(app, grant_owner, owner)
    engine = editor(app, workspace, request['prompt'])
    if research:
        budget = min(12000, getattr(engine, 'context_bytes', 8000) // 4)
        while research and len(json.dumps(research, ensure_ascii=False, separators=(',', ':')).encode()) > budget:
            if len(research) > 1:
                research.pop()
            elif len(research[0]['snippet']) > 100:
                research[0]['snippet'] = research[0]['snippet'][:len(research[0]['snippet']) // 2]
            else:
                research = None
    paths = [p.strip() for p in request.get('project_files', '').splitlines() if p.strip()]
    if request.get('auto_files'):
        from .project_selection import select_files
        paths = select_files(engine, request['prompt'], cancel_event=app.cancel_event)
    options = dict(preset=request.get('preset', 'auto'), test_target=request.get('target', ''),
                   research=research, cancel_event=app.cancel_event)
    if len(paths) > 1:
        from .project_change_set import propose_group
        candidate = propose_group(engine, request['prompt'], paths, **options)
    else:
        candidate = engine.propose(request['prompt'], target=paths[0] if paths else request.get('file', ''), **options)
    _verify(app, grant_owner, owner)
    from . import project_advisory
    specialists = None
    if request.get('specialist_review'):
        from . import project_specialists
        def verify_specialist_owner():
            if revision() != specialist_policy_revision:
                raise ValueError('Application policy changed during specialist preparation; prepare a new review.')
            require_dispatch('ui.project_edit')
            _verify(app, grant_owner, owner)
        specialists = project_specialists.prepare(app, engine, candidate, verify=verify_specialist_owner)
        advisory = specialists['stages'][0]['advice']
        if advisory is None:
            from .advisory_review_runtime import _result as unavailable_advice
            advisory = unavailable_advice('unavailable', 'resource_unavailable')
    else:
        app.progress('Checking whether a local advisory reviewer fits the complete proposed change')
        advisory = project_advisory.prepare(app, engine, candidate)
    _verify(app, grant_owner, owner)
    if specialists is not None:
        verify_specialist_owner()
    if advisory.get('status') == 'cancelled':
        raise GenerationCancelled('Local review stopped; no change was applied.')
    specialist_escalation = specialists is not None and project_specialists.needs_exact_review(specialists)
    if delegated and not project_advisory.needs_exact_review(advisory) and not specialist_escalation:
        return _apply(app, request, candidate, owner=owner, grant_owner=grant_owner, resolved=resolved,
                      model=getattr(engine, 'model', 'local helper'), advisory=advisory, specialists=specialists,
                      decision_link=decision_link)
    review = {'action': 'Apply the proposed change to ' + candidate['path'],
        'reason': candidate['reason'], 'benefit': candidate['benefit'],
        'effects': 'Apply the displayed diff, run the displayed checks, and retain an undo record. '
                   'Project tests execute with this Windows account’s permissions. Failed checks trigger undo. '
                   'After checks, an available local reviewer may inspect the proposed source and recorded process outcomes; it cannot override failed checks or undo.',
        'project': str(workspace.root), 'target': candidate['path'], 'diff': candidate['diff'],
        'checks': candidate['checks'], 'check_reason': candidate['check_reason'],
        'resources': {'project': str(workspace.root), 'file': candidate['path']},
        'duration': 'This exact change and its checks once'}
    from .project_task_evidence import prepare_evidence
    from .project_resources import service
    review['verification'] = prepare_evidence(engine, candidate)
    review['resource_assessment'] = service(app).review(candidate)
    review['advisory_review'] = advisory
    if specialists is not None:
        review['specialist_review'] = specialists
    if delegated:
        review['effects'] += (' Specialist review is incomplete or raised a high-risk concern; review this exact change before applying it.'
                              if specialist_escalation else
                              ' A local reviewer raised a high-risk concern; review this exact change before applying it.')
    return _review(app, request, owner, binding, 'project_edit',
                   {'candidate': candidate, 'request': request, 'model': getattr(engine, 'model', 'local helper'),
                    'advisory_review': advisory, 'specialist_review': specialists},
                   review, resolved, expected_revision=specialist_policy_revision)


def _apply(app, request, candidate, *, owner, grant_owner, resolved=None, model='local helper', advisory=None, specialists=None, decision_link=None):
    from .project_resources import service
    from .project_task_evidence import prepare_evidence
    from .project_editor import ProjectEditor
    _verify(app, grant_owner, owner)
    engine = ProjectEditor(ui_workspace.workspace(app, request), lambda *_: None, progress=app.progress)
    evidence = prepare_evidence(engine, candidate)
    from . import project_advisory
    files = project_advisory.proposed_files(engine, candidate)
    advisory = project_advisory.retain(candidate, files, advisory)
    if bool(request.get('specialist_review')) != (specialists is not None):
        raise ValueError('The retained specialist review does not match the requested task.')
    if specialists is not None:
        from . import project_specialists
        specialists = project_specialists.retain(candidate, files, specialists)
        correctness_advice = specialists['stages'][0]['advice']
        if correctness_advice is not None and advisory != correctness_advice:
            raise ValueError('The retained correctness review does not match the specialist team.')
    app.progress('Rechecking resource headroom before applying the reviewed change')
    with service(app).reserve(candidate, app.current or 'owner-project-task') as resources:
        return _apply_reserved(app, request, candidate, owner=owner, grant_owner=grant_owner,
            resolved=resolved, model=model, evidence=evidence, resources=resources, advisory=advisory,
            specialists=specialists, decision_link=decision_link)


def _apply_reserved(app, request, candidate, *, owner, grant_owner, resolved, model, evidence, resources, advisory=None, specialists=None, decision_link=None):
    from .project_editor import ProjectEditor
    _verify(app, grant_owner, owner)
    engine = ProjectEditor(ui_workspace.workspace(app, request), lambda *_: None, progress=app.progress)
    from .project_advisory import proposed_files
    from .reviewer_check_capture import ApprovedCheckCapture
    snapshot = proposed_files(engine, candidate)
    try:
        capture = ApprovedCheckCapture(candidate, snapshot)
    except (ValueError, TypeError):
        # Optional review limits must not reject a valid owner-approved edit.
        # proposed_files and the editor still validate the exact candidate.
        capture = None
    from .project_decision_outcomes import ProjectOutcome
    outcome = ProjectOutcome(app, engine.workspace, owner, decision_link)
    outcome.start()
    try:
        if candidate.get('version') == 2:
            from .project_change_set import apply_group
            result = apply_group(engine, candidate, cancel_event=app.cancel_event, on_output=app.append_partial,
                check_runner=capture.run_check if capture is not None else None,
                on_result=lambda native: outcome.finish(native, len(candidate['checks'])))
        else:
            from . import project_recovery
            with engine.workspace.lock:
                journal = project_recovery.begin(engine, [candidate])
                result = engine.apply(candidate, cancel_event=app.cancel_event, on_output=app.append_partial,
                                      check_runner=capture.run_check if capture is not None else None)
                outcome.finish(result, len(candidate['checks']))
                project_recovery.finish(engine.workspace, journal, result)
    except Exception:
        outcome.unknown()
        raise
    result.update(project=request['project'], state=result['status'], model=model,
                  reason=candidate['reason'], benefit=candidate['benefit'], diff=candidate['diff'])
    result['advisory_review'] = advisory
    if specialists is not None:
        result['specialist_review'] = specialists
    from .project_task_evidence import finish_evidence
    from .project_advisory import findings
    try:
        reviewer_findings = findings(advisory)
        if specialists is not None:
            from . import project_specialists
            reviewer_findings = project_specialists.findings(specialists)
        result['verification'] = finish_evidence(engine, candidate, evidence, result,
                                                reviewer_findings=reviewer_findings)
    except Exception as error:
        # Preserve actual writes, rollback details and undo even if reporting fails.
        result['verification'] = {'status': 'blocked', 'phase': 'executed', 'authorized': False,
            'error': 'Verification evidence unavailable: ' + str(error)[:300]}
    try:
        from .measured_review_runtime import run_review, validate_saved_review
        if capture is None:
            raise ValueError('Optional measured review snapshot exceeds its supported scope')
        measured_review = run_review(app, candidate, capture.finish())
        validate_saved_review(measured_review)
        result['measured_review'] = measured_review
    except Exception:
        # Review failure must never erase executed checks, undo, or recovery.
        result['measured_review'] = dict(status='runtime_unavailable', attempted=False, report=None, measurement_identity=None, reviewer=None, prompt_version='measured-evidence-reference-v1', prompt_sha256=None, measurement_kind='native_process_outcomes', semantic_evidence_available=False, authorized=False, production_accepted=False)
    result['resource_assessment'] = resources
    from .project_resources import service
    try:
        result['resource_measurements'] = service(app).observe(candidate, result, reservation=resources)
    except Exception as error:
        result['resource_measurements'] = {'recorded': False, 'error': 'Resource history unavailable: ' + str(error)[:300]}
    reply = ('Project task: ' + result['status'] + '.\n' + candidate['reason'] + '\nBenefit: '
             + candidate['benefit'] + '\nFile: ' + candidate['path'])
    if result.get('rollback', {}).get('undone'):
        reply += '\nThe applied edit was undone after the checks or task stopped.'
    if result.get('rollback', {}).get('error'):
        reply += '\nUndo needs attention: ' + result['rollback']['error']
    details = {'reply': reply, 'project_edit': result}
    if resolved:
        details['approval_resolved'] = resolved
    return _result(app, request, details, 'Project task · ' + result['status'])


def run_idle(app, *, force=False):
    require_dispatch('ui.idle_run')
    from .project_resources import background_allowed
    admission = background_allowed(app)
    result = (research_store(app).run_due(cancel_event=app.cancel_event, force=force, progress=app.progress)
              if admission['allowed'] else dict(state='resource_deferred', resource_admission=admission))
    status = research_store(app).status()
    return {'reply': 'Idle public research: ' + result['state'] + '.',
            'details': {'idle_research': {**status, 'result': result}},
            'label': 'Idle public research', 'action': 'idle_run'}


def _observed_at(timestamp):
    # Research stores Unix seconds; saved answer provenance uses ISO text.
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def recall_sources(app, prompt):
    try:
        recalled = research_store(app).recall(prompt, max_results=3, max_chars=6000)
    except (OSError, ValueError):
        app.progress('Saved research needs attention; answering without those sources')
        return []
    return [{'id': 'S' + str(i + 1), 'title': row.get('title') or 'Saved public research',
             'location': row.get('url', ''),
             'excerpt': row.get('snippet') or row.get('excerpt') or row.get('text', ''),
             'imported_at': _observed_at(row.get('last_seen')), 'sha256': row.get('content_sha256')}
            for i, row in enumerate(recalled.get('results', []))
            if row.get('snippet') or row.get('excerpt') or row.get('text')]


def execute(app, request, *, grant_owner=None, binding=None):
    mode = request['mode']
    command = from_chat(request['prompt']) if mode == 'chat' else None
    decision = decision_text(request['prompt']) if mode == 'chat' else request.get('approval_decision')
    possible_decision = bool(decision and app.action_approvals.has_binding(binding))
    if mode not in MODES and not command and not possible_decision:
        return None
    if grant_owner is None or binding is None:
        raise ValueError('Unlock with your owner passkey to use project tasks or idle research.')
    with grant_owner() as owner:
        pending = app.action_approvals.pending(owner=owner, binding=binding)
    if possible_decision and pending and pending['kind'] not in KINDS:
        return None
    if mode == 'approval_decision' and not pending:
        return None
    if possible_decision and pending:
        if request.get('session') != pending['session']:
            raise ValueError('Return to the conversation containing this proposal before deciding.')
        require_dispatch('ui.approval_decision')
        _verify(app, grant_owner, owner)
        draft = None
        if decision == 'request_changes':
            if pending['kind'] != 'project_edit':
                raise ValueError('Request changes is available only for an exact project edit.')
            original = pending['payload']['request']
            feedback = request.get('approval_feedback', '').strip()
            if not feedback or len(feedback) > 2000 or '\x00' in feedback:
                raise ValueError('Describe the requested changes in 1 to 2000 characters.')
            prompt = original['prompt'] + '\n\nRequested changes from the owner:\n' + feedback
            if len(prompt) > 4000:
                raise ValueError('The original goal and requested changes must fit within 4000 characters. Shorten the feedback.')
            draft = {key: original[key] for key in ('project', 'file', 'project_files', 'auto_files',
                     'specialist_review', 'preset', 'target') if key in original}
            draft.update(prompt=prompt, edit_policy='ask_first', research_query='', origin_approval=pending['id'])
        row = app.action_approvals.consume(owner=owner, binding=binding, session=request['session'],
            approval_id=pending['id'] if mode == 'chat' else request['approval_id'],
            fingerprint=pending['fingerprint'] if mode == 'chat' else request['approval_fingerprint'],
            policy_revision=revision(), decision=decision, feedback=request.get('approval_feedback', ''))
        if decision == 'request_changes':
            from .project_decision_outcomes import project_hash
            context = app.action_approvals.revision_context(owner=owner, binding=binding,
                session=row['session'], parent_row=row,
                project_hash=project_hash(ui_workspace.workspace(app, row['payload']['request'])))
            result = _result(app, request, {'reply': 'Changes requested. The old proposal is closed; no files were changed. '
                'Review the revised goal and prepare a fresh exact proposal.',
                'approval_resolved': row['id'], 'revision_draft': draft}, 'Task · changes requested', 'approval_decision')
            # The token is returned only to this live response, never saved as a draft grant.
            return {**result, 'details': {**result['details'], 'revision_draft': {**draft, **context}}}
        if decision == 'reject':
            return _result(app, request, {'reply': 'Denied. No proposed change was applied.',
                            'approval_resolved': row['id']}, 'Task · denied')
        payload = row['payload']
        original = {**payload['request'], 'session': row['session']}
        require_dispatch('ui.' + original['mode'])
        if row['kind'] == 'project_edit':
            return _apply(app, original, payload['candidate'], owner=owner, grant_owner=grant_owner,
                          resolved=row['id'], model=payload['model'], advisory=payload.get('advisory_review'),
                          specialists=payload.get('specialist_review'),
                          decision_link={'id': row['id'], 'fingerprint': row['fingerprint']})
        if row['kind'] == 'project_task':
            return _prepare(app, original, owner=owner, binding=binding, grant_owner=grant_owner,
                            delegated=original.get('edit_policy') == 'delegated', resolved=row['id'],
                            decision_link={'id': row['id'], 'fingerprint': row['fingerprint']})
        _verify(app, grant_owner, owner)
        from .capability_outcome_hooks import tracking, gap_notice
        with tracking(app.action_approvals, owner, row) as outcome:
            configured = research_store(app).configure(original['prompt'], original['enabled'],
                                          original.get('interval_minutes', 60), owner=owner)
            settings = configured['settings']
            observed = outcome.observe('settings_saved', evidence={'saved': True,
                'revision': settings['revision'], 'enabled': settings['enabled'],
                'interval_minutes': settings['interval_minutes']})
        return _result(app, original, {'reply': 'Idle public research is configured.' + gap_notice(observed),
            'approval_resolved': row['id'], 'idle_research': configured,
            **({'decision_outcome': observed} if observed is not None else {})},
            'Idle public research', 'idle_config')
    if mode == 'idle_status':
        return {'reply': 'Current idle public research settings and retained findings.',
                'details': {'idle_research': research_store(app).status()},
                'action': mode, 'label': 'Idle public research'}
    if mode == 'idle_config' and not request['enabled']:
        settings = research_store(app).status()['settings']
        if settings.get('owner'):
            research_store(app).configure(settings['topic'], False, settings['interval_minutes'], owner=owner)
        return _result(app, request, {'reply': 'Idle public research is disabled.',
            'idle_research': research_store(app).status()}, 'Idle public research · disabled', mode)
    if mode == 'idle_run':
        settings = research_store(app).status()['settings']
        if not settings.get('enabled') or settings.get('owner') != owner:
            raise ValueError('Approve an idle research topic before running it.')
        _verify(app, grant_owner, owner)
        return run_idle(app, force=True)
    if pending:
        raise ValueError('Approve or deny the pending proposal before starting a new task.')
    if mode == 'idle_config':
        review = {'action': 'Enable idle public research', 'reason': 'Keep cited observations for the topic you chose.',
            'benefit': 'Use relevant saved findings as evidence in later answers.',
            'effects': 'While the app is running and idle, send this topic to the public search provider and save '
                       'bounded source excerpts locally. This can consume provider credits. Research continues '
                       'after restart until disabled. Closing the browser tab keeps the app available for this research; '
                       'Close Prometheus stops it. Findings do not change model weights or execute instructions.',
            'resources': {'public_query': request['prompt'], 'interval_minutes': request.get('interval_minutes', 60)},
            'duration': 'Until you disable idle research'}
        return _review(app, request, owner, binding, 'idle_config', {'request': request}, review)
    if command:
        request = {**request, 'mode': 'project_edit', 'prompt': command}
    if not request.get('project', '').strip():
        raise ValueError('Open a development project before requesting an edit.')
    require_dispatch('ui.project_edit')
    workspace = ui_workspace.workspace(app, request)  # Validate selected root before approval.
    if request.get('revision_token'):
        from .project_decision_outcomes import project_hash
        app.action_approvals.validate_revision_context(owner=owner, binding=binding,
            session=request.get('session'), project_hash=project_hash(workspace),
            revision_token=request['revision_token'])
    if request.get('edit_policy') == 'delegated' or request.get('research_query', '').strip():
        return _review(app, request, owner, binding, 'project_task', {'request': request}, _task_review(request))
    return _prepare(app, request, owner=owner, binding=binding, grant_owner=grant_owner)
