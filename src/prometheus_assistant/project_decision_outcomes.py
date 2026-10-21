"""Link native project execution to an exact decision; never restore authority."""
import hashlib
import json
import os
import re
import secrets
import time


def project_hash(workspace):
    name = os.path.normcase(str(workspace.root.resolve()))
    return hashlib.sha256(name.encode('utf-8')).hexdigest()


def _event(phase, project, *, status='started', edit_id='', checks=None,
           rollback='unknown', evidence=None):
    return dict(event_id=secrets.token_hex(16), phase=phase, observed_at=time.time(),
        project_hash=project, status=status, edit_id=edit_id, checks=checks,
        rollback=rollback, evidence_sha256=(hashlib.sha256(json.dumps(evidence,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if evidence is not None else None))


def native_summary(result, planned):
    """Select fixed native facts; raw output, model advice and paths are excluded."""
    status = result.get('status')
    if status not in {'passed', 'failed', 'cancelled', 'unchanged', 'applied_without_checks'}:
        raise ValueError('Unrecognized native project outcome.')
    rows = result.get('checks', [])
    if type(rows) is not list or type(planned) is not int or not 0 <= len(rows) <= planned <= 64:
        raise ValueError('Invalid native check plan or results.')
    counts = dict(planned=planned, executed=0, passed=0, failed=0, cancelled=0, timed_out=0)
    incomplete = False
    for row in rows:
        if type(row) is not dict:
            raise ValueError('Invalid native check result.')
        executed = type(row.get('exit_code')) is int or row.get('executed') is True
        if not executed:
            incomplete = True
            continue
        counts['executed'] += 1
        category = ('cancelled' if row.get('cancelled') else 'timed_out' if row.get('timed_out') else
                    'passed' if row.get('passed') is True and row.get('exit_code') == 0
                    and row.get('tests_run') != 0 else 'failed')
        counts[category] += 1
    # A misleading aggregate can never hide native failures or missing checks.
    if status == 'passed' and (incomplete or counts['passed'] != planned):
        status = 'failed'
    if status == 'passed' and not planned:
        status = 'applied_without_checks'
    rollback = result.get('rollback', {})
    if type(rollback) is not dict:
        raise ValueError('Invalid native rollback result.')
    restored = ('restored' if rollback.get('undone') is True else 'needs_attention') if rollback.get('attempted') else 'not_attempted'
    edit_id = result.get('edit_id') or ''
    if type(edit_id) is not str or not re.fullmatch(r'(?:|[a-f0-9]{32}|group-[a-f0-9]{32})', edit_id):
        raise ValueError('Invalid native edit identity.')
    return dict(status=status, edit_id=edit_id, checks=counts, rollback=restored)


class ProjectOutcome:
    """Only created from a consumed server-side approval, not request input."""
    def __init__(self, app, workspace, owner, link):
        self.store = app.action_approvals
        self.project = project_hash(workspace)
        self.owner, self.link = owner, link
        self.enabled = link is not None and self.store.decision_history is not None
        self.finished = False

    def _record(self, event):
        return self.store.record_outcome(owner=self.owner, approval_id=self.link['id'],
            fingerprint=self.link['fingerprint'], event=event)

    def start(self):
        if self.enabled:
            # Failure here prevents any project mutation. The consumed decision
            # is still an audit receipt, never permission to retry the operation.
            self._record(_event('started', self.project))

    def finish(self, result, planned):
        if not self.enabled:
            return
        self.finished = True
        try:
            native = native_summary(result, planned)
            event = _event('result', self.project, **native, evidence=native)
            self._record(event)
            result['decision_outcome'] = dict(recorded=True, approval_id=self.link['id'],
                event_id=event['event_id'], evidence_sha256=event['evidence_sha256'])
        except Exception:
            # Never erase successful restoration, actual failure or the undo ID
            # merely because audit storage or an optional report is unavailable.
            result['decision_outcome'] = dict(recorded=False, approval_id=self.link['id'],
                error='Native decision outcome could not be saved. History remains unconfirmed.')

    def unknown(self):
        if self.enabled and not self.finished:
            try:
                self._record(_event('result', self.project, status='unknown'))
            except Exception:
                pass  # A durable start without a terminal result remains unknown.


def undo(app, workspace, owner, edit_id, perform):
    """Preserve explicit restoration even when its separate audit is unavailable."""
    if owner is None or not re.fullmatch(r'(?:group-)?[a-f0-9]{32}', edit_id):
        # Old direct calls and interrupted recovery IDs have no recorded native
        # edit association. Restoring them does not infer a decision linkage.
        return perform()
    store = app.action_approvals
    if store.decision_history is None:
        return perform()
    project = project_hash(workspace)
    links, started, gap = [], [], False
    try:
        links = store.outcomes_for_edit(owner=owner, project_hash=project, edit_id=edit_id)
        for link in links:
            store.record_outcome(owner=owner, **link,
                event=_event('undo_started', project, edit_id=edit_id))
            started.append(link)
    except Exception:
        gap = True
    def finish(restored):
        nonlocal gap
        native = dict(undone=restored)
        for link in started:
            try:
                store.record_outcome(owner=owner, **link, event=_event('undo_result', project,
                    status='restored' if restored else 'restore_failed', edit_id=edit_id,
                    rollback='restored' if restored else 'needs_attention', evidence=native))
            except Exception:
                gap = True
    try:
        result = perform()
    except Exception:
        finish(False)
        raise
    finish(result.get('undone') is True)
    if links or gap:
        result['decision_outcome'] = dict(recorded=not gap,
            error='Restoration audit unavailable; inspect the native result.' if gap else '',
            linked_decisions=len(started))
    return result
