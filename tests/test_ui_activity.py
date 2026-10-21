"""Observable task history, independent of generated answers and private inputs."""
import json
import threading
import time

import pytest

from prometheus_assistant import ui_server as ui


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, 'hardware_status', lambda: {})
    instance = ui.Interface(tmp_path / 'state' / 'memory.sqlite3')
    yield instance
    instance.close()


def finished(app, job):
    for _ in range(200):
        with app.lock:
            if app.jobs[job['id']]['state'] != 'running':
                return
        time.sleep(.01)
    raise AssertionError('Task did not finish')


def test_activity_exposes_background_stages_and_retains_completion(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def execute(request):
        app.progress('Checking saved source versions')
        entered.set()
        assert release.wait(3)
        return {'reply': 'Private result', 'details': {'checks': []}}
    monkeypatch.setattr(app, 'execute', execute)
    job = app.submit({'mode': 'check_sources', 'prompt': ''}, background=True)
    try:
        assert entered.wait(2)
        row = app.status()['activity'][0]
        assert row['id'] == job['id'] and row['background'] is True
        assert row['state'] == 'running'
        assert row['stage'] == 'Checking saved source versions'
        assert row['seconds'] >= 0
        row['steps'].clear()  # A consumer cannot mutate the server's history.
    finally:
        release.set()
    finished(app, job)
    row = app.status()['activity'][0]
    assert row['state'] == 'done'
    assert any(step['message'] == 'Checking saved source versions' for step in row['steps'])
    assert 'Private result' not in json.dumps(row)


def test_real_code_preview_and_apply_report_distinct_file_effects(app, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    source = project / 'example.py'
    source.write_text('answer = 1\n', encoding='utf-8')
    import hashlib
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    request = {'project': str(project), 'file': 'example.py', 'content': 'answer = 2\n',
               'expected_sha256': expected, 'prompt': ''}
    preview = app.submit({**request, 'mode': 'code_preview'})
    finished(app, preview)
    row = app.status()['activity'][0]
    assert row['target'] == 'example.py'
    assert 'review' in row['stage'].lower() and 'no files changed' in row['stage'].lower()
    assert source.read_text() == 'answer = 1\n'
    applied = app.submit({**request, 'mode': 'code_apply'})
    finished(app, applied)
    row = app.status()['activity'][0]
    assert 'applied' in row['stage'].lower()
    assert source.read_text() == 'answer = 2\n'
    assert 'answer = 2' not in json.dumps(row)


def test_failed_tests_are_not_reported_as_success(app, monkeypatch):
    monkeypatch.setattr(app, 'execute', lambda request: {
        'reply': 'Project check failed', 'details': {'exit_code': 1}, 'action': 'code_tests'})
    job = app.submit({'mode': 'code_tests', 'prompt': '', 'project': 'unused'})
    finished(app, job)
    row = app.status()['activity'][0]
    assert row['state'] == 'error' and row['stage'] == 'Project checks failed'


@pytest.mark.parametrize('council_state,activity_state', [('review_needed', 'review'), ('blocked', 'error')])
def test_project_activity_preserves_unresolved_verification(council_state, activity_state):
    from prometheus_assistant.ui_activity import outcome
    state, stage = outcome('project_edit', {'state': 'done', 'result': {
        'details': {'project_edit': {'status': 'passed', 'verification': {
            'status': 'blocked', 'council': {'status': council_state}}}}}})
    assert state == activity_state
    assert 'attention' in stage.lower()
    assert stage != 'Project edit and checks passed'


def test_activity_omits_prompts_errors_and_generated_content(app, monkeypatch):
    def execute(request):
        app.append_partial('PRIVATE GENERATED CONTENT')
        raise RuntimeError('SECRET ERROR DETAIL')
    monkeypatch.setattr(app, 'execute', execute)
    job = app.submit({'mode': 'coding', 'prompt': 'PRIVATE USER PROMPT'})
    finished(app, job)
    row = app.status()['activity'][0]
    assert row['state'] == 'error'
    assert not any(secret in json.dumps(row) for secret in
                   ('PRIVATE GENERATED CONTENT', 'SECRET ERROR DETAIL', 'PRIVATE USER PROMPT'))


def test_recent_history_and_repeated_steps_are_bounded(app, monkeypatch):
    def execute(request):
        for i in range(50):
            app.progress(f'Step {i}')
            app.progress(f'Step {i}')
        return {'reply': 'done'}
    monkeypatch.setattr(app, 'execute', execute)
    for _ in range(14):
        job = app.submit({'mode': 'calculate', 'prompt': '2+2'})
        finished(app, job)
    rows = app.status()['activity']
    assert len(rows) <= 12
    assert len(rows[0]['steps']) <= 24
    messages = [step['message'] for step in rows[0]['steps']]
    assert all(a != b for a, b in zip(messages, messages[1:]))
