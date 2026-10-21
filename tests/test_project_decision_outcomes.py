"""Decision links come from executed checks, never reviewer claims."""
import pytest
import time
from test_project_tasks_ui import setup, decide, OWNER, BINDING
from prometheus_assistant import ui_workspace
from prometheus_assistant.action_approvals import ActionApprovals
from test_grants_http import endpoint


def linked(s, approval):
    row = next(row for row in s.app.action_approvals.history(owner=OWNER)
               if row['id'] == approval['id'])
    return row['outcomes']


def test_native_pass_and_exact_manual_undo_survive_reopen(setup):
    s = setup
    prepared = s.run(s.req)
    approval = prepared['details']['approval']
    result = decide(s, prepared)['details']['project_edit']
    events = linked(s, approval)
    assert [row['phase'] for row in events] == ['started', 'result']
    assert events[-1]['status'] == 'passed' and events[-1]['checks']['passed'] == 1
    assert events[-1]['edit_id'] == result['edit_id']
    assert 'main.py' not in str(events) and str(s.project) not in str(events)
    undo = ui_workspace.execute(s.app, dict(mode='code_undo', project=str(s.project), edit_id=result['edit_id']), decision_owner=OWNER)
    assert undo['details']['undone'] and 'return 1' in s.file.read_text()
    assert [row['phase'] for row in linked(s, approval)] == ['started', 'result', 'undo_started', 'undo_result']
    assert linked(s, approval)[-1]['status'] == 'restored'
    root = s.app.action_approvals.decision_history.root
    s.app.action_approvals = ActionApprovals(history_root=root)
    assert linked(s, approval)[-1]['status'] == 'restored'
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_failed_checks_stay_failed_after_automatic_rollback(setup):
    s = setup
    (s.project/'test_main.py').write_text('import unittest\nfrom main import value\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 1)\n')
    prepared = s.run(s.req | dict(preset='python-unittest', target='.'))
    result = decide(s, prepared)['details']['project_edit']
    terminal = linked(s, prepared['details']['approval'])[-1]
    assert result['status'] == terminal['status'] == 'failed'
    assert terminal['rollback'] == 'restored' and terminal['checks']['failed'] == 1
    assert terminal['checks']['passed'] == 1 and 'return 1' in s.file.read_text()


def test_native_result_precedes_optional_report_or_chat_save(setup, monkeypatch):
    s = setup
    prepared = s.run(s.req)
    def fail(*args, **kwargs):
        raise OSError('report failed')
    monkeypatch.setattr(s.app, 'save', fail)
    with pytest.raises(OSError, match='report failed'):
        decide(s, prepared)
    terminal = linked(s, prepared['details']['approval'])[-1]
    assert terminal['status'] == 'passed' and 'return 2' in s.file.read_text()


def test_start_storage_failure_prevents_edit(setup, monkeypatch):
    s = setup
    prepared = s.run(s.req)
    def fail(**kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(s.app.action_approvals, 'record_outcome', fail)
    with pytest.raises(OSError, match='disk unavailable'):
        decide(s, prepared)
    assert 'return 1' in s.file.read_text()


def test_terminal_storage_failure_preserves_actual_edit_and_reports_gap(setup, monkeypatch):
    s = setup
    prepared = s.run(s.req)
    original = s.app.action_approvals.record_outcome
    def record(**kwargs):
        if kwargs['event']['phase'] == 'result':
            raise OSError('no room')
        return original(**kwargs)
    monkeypatch.setattr(s.app.action_approvals, 'record_outcome', record)
    result = decide(s, prepared)['details']['project_edit']
    assert result['status'] == 'passed' and result['undo']['available']
    assert result['decision_outcome']['recorded'] is False
    assert [e['phase'] for e in linked(s, prepared['details']['approval'])] == ['started']


def test_undo_does_not_link_another_owner_and_audit_failure_cannot_block_restore(setup, monkeypatch):
    s = setup
    prepared = s.run(s.req)
    result = decide(s, prepared)['details']['project_edit']
    def fail(**kwargs):
        raise OSError('history offline')
    monkeypatch.setattr(s.app.action_approvals, 'record_outcome', fail)
    undone = ui_workspace.execute(s.app, dict(mode='code_undo', project=str(s.project), edit_id=result['edit_id']), decision_owner=OWNER)
    assert undone['details']['undone'] and 'return 1' in s.file.read_text()
    assert undone['details']['decision_outcome']['recorded'] is False
    assert s.app.action_approvals.history(owner='f'*64) == []


def test_second_owner_undo_does_not_append_first_owner_history(setup):
    s = setup
    prepared = s.run(s.req)
    result = decide(s, prepared)['details']['project_edit']
    original = s.app.action_approvals.history(owner=OWNER)
    undone = ui_workspace.execute(s.app, dict(mode='code_undo', project=str(s.project), edit_id=result['edit_id']), decision_owner='f'*64)
    assert undone['details']['undone']
    assert s.app.action_approvals.history(owner=OWNER) == original
    assert s.app.action_approvals.history(owner='f'*64) == []


def test_undo_through_real_worker_uses_authenticated_owner(setup):
    s = setup
    prepared = s.run(s.req)
    result = decide(s, prepared)['details']['project_edit']
    job = s.app.submit(dict(mode='code_undo', project=str(s.project), edit_id=result['edit_id']),
                       grant_owner=s.owner, approval_binding=BINDING)
    deadline = time.monotonic() + 5
    while s.app.jobs[job['id']]['state'] == 'running' and time.monotonic() < deadline:
        time.sleep(.01)
    assert s.app.jobs[job['id']]['state'] == 'done'
    assert linked(s, prepared['details']['approval'])[-1]['status'] == 'restored'


def test_delegated_edit_links_scope_approval(setup):
    s = setup
    prepared = s.run(s.req | dict(edit_policy='delegated'))
    assert prepared['details']['approval']['kind'] == 'project_task'
    result = decide(s, prepared)['details']['project_edit']
    assert result['status'] == linked(s, prepared['details']['approval'])[-1]['status'] == 'passed'


def test_recovery_journal_failure_does_not_erase_native_terminal(setup, monkeypatch):
    from prometheus_assistant import project_recovery
    s = setup
    prepared = s.run(s.req)
    def fail(*args):
        raise OSError('recovery finish unavailable')
    monkeypatch.setattr(project_recovery, 'finish', fail)
    with pytest.raises(OSError, match='recovery finish'):
        decide(s, prepared)
    assert linked(s, prepared['details']['approval'])[-1]['status'] == 'passed'
    assert 'return 2' in s.file.read_text()


def test_exception_without_native_result_is_unknown(setup, monkeypatch):
    from prometheus_assistant.project_editor import ProjectEditor
    s = setup
    prepared = s.run(s.req)
    def interrupted(*args, **kwargs):
        raise RuntimeError('interrupted at mutating boundary')
    monkeypatch.setattr(ProjectEditor, 'apply', interrupted)
    with pytest.raises(RuntimeError):
        decide(s, prepared)
    terminal = linked(s, prepared['details']['approval'])[-1]
    assert terminal['status'] == 'unknown' and terminal['checks'] is None
    assert terminal['evidence_sha256'] is None and terminal['edit_id'] == ''


def test_manual_undo_conflict_remains_failure_with_original_pass(setup):
    s = setup
    prepared = s.run(s.req)
    result = decide(s, prepared)['details']['project_edit']
    s.file.write_text('owner made newer changes\n')
    with pytest.raises(ValueError):
        ui_workspace.execute(s.app, dict(mode='code_undo', project=str(s.project), edit_id=result['edit_id']), decision_owner=OWNER)
    events = linked(s, prepared['details']['approval'])
    assert events[1]['status'] == 'passed' and events[-1]['status'] == 'restore_failed'
    assert s.file.read_text() == 'owner made newer changes\n'


def test_grouped_result_and_undo_use_exact_group_identity(setup, monkeypatch):
    from prometheus_assistant import project_tasks_ui
    from prometheus_assistant.project_editor import ProjectEditor
    s = setup
    (s.project/'extra.py').write_text('def value():\n    return 1\n')
    def editor(app, workspace, goal):
        def generate(messages, schema):
            import json
            payload = json.loads(messages[-1]['content'])
            return dict(path=payload['path'], reason='Change both files.', benefit='Consistent value.',
                replacements=[dict(old_text='return 1', new_text='return 2')])
        return ProjectEditor(workspace, generate)
    monkeypatch.setattr(project_tasks_ui, 'editor', editor)
    prepared = s.run(s.req | dict(file='', project_files='main.py\nextra.py'))
    result = decide(s, prepared)['details']['project_edit']
    terminal = linked(s, prepared['details']['approval'])[-1]
    assert terminal['edit_id'].startswith('group-') and terminal['checks']['passed'] == 2
    undone = ui_workspace.execute(s.app, dict(mode='code_undo', project=str(s.project), edit_id=result['edit_id']), decision_owner=OWNER)
    assert undone['details']['undone'] and 'return 1' in s.file.read_text()
    assert 'return 1' in (s.project/'extra.py').read_text()
    assert linked(s, prepared['details']['approval'])[-1]['status'] == 'restored'


@pytest.mark.parametrize('row,expected',[
    ({'passed':False,'exit_code':1}, 'failed'),
    ({'passed':True,'exit_code':0,'timed_out':True}, 'timed_out'),
    ({'passed':True,'exit_code':0,'cancelled':True}, 'cancelled'),
    ({'passed':True,'exit_code':0,'tests_run':0}, 'failed'),
    ({'passed':False,'executed':False,'error':'runner failed'}, None),
])
def test_native_summary_cannot_hide_failed_or_incomplete_checks(row, expected):
    from prometheus_assistant.project_decision_outcomes import native_summary
    summary = native_summary(dict(status='passed', checks=[row]), 1)
    assert summary['status'] == 'failed'
    if expected:
        assert summary['checks'][expected] == 1
    else:
        assert summary['checks']['executed'] == 0


def test_zero_checks_never_claims_passed():
    from prometheus_assistant.project_decision_outcomes import native_summary
    assert native_summary(dict(status='passed', checks=[]), 0)['status'] == 'applied_without_checks'


def test_legacy_direct_undo_does_not_require_decision_service():
    from prometheus_assistant.project_decision_outcomes import undo
    assert undo(object(), object(), None, 'e'*32, lambda: 'restored') == 'restored'
    assert undo(object(), object(), OWNER, 'recovery-'+'e'*32, lambda: 'restored') == 'restored'


def test_real_backup_restores_outcomes_without_restoring_authority(setup, tmp_path):
    from pathlib import Path
    from prometheus_assistant.maintenance import create_backup, restore_backup
    s = setup
    prepared = s.run(s.req)
    decide(s, prepared)
    before = s.app.action_approvals.history(owner=OWNER)
    backup = create_backup(s.app.memory_path, tmp_path/'backups',
        data_dirs={'Maintenance':s.app.data_root/'Maintenance'}, require_separate_volume=False)
    assert backup['verified'] and backup['source_unchanged']
    restored = restore_backup(backup['path'], tmp_path/'restore')
    assert restored['verified'] and not restored['live_data_overwritten']
    opened = ActionApprovals(history_root=Path(restored['path'])/'Maintenance/DecisionHistory')
    assert opened.history(owner=OWNER) == before
    assert opened.pending(owner=OWNER, binding=BINDING) is None


def test_outcome_http_is_owner_bound_and_read_only(endpoint):
    from prometheus_assistant.project_decision_outcomes import _event
    app, gate, effects, request = endpoint
    with gate.grant_owner('fixture-cookie') as owner:
        row = app.action_approvals.review(owner=owner, binding='b'*64, session='c'*32,
            kind='project_edit', payload={}, review={}, policy_revision='test')
        app.action_approvals.consume(owner=owner, binding='b'*64, session=row['session'],
            approval_id=row['id'], fingerprint=row['fingerprint'], policy_revision='test', decision='approve')
        app.action_approvals.record_outcome(owner=owner, approval_id=row['id'], fingerprint=row['fingerprint'],
            event=_event('started', 'd'*64))
    code, data = request('/api/decisions/history')
    assert code == 200 and data['read_only']
    assert data['items'][0]['outcomes'][0]['status'] == 'started'
    assert owner not in str(data) and not effects
    for args, expected in [({'owner':False}, 401), ({'token':False}, 403), ({'origin':'https://foreign.example'}, 403)]:
        assert request('/api/decisions/history', **args)[0] == expected
    assert request('/api/jobs', dict(mode='code_undo', project='.', edit_id='e'*32, decision_owner=owner))[0] == 400
    assert not effects
