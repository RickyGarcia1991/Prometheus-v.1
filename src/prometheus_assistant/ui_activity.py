"""Bounded, in-memory history of observable UI work; caller holds Interface.lock."""
from copy import deepcopy
from datetime import datetime, timezone
import time


LABELS = {
    'chat': 'Preparing a reply', 'coding': 'Preparing a code suggestion · no files changed',
    'ask': 'Reading selected evidence', 'vision': 'Inspecting the selected image',
    'project': 'Searching project files', 'documents': 'Searching your documents',
    'code_open': 'Inspecting project files', 'code_read': 'Reading a source file',
    'code_preview': 'Preparing a change preview · no files changed',
    'code_apply': 'Applying reviewed code changes', 'code_undo': 'Restoring the saved file version',
    'code_tests': 'Running project checks', 'document_import': 'Importing a document',
    'document_list': 'Reading the document list', 'document_index': 'Indexing document meanings',
    'check_sources': 'Checking saved sources', 'check_updates': 'Checking component updates',
    'backup': 'Creating and verifying a backup', 'restore_check': 'Checking backup restoration',
    'health_check': 'Checking application health', 'model_benchmark': 'Checking local helpers',
    'self_improve': 'Preparing an application change for review',
    'project_edit': 'Preparing a project edit', 'idle_config': 'Reviewing idle public research',
    'idle_run': 'Researching the approved public topic', 'idle_status': 'Reading saved research status',
    'approval_decision': 'Processing your reviewed decision',
    'scheduled_syntax_prepare': 'Comparing syntax-check destinations',
    'scheduled_syntax_tick': 'Checking approved waiting work',
    'scheduled_syntax_cancel': 'Cancelling waiting work',
    'scheduled_syntax_recover': 'Recovering the original syntax result',
    'evidence_followup_prepare': 'Preparing an evidence follow-up review',
    'evidence_followup_tick': 'Checking approved evidence follow-ups',
    'evidence_followup_cancel': 'Cancelling a waiting evidence follow-up',
    'evidence_followup_recover': 'Recovering the original follow-up result',
    'integration': 'Preparing an integration action', 'integration_access': 'Reviewing task access',
    'dictate': 'Preparing local recording', 'voice_prepare': 'Preparing voice · microphone off',
}


def task_label(request):
    mode = request['mode']
    return LABELS.get(mode, mode.replace('_', ' ').capitalize())


def outcome(action, result):
    state = result['state']
    if state != 'done':
        return state, 'Stopped' if state == 'cancelled' else 'Needs attention · see the task result'
    data = result.get('result') or {}
    details = data.get('details') or {}
    if details.get('approval'):
        return 'review', 'Waiting for your review'
    scheduled = details.get('scheduled_syntax')
    if scheduled:
        state = scheduled.get('state')
        if state == 'waiting': return 'review', 'Approved job waiting for resources'
        if state == 'unconfirmed': return 'error', 'Outcome unconfirmed · query the original result; no rerun'
        if state in {'cancelled','expired','rejected'}: return 'cancelled', 'Approved job ' + state
        evidence = (scheduled.get('result') or {}).get('evidence') or {}
        if state == 'failed' or evidence.get('status') == 'failed': return 'error', 'Syntax check failed'
        if state == 'completed': return 'done', 'Syntax check completed · see evidence'
    project = details.get('project_edit', {})
    if project:
        state = project.get('state', project.get('status'))
        if state in {'failed', 'conflict', 'cancelled'}:
            return ('cancelled' if state == 'cancelled' else 'error'), 'Project task ' + state + ' · see checks and undo'
        verification = project.get('verification') or {}
        if verification.get('status') == 'blocked':
            if (verification.get('council') or {}).get('status') == 'review_needed':
                return 'review', 'Reviewer concerns need attention · see project evidence'
            return 'error', 'Project verification needs attention · see evidence and undo'
        if state == 'applied_without_checks':
            return 'done', 'Edit applied · no supported checks available'
        return 'done', 'Project edit and checks passed' if state == 'passed' else 'No file changes needed'
    research = details.get('idle_research', {}).get('result', {})
    if research.get('state') in {'failed', 'cancelled'}:
        return ('error' if research['state'] == 'failed' else 'cancelled'), 'Idle research ' + research['state']
    if action == 'code_tests':
        if details.get('cancelled'):
            return 'cancelled', 'Project checks stopped'
        if details.get('timed_out'):
            return 'error', 'Project checks timed out'
        return ('done', 'Project checks passed') if details.get('exit_code') == 0 else ('error', 'Project checks failed')
    if action == 'code_preview':
        return ('review', 'Preview ready for review · no files changed') if details.get('changed') else ('done', 'Complete · no file changes needed')
    if action == 'code_apply':
        return 'done', 'Reviewed changes applied' if details.get('changed') else 'Complete · no file changes needed'
    if action == 'coding':
        return 'done', 'Code suggestion ready · no files changed'
    if action == 'check_sources' and any(row.get('error') for row in details.get('checks', [])):
        return 'error', 'Source checks need attention'
    if action == 'check_updates' and any(row.get('error') or row.get('status') in
            {'check_failed', 'rolled_back', 'cancelled'} for row in details.get('entries', [])):
        return 'error', 'Component checks need attention'
    if details.get('self_improvement', {}).get('state') in {'failed', 'error', 'cancelled'}:
        return 'error', 'Application change needs attention · see its result'
    followup = details.get('evidence_followup')
    if isinstance(followup, dict):
        status = followup.get('state')
        if status in {'failed', 'unconfirmed', 'incomplete', 'unrecorded'}:
            return 'error', 'Follow-up check failed' if status == 'failed' else 'Follow-up outcome unconfirmed · do not repeat execution'
        if status in {'rejected', 'cancelled', 'expired'}:
            return 'cancelled', 'Follow-up ' + status + ' · no new execution'
        if status == 'waiting':
            return 'review', 'Follow-up waiting on its approved destination'
        if status == 'completed':
            evidence = (followup.get('result') or {}).get('evidence') or {}
            if evidence.get('status') == 'passed':
                return 'done', 'Follow-up checks passed · original evidence retained'
            return 'error', 'Follow-up checks failed · see findings' if evidence.get('status') == 'failed' else 'Follow-up evidence unconfirmed · inspect the original result'
    return 'done', 'Completed · see the task result'


class ActivityHistory:
    def __init__(self):
        self.rows = {}
        self.started = {}
        self.revision = 0

    def start(self, ident, request, background=False):
        while len(self.rows) >= 12:
            oldest = next(iter(self.rows))
            del self.rows[oldest]
            self.started.pop(oldest, None)
        self.started[ident] = time.monotonic()
        # Do not retain prompts, file contents, command output, or error payloads.
        target = str(request.get('file') or (request.get('target', '') if request['mode'] in
                     {'code_tests', 'self_improve'} else '')).replace('\\', '/').rsplit('/', 1)[-1][:160]
        self.rows[ident] = {'id': ident, 'action': request['mode'], 'label': task_label(request),
            'target': target if request['mode'].startswith('code_') or request['mode'] == 'self_improve' else '',
            'background': background, 'state': 'running', 'stage': '', 'steps': [],
            'started_at': datetime.now(timezone.utc).isoformat(), 'seconds': 0.}
        self.progress(ident, task_label(request))

    def progress(self, ident, message):
        row = self.rows.get(ident)
        if not row or row['state'] != 'running':
            return
        message = str(message).strip()[:240]
        if not message or row['stage'] == message:
            return
        self.revision += 1
        row.update(stage=message, revision=self.revision)
        row['steps'].append({'message': message, 'seconds': self.elapsed(ident)})
        del row['steps'][:-24]

    def finish(self, ident, result):
        row = self.rows[ident]
        state, message = outcome(row['action'], result)
        self.progress(ident, message)
        self.revision += 1
        row.update(state=state, seconds=self.elapsed(ident), revision=self.revision)
        self.started.pop(ident, None)

    def elapsed(self, ident):
        if ident not in self.started:
            return self.rows[ident]['seconds']
        return round(max(0., time.monotonic() - self.started[ident]), 3)

    def snapshot(self, ident):
        if ident not in self.rows:
            return None
        return {**deepcopy(self.rows[ident]), 'seconds': self.elapsed(ident)}

    def recent(self):
        return [self.snapshot(ident) for ident in reversed(self.rows)]
