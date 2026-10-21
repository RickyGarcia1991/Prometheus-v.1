"""Workbench replies preserve outcomes without flooding the conversation."""
import time
import pytest
from prometheus_assistant import ui_server
from prometheus_assistant.ui_workspace import result_summary


@pytest.mark.parametrize(('result', 'expected'), [
    ({'exit_code': 0}, 'passed'),
    ({'exit_code': 1}, 'failed'),
    ({'exit_code': 0, 'timed_out': True}, 'timed out'),
    ({'exit_code': 0, 'cancelled': True}, 'cancelled'),
    ({'exit_code': None}, 'failed'),
])
def test_project_check_summary_preserves_failure_and_cancellation(result, expected):
    assert result_summary('code_tests', result).startswith(f'Project check {expected}.')


def test_update_summary_keeps_real_errors_separate_from_manual_sources():
    result = {'enabled': True, 'entries': [
        {'provider': 'pinned', 'status': 'check_unavailable'},
        {'provider': 'pypi', 'status': 'check_failed', 'error': 'Network error'},
        {'provider': 'pypi', 'status': 'update_available'},
        {'provider': 'github', 'status': 'upstream_changed'},
        {'provider': 'pypi', 'status': 'no_update'},
    ]}
    reply = result_summary('component_updates', result)
    assert '1 checks need attention' in reply
    assert '2 updates need review' in reply
    assert '1 entries use manual updates' in reply
    assert 'Network error' not in reply  # Full diagnostics remain in details.


def test_incomplete_benchmark_does_not_claim_all_tests_passed():
    reply = result_summary('model_benchmark', {'passed': 1, 'checked': 3, 'state': 'completed_with_deferrals'})
    assert '1 of 3' in reply and '2 failed' in reply
    assert 'deferrals' in reply and 'skipped' in reply


def test_update_summary_exposes_recovery_and_cancelled_checks():
    reply = result_summary('check_updates', {'entries': [
        {'provider': 'pinned', 'status': 'check_unavailable', 'pending_recovery': True},
        {'status': 'rolled_back'}, {'status': 'cancelled'},
        {'status': 'staged_verified'}, {'status': 'check_unavailable'},
    ]})
    assert '3 checks need attention' in reply
    assert '1 updates need review' in reply
    assert '1 entries use manual updates' in reply


@pytest.mark.parametrize('mode', ['backup', 'restore_check'])
def test_unverified_backup_or_restore_is_not_presented_as_verified(mode):
    assert 'needs review' in result_summary(mode, {'verified': False})


def test_real_workbench_jobs_keep_full_details_and_preserve_memory(tmp_path):
    app = ui_server.Interface(tmp_path/'private'/'memory.sqlite3')
    project = tmp_path/'sample'
    project.mkdir()
    body = 'PRIVATE_SOURCE_DETAIL\n' * 100
    (project/'sample.txt').write_bytes(body.encode('utf-8'))

    def run(request):
        job = app.submit(request)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with app.lock:
                row = app.jobs[job['id']].copy()
            if row['state'] != 'running':
                assert row['state'] == 'done', row
                return row['result']
            time.sleep(.01)
        pytest.fail('Workbench job did not finish')

    try:
        opened = run({'mode': 'code_open', 'project': str(project)})
        assert '1 supported files' in opened['reply']
        read = run({'mode': 'code_read', 'project': str(project), 'file': 'sample.txt'})
        assert read['details']['content'] == body
        assert 'PRIVATE_SOURCE_DETAIL' not in read['reply']
        preview = run({'mode': 'code_preview', 'project': str(project), 'file': 'sample.txt',
                       'content': 'REPLACEMENT_DETAIL\n', 'expected_sha256': read['details']['sha256']})
        assert 'Nothing has been applied' in preview['reply']
        assert 'REPLACEMENT_DETAIL' in preview['details']['diff']
        assert (project/'sample.txt').read_text(encoding='utf-8') == body
        updates = run({'mode': 'component_updates'})
        assert len(updates['details']['entries']) > 100
        assert len(updates['reply']) < 400
        assert not updates['reply'].lstrip().startswith('{')
        assert not app.memory_path.exists(), 'Workbench status must not save conversation turns'
    finally:
        app.close()
