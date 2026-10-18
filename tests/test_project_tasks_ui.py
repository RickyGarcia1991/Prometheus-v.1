"""Real project workflow through owner-bound UI approval and scheduler routing."""
from contextlib import contextmanager, nullcontext
import threading
import time
from types import SimpleNamespace

import pytest

from prometheus_assistant import project_tasks_ui as tasks, ui_server as ui
from prometheus_assistant.project_editor import ProjectEditor
from prometheus_assistant.ollama import GenerationCancelled

OWNER, BINDING = 'a' * 64, 'b' * 64


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from prometheus_assistant import advisory_review_runtime
    monkeypatch.setattr(advisory_review_runtime, 'run_review', lambda *a, **k:
        advisory_review_runtime._result('unavailable', 'runtime_unavailable'))
    from prometheus_assistant import measured_review_runtime
    monkeypatch.setattr(measured_review_runtime, 'run_review', lambda *a, **k:
        dict(status='runtime_unavailable', attempted=False, report=None, measurement_identity=None, reviewer=None, prompt_version='measured-evidence-reference-v1', prompt_sha256=None, measurement_kind='native_process_outcomes', semantic_evidence_available=False, authorized=False, production_accepted=False))
    project = tmp_path / 'project'
    project.mkdir()
    file = project / 'main.py'
    file.write_text('def value():\n    return 1\n')
    app = ui.Interface(tmp_path / 'data/memory.sqlite3')
    app.project_resources.probe = SimpleNamespace(snapshot=lambda **kwargs: dict(
        available_ram_gib=8, total_ram_gib=16, free_disk_gib=20, cpu_percent=10,
        battery_percent=80, plugged_in=True, observed_at='fixture'))
    auth = {'valid': True, 'owner': OWNER}
    calls = []

    def editor(app, workspace, goal):
        def generate(messages, schema):
            calls.append(goal)
            return {'path': 'main.py', 'reason': 'The owner requested two.', 'benefit': 'Return the required value.',
                    'replacements': [{'old_text': 'return 1', 'new_text': 'return 2'}]}
        return ProjectEditor(workspace, generate, progress=app.progress)
    monkeypatch.setattr(tasks, 'editor', editor)
    monkeypatch.setattr(ui, 'reference_session', nullcontext)

    @contextmanager
    def owner():
        if not auth['valid']:
            raise ValueError('Owner expired')
        yield auth['owner']

    def run(body):
        return tasks.execute(app, ui.validate_job(body), grant_owner=owner, binding=BINDING)

    req = {'mode': 'project_edit', 'prompt': 'Return two.', 'project': str(project), 'file': 'main.py'}
    yield SimpleNamespace(app=app, auth=auth, calls=calls, owner=owner, run=run, req=req, file=file, project=project)
    app.close()


def decide(s, result, text='approve'):
    return s.run({'mode': 'chat', 'prompt': text, 'session': result['session']})


def advisory_fake(monkeypatch, *, risk='normal', callback=None):
    import json
    from prometheus_assistant import advisory_review_runtime
    from prometheus_assistant.advisory_review import prepare_review, qualify_review
    calls = []
    def review(app, candidate, files, **kwargs):
        calls.append((candidate, files))
        prepared = prepare_review(candidate, files, author=kwargs.get('author'),
            reviewer=dict(provider='ollama-local', model='reviewer', artifact_sha256='a'*64),
            prompt_version='fixture', prompt_sha256='b'*64)
        path = next(iter(files))
        report = qualify_review(candidate, files, prepared, json.dumps({'findings': [dict(
            file=path, line=1, category='correctness', risk=risk, confidence=.9,
            message='Check the boundary case.', reproducible=True)]}),
            invocation=dict(request_sha256=prepared['request_sha256'], status='completed', duration_seconds=.1))
        if callback:
            callback(app)
        return advisory_review_runtime._result('qualified_advisory', 'qualified', attempted=True, report=report)
    monkeypatch.setattr(advisory_review_runtime, 'run_review', review)
    return calls


def test_advisory_review_is_exact_retained_and_never_rerun_on_approval(setup, monkeypatch):
    s = setup
    calls = advisory_fake(monkeypatch)
    prepared = s.run(s.req)
    advice = prepared['details']['approval']['review']['advisory_review']
    assert advice['status'] == 'qualified_advisory'
    assert advice['report']['context']['artifact_relationship'] == 'unestablished'
    assert calls[0][1] == {'main.py': s.file.read_bytes().replace(b'return 1', b'return 2')}
    assert 'return 1' in s.file.read_text()
    result = decide(s, prepared)['details']['project_edit']
    assert result['advisory_review'] == advice and len(calls) == 1
    assert result['verification']['council']['findings'][0]['reproducible'] is False
    assert result['verification']['status'] == 'passed'
    assert result['verification']['authorized'] is False
    from prometheus_assistant.memory import MemoryStore
    with MemoryStore(s.app.memory_path) as memory:
        saved = memory.history(prepared['session'], include_metadata=True)[-1]
    assert saved['details']['project_edit']['advisory_review'] == advice
    assert len(calls) == 1


def test_advisory_snapshot_preserves_bom_and_crlf(setup, monkeypatch):
    s = setup
    s.file.write_bytes(b'\xef\xbb\xbfdef value():\r\n    return 1\r\n')
    calls = advisory_fake(monkeypatch)
    prepared = s.run(s.req)
    assert calls[0][1]['main.py'] == b'\xef\xbb\xbfdef value():\r\n    return 2\r\n'
    decide(s, prepared, 'deny')
    assert s.file.read_bytes().endswith(b'return 1\r\n')


def test_unavailable_advice_is_visible_without_claiming_a_review_pass(setup):
    s = setup
    prepared = s.run(s.req)
    advice = prepared['details']['approval']['review']['advisory_review']
    assert advice['status'] == 'unavailable' and advice['report'] is None
    result = decide(s, prepared)['details']['project_edit']
    assert result['advisory_review'] == advice


def test_stop_during_advice_creates_no_pending_approval_or_write(setup, monkeypatch):
    s = setup
    advisory_fake(monkeypatch, callback=lambda app: app.cancel_event.set())
    with pytest.raises(GenerationCancelled):
        s.run(s.req)
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None
    assert 'return 1' in s.file.read_text()


def test_high_risk_advice_requires_exact_review_for_delegated_task(setup, monkeypatch):
    s = setup
    advisory_fake(monkeypatch, risk='high')
    scope = s.run({**s.req, 'edit_policy': 'delegated'})
    exact = decide(s, scope)
    assert exact['details']['approval']['kind'] == 'project_edit'
    assert 'return 1' in s.file.read_text()
    result = decide(s, exact)['details']['project_edit']
    assert result['verification']['status'] == 'blocked'
    assert result['verification']['council']['status'] == 'review_needed'


def test_stale_advice_rejected_before_apply(setup, monkeypatch):
    s = setup
    advisory_fake(monkeypatch)
    prepared = s.run(s.req)
    # Simulate corruption of retained server data, never an accepted request field.
    pending = s.app.action_approvals._pending[(OWNER, BINDING)]
    pending['payload']['advisory_review']['report']['context']['candidate_sha256'] = '0'*64
    with pytest.raises(ValueError, match='advisory|review'):
        decide(s, prepared)
    assert 'return 1' in s.file.read_text()


def test_external_file_change_during_advice_requires_new_proposal(setup, monkeypatch):
    s = setup
    advisory_fake(monkeypatch, callback=lambda app: s.file.write_text('new owner work\n'))
    with pytest.raises(ValueError, match='changed'):
        s.run(s.req)
    assert s.file.read_text() == 'new owner work\n'
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_advice_cannot_override_failed_checks_or_rollback(setup, monkeypatch):
    s = setup
    calls = advisory_fake(monkeypatch)
    (s.project/'test_main.py').write_text('import unittest\nfrom main import value\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 1)\n')
    prepared = s.run({**s.req, 'preset': 'python-unittest', 'target': '.'})
    result = decide(s, prepared)['details']['project_edit']
    assert result['rollback']['undone'] and 'return 1' in s.file.read_text()
    assert result['verification']['status'] == 'blocked'
    assert result['advisory_review']['report']['findings'] and len(calls) == 1


def test_review_exposes_pending_evidence_then_actual_verified_checks(setup):
    s = setup
    prepared = s.run(s.req)
    review = prepared['details']['approval']['review']
    assert review['verification']['status'] == 'pending_checks'
    assert review['resource_assessment']['decision'] == 'run_local'
    assert 'return 1' in s.file.read_text()
    result = decide(s, prepared)['details']['project_edit']
    assert result['verification']['status'] == 'passed'
    assert result['resource_assessment']['dispatch_performed'] is False
    assert not s.app.project_resources.scheduler.leases


def test_actual_runner_counters_feed_following_review(setup):
    import os
    if os.name != 'nt':
        pytest.skip('Native Windows process counters')
    s = setup
    prepared = s.run(s.req)
    result = decide(s, prepared)['details']['project_edit']
    observed = result['resource_measurements']
    assert observed['recorded'] and observed['ram_gib'] > 0
    assert observed['cpu_seconds'] >= 0
    assert observed['history_samples'] == 1
    # A second proposal reads the saved workload sample even in a fresh service.
    from prometheus_assistant.project_resources import ProjectResources
    previous = s.app.project_resources
    s.app.project_resources = ProjectResources(previous.root, probe=previous.probe)
    s.file.write_text('def value():\n    return 1\n')
    second = s.run(s.req)['details']['approval']['review']
    assert second['resource_assessment']['history_samples'] == 1


def test_history_write_failure_preserves_success_and_undo(setup, monkeypatch):
    s = setup
    prepared = s.run(s.req)
    def fail(*args):
        raise OSError('read-only history')
    monkeypatch.setattr(s.app.project_resources, 'observe', fail)
    result = decide(s, prepared)['details']['project_edit']
    assert result['status'] == 'passed' and result['edit_id']
    assert result['resource_measurements']['recorded'] is False
    assert 'return 2' in s.file.read_text()


def test_reporting_failure_preserves_applied_change_and_undo(setup, monkeypatch):
    s = setup
    prepared = s.run(s.req)
    def fail(*args):
        raise ValueError('report unavailable')
    from prometheus_assistant import project_task_evidence
    monkeypatch.setattr(project_task_evidence, 'finish_evidence', fail)
    result = decide(s, prepared)['details']['project_edit']
    assert 'return 2' in s.file.read_text()
    assert result['edit_id']
    assert result['verification']['status'] == 'blocked'
    assert not s.app.project_resources.scheduler.leases


def test_resource_pressure_after_approval_review_prevents_writes(setup):
    s = setup
    prepared = s.run(s.req)
    s.app.project_resources.probe = SimpleNamespace(snapshot=lambda **kwargs: dict(
        available_ram_gib=.2, free_disk_gib=20, cpu_percent=10,
        battery_percent=80, plugged_in=True))
    with pytest.raises(ValueError, match='deferred before changing files'):
        decide(s, prepared)
    assert 'return 1' in s.file.read_text()
    assert not s.app.project_resources.scheduler.leases
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_resource_observations_are_in_application_status(setup):
    report = setup.app.status()['resource_status']
    assert report['observations']['available_ram_gib'] == 8
    assert report['policy_calibrated'] is False
    assert 'thermal events' in report['unmeasured']


def test_automatic_selection_routes_to_combined_exact_review(setup, monkeypatch):
    import json
    s = setup
    (s.project/'other.py').write_text('def value():\n    return 1\n')
    calls = []
    def factory(app, workspace, goal):
        def generate(messages, schema):
            payload = json.loads(messages[-1]['content'])
            calls.append(payload)
            if 'catalog' in payload:
                return {'files': ['main.py', 'other.py']}
            return {'path': payload['path'], 'reason': 'Owner request', 'benefit': 'Consistent values',
                    'replacements': [{'old_text': 'return 1', 'new_text': 'return 2'}]}
        return ProjectEditor(workspace, generate)
    monkeypatch.setattr(tasks, 'editor', factory)
    req = {**s.req, 'file': '', 'auto_files': True}
    review = s.run(req)
    assert len(calls) == 3
    assert 'other.py' in review['details']['approval']['review']['target']
    assert 'return 1' in s.file.read_text()
    result = decide(s, review)['details']['project_edit']
    assert result['status'] == 'passed'
    assert result['edit_id'].startswith('group-')


@pytest.mark.parametrize('extra', [{'edit_policy': 'delegated'}, {'project_files': ['bad']}, {'file':'main.py'}])
def test_auto_selection_cannot_expand_delegated_or_manual_scope(setup, extra):
    with pytest.raises(ValueError):
        setup.run({**setup.req, 'file': '', 'auto_files': True, **extra})


def test_combined_review_deny_apply_reopen_group_undo(setup, monkeypatch):
    import json
    from prometheus_assistant.project_change_set import undo_group, latest_group
    s = setup
    second = s.project / 'other.py'
    second.write_text('def value():\n    return 1\n')
    def factory(app, workspace, goal):
        def generate(messages, schema):
            path = json.loads(messages[-1]['content'])['path']
            return {'path': path, 'reason': 'Requested change', 'benefit': 'Both return two',
                'replacements': [{'old_text': 'return 1', 'new_text': 'return 2'}]}
        return ProjectEditor(workspace, generate)
    monkeypatch.setattr(tasks, 'editor', factory)
    req = {**s.req, 'project_files': 'main.py\nother.py'}
    review = s.run(req)
    diff = review['details']['approval']['review']['diff']
    assert 'a/main.py' in diff and 'a/other.py' in diff
    decide(s, review, 'deny')
    assert 'return 1' in s.file.read_text() and 'return 1' in second.read_text()
    result = decide(s, s.run(req))['details']['project_edit']
    assert result['status'] == 'passed'
    assert result['edit_id'].startswith('group-')
    ws = ui.ui_workspace.workspace(s.app, s.req)
    assert latest_group(ws, ws.latest_edit())['edit_id'] == result['edit_id']
    second.write_text('newer work')
    with pytest.raises(ValueError, match='changed'):
        undo_group(ws, result['edit_id'])
    assert 'return 2' in s.file.read_text()
    second.write_text('def value():\n    return 2\n')
    assert undo_group(ws, result['edit_id'])['undone']
    assert 'return 1' in s.file.read_text() and 'return 1' in second.read_text()


def test_ask_first_exact_diff_then_real_checks_and_undo(setup):
    s = setup
    review = s.run(s.req)
    approval = review['details']['approval']
    assert approval['kind'] == 'project_edit'
    assert '+    return 2' in approval['review']['diff']
    assert s.file.read_text().endswith('return 1\n')
    result = decide(s, review, 'allow')
    edit = result['details']['project_edit']
    assert edit['status'] == 'passed', edit
    assert edit['checks'][0]['exit_code'] == 0
    assert s.file.read_text().endswith('return 2\n')
    assert edit['project'] == str(s.project)
    reopened = ui.ui_workspace.workspace(s.app, s.req).describe()
    assert reopened['latest_edit'] == {'edit_id': edit['edit_id'], 'path': 'main.py', 'project': str(s.project)}
    undone = ui.ui_workspace.workspace(s.app, s.req).undo_edit(edit['edit_id'])
    assert undone['undone']
    assert s.file.read_text().endswith('return 1\n')
    assert ui.ui_workspace.workspace(s.app, s.req).describe()['latest_edit'] is None
    assert decide(s, review) is None


def test_deny_does_not_apply_and_delegated_waits_before_generation(setup):
    s = setup
    review = s.run({**s.req, 'edit_policy': 'delegated'})
    assert not s.calls
    assert review['details']['approval']['kind'] == 'project_task'
    assert 'Windows account' in review['details']['approval']['review']['effects']
    decide(s, review, 'deny')
    assert not s.calls
    review = s.run({**s.req, 'edit_policy': 'delegated'})
    result = decide(s, review)
    assert len(s.calls) == 1
    assert result['details']['project_edit']['status'] == 'passed'
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_owner_expiry_wrong_session_and_stale_file_prevent_apply(setup):
    s = setup
    review = s.run(s.req)
    with pytest.raises(ValueError, match='conversation'):
        s.run({'mode': 'chat', 'prompt': 'yes', 'session': 'c' * 32})
    s.auth['valid'] = False
    with pytest.raises(ValueError, match='expired'):
        decide(s, review)
    s.auth['valid'] = True
    s.file.write_text('later owner edit\n')
    with pytest.raises(ValueError):
        decide(s, review)
    assert s.file.read_text() == 'later owner edit\n'
    assert decide(s, review) is None


def test_research_only_after_scope_approval_then_exact_edit_review(setup, monkeypatch):
    s = setup
    searches = []
    monkeypatch.setattr(tasks, '_research', lambda query, app: searches.append(query) or
                        {'results': [{'title': 'Python', 'url': 'https://docs.python.org/3/', 'excerpt': 'Source excerpt'}]})
    review = s.run({**s.req, 'research_query': 'Python return statement'})
    assert not searches and not s.calls
    exact = decide(s, review)
    assert searches == ['Python return statement']
    assert exact['details']['approval']['kind'] == 'project_edit'
    assert s.file.read_text().endswith('return 1\n')
    assert decide(s, exact)['details']['project_edit']['status'] == 'passed'


def test_failed_behavioral_check_rolls_back(setup):
    s = setup
    test = s.project / 'test_main.py'
    test.write_text('import unittest\nfrom main import value\nclass Test(unittest.TestCase):\n'
                    '    def test_original(self):\n        self.assertEqual(value(), 1)\n')
    review = s.run(s.req)
    result = decide(s, review)['details']['project_edit']
    assert result['status'] == 'failed'
    assert result['rollback']['undone']
    assert s.file.read_text().endswith('return 1\n')


def test_approval_job_routes_to_project_adapter(setup):
    s = setup
    review = s.run(s.req)
    pending = review['details']['approval']
    ident = s.app.submit({'mode': 'approval_decision', 'approval_id': pending['id'],
            'approval_fingerprint': pending['fingerprint'], 'approval_decision': 'approve',
            'session': review['session']}, grant_owner=s.owner, approval_binding=BINDING)['id']
    s.app.executor.submit(lambda: None).result(timeout=10)
    result = s.app.jobs[ident]
    assert result['state'] == 'done', result
    assert result['result']['details']['project_edit']['status'] == 'passed'


def test_idle_config_review_run_recall_and_immediate_disable(setup, monkeypatch):
    s = setup
    calls = []
    monkeypatch.setattr('prometheus_assistant.firecrawl_search.search', lambda query, **kwargs:
        calls.append(query) or {'results': [{'url': 'https://docs.python.org/3/', 'title': 'Python testing',
                                           'excerpt': 'Use unittest to check Python behavior.'}]})
    review = s.run({'mode': 'idle_config', 'enabled': True, 'interval_minutes': 15, 'prompt': 'Python testing'})
    assert not tasks.research_store(s.app).status()['settings']['enabled']
    decide(s, review)
    result = s.run({'mode': 'idle_run'})
    assert result['details']['idle_research']['result']['state'] == 'complete'
    assert calls == ['Python testing']
    sources = tasks.recall_sources(s.app, 'Python testing')
    assert sources and sources[0]['location'] == 'https://docs.python.org/3/'
    assert 'unittest' in sources[0]['excerpt']
    # Exercise the real answer-provenance persistence contract. Research timestamps
    # are numeric on disk, but chat metadata requires bounded date text.
    from datetime import datetime
    from prometheus_assistant.compact_chat import grounded_messages, annotate_sources
    from prometheus_assistant.memory import MemoryStore
    assert datetime.fromisoformat(sources[0]['imported_at']).tzinfo is not None
    _, supplied = grounded_messages('Python testing', [], 4096, sources)
    reply, grounding = annotate_sources('Use unittest [S1].', supplied)
    with MemoryStore(s.app.memory_path) as memory:
        session = memory.create_session('fixture')
        memory.save_exchange(session, 'Python testing', reply, details={'grounding': grounding})
    with MemoryStore(s.app.memory_path) as memory:
        saved = memory.history(session, include_metadata=True)[-1]
        assert saved['content'] == reply
        assert saved['details']['grounding']['sources'] == supplied
    s.run({'mode': 'idle_config', 'enabled': False})
    assert not tasks.research_store(s.app).status()['settings']['enabled']
    assert tasks.recall_sources(s.app, 'Python testing')
    with pytest.raises(ValueError, match='Approve'):
        s.run({'mode': 'idle_run'})


def test_idle_scheduler_uses_only_approved_topic_after_inactivity(setup, monkeypatch):
    s = setup
    monkeypatch.setattr(ui.ui_workspace, 'schedule', lambda app: SimpleNamespace(status=lambda: {'watches': []}))
    monkeypatch.setattr(ui.ui_workspace, 'component_updates', lambda app: SimpleNamespace(status=lambda: {'due_count': 0}))
    queued = []
    monkeypatch.setattr(s.app, 'submit', lambda request, **kwargs: queued.append((request, kwargs)))
    s.app.next_maintenance = 0
    s.app.last_user_activity = time.monotonic() - 120
    s.app.idle_maintenance()
    assert not queued
    tasks.research_store(s.app).configure('Python testing', True, 15, owner=OWNER)
    s.app.next_maintenance = 0
    s.app.last_user_activity = time.monotonic()
    s.app.idle_maintenance()
    assert not queued
    s.app.next_maintenance = 0
    s.app.last_user_activity -= 120
    s.app.idle_maintenance()
    assert queued == [({'mode': 'idle_run', 'prompt': ''}, {'background': True})]


def test_idle_background_cannot_be_forged_by_http_fields(setup):
    with pytest.raises(ValueError):
        ui.validate_job({'mode': 'idle_run', 'background': True})
    with pytest.raises(ValueError, match='Unlock'):
        tasks.execute(setup.app, {'mode': 'idle_run', 'prompt': ''})
    with pytest.raises(ValueError, match='authenticated'):
        setup.app.execute({'mode': 'idle_run'})


def test_idle_research_lifetime_and_corrupt_notes_do_not_break_chat(setup):
    s = setup
    assert not s.app.idle_research_enabled()
    store = tasks.research_store(s.app)
    store.configure('Python testing', True, 15, owner=OWNER)
    assert s.app.idle_research_enabled()
    store.path.write_text('{bad state', encoding='utf-8')
    assert not s.app.idle_research_enabled()
    assert tasks.recall_sources(s.app, 'Python testing') == []


@pytest.mark.parametrize('with_sources,qwen_eligible', [(True, True), (False, True), (True, False)])
def test_saved_research_prefers_qualified_helper_without_expanding_eligibility(setup, monkeypatch,
                                                                            with_sources, qwen_eligible):
    from prometheus_assistant import compact_chat
    from prometheus_assistant.hardware import HardwareProfile, ModelProfile
    app = setup.app
    profiles = [ModelProfile('qwen2.5-coder:1.5b', 4, 4096, 'balanced', 2)]
    if qwen_eligible:
        profiles.append(ModelProfile('qwen3:1.7b', 6, 4096, 'balanced', 2))
    sources = [{'id': 'S1', 'title': 'Python unittest', 'location': 'https://docs.python.org/3/library/unittest.html',
                'excerpt': 'The unittest framework supports test automation.'}] if with_sources else []
    monkeypatch.setattr(tasks, 'recall_sources', lambda *args: sources)
    monkeypatch.setattr(app, 'ensure_runtime', lambda: None)
    monkeypatch.setattr(ui, 'detect_hardware', lambda: HardwareProfile(16, 8, 'Windows', 7))
    monkeypatch.setattr(ui, 'installed_models', lambda: {p.model for p in profiles})
    monkeypatch.setattr(ui.OllamaClient, 'resident_memory_gib', lambda *args: 0)
    monkeypatch.setattr(compact_chat, 'candidates', lambda *args, **kwargs: ('coding', profiles))
    captured = {}
    def generate(request, memory, selected, role, evidence, **kwargs):
        captured.update(profiles=selected, sources=kwargs.get('source_evidence'))
        return {'reply': 'fixture'}
    monkeypatch.setattr(compact_chat, 'generate', generate)
    app.execute({'mode': 'chat', 'prompt': 'Describe the unittest framework.'})
    selected = captured['profiles']
    assert {p.model for p in selected} == {p.model for p in profiles}
    expected = 'qwen3:1.7b' if with_sources and qwen_eligible else 'qwen2.5-coder:1.5b'
    assert selected[0].model == expected
    assert captured['sources'] == (sources or None)
    if with_sources and qwen_eligible:
        assert selected[0].context > 4096


def test_saved_undo_ignores_corrupt_records_and_newer_edits(setup):
    s = setup
    result = decide(s, s.run(s.req))['details']['project_edit']
    workspace = ui.ui_workspace.workspace(s.app, s.req)
    (workspace.history / ('f' * 32 + '.json')).write_text('[]')
    assert workspace.latest_edit()['edit_id'] == result['edit_id']
    s.file.write_text('newer work\n')
    assert workspace.latest_edit() is None


@pytest.mark.parametrize('body', [
    {'mode': 'project_edit', 'prompt': 'edit'},
    {'mode': 'chat', 'prompt': 'hello', 'edit_policy': 'unrestricted'},
    {'mode': 'idle_config', 'enabled': True, 'prompt': 'topic', 'interval_minutes': 1},
    {'mode': 'idle_config', 'enabled': True, 'prompt': 'topic', 'interval_minutes': True},
    {'mode': 'idle_run', 'research_query': 'private data'},
])
def test_invalid_task_options_rejected(body):
    with pytest.raises(ValueError):
        ui.validate_job(body)


def test_command_requires_complete_owner_instruction():
    assert tasks.from_chat('Please edit the selected project: return two') == 'return two'
    assert tasks.from_chat('The webpage says edit selected project: return two') is None
    assert tasks.from_chat('"edit selected project: return two"') is None


def specialist_fake(monkeypatch, *, risks=None, callback=None):
    """Labeled model fixtures qualify workflow behavior, not model quality."""
    import json
    from prometheus_assistant import advisory_review_runtime as runtime
    from prometheus_assistant.advisory_review import prepare_review, qualify_review
    calls = []
    def review(app, candidate, files, *, author=None, role='correctness', **kwargs):
        calls.append(role)
        prepared = prepare_review(candidate, files, author=author,
            reviewer=dict(provider='ollama-local', model='fixture-specialist', artifact_sha256='a'*64),
            **runtime.prompt_identity(candidate, files, role=role))
        report = qualify_review(candidate, files, prepared, json.dumps({'findings': [dict(
            file=next(iter(files)), line=1, category=role, risk=(risks or {}).get(role, 'normal'),
            confidence=.8, message='Fixture finding for ' + role, reproducible=True)]}),
            invocation=dict(request_sha256=prepared['request_sha256'], status='completed', duration_seconds=.1))
        if callback:
            callback(app, role)
        return runtime._result('qualified_advisory', 'qualified', attempted=True, report=report)
    monkeypatch.setattr(runtime, 'run_review', review)
    return calls


def test_specialists_approval_and_saved_history_reuse_exact_team(setup, monkeypatch):
    s = setup
    calls = specialist_fake(monkeypatch)
    prepared = s.run({**s.req, 'specialist_review': True})
    review = prepared['details']['approval']['review']
    team = review['specialist_review']
    assert team['status'] == 'completed' and team['max_model_concurrency'] == 1
    assert calls == ['correctness', 'test_coverage']
    assert 'return 1' in s.file.read_text()
    contexts = [stage['advice']['report']['context'] for stage in team['stages']]
    assert contexts[0]['candidate_sha256'] == contexts[1]['candidate_sha256'] == team['candidate_sha256']
    assert contexts[0]['files'] == contexts[1]['files'] == team['files']
    assert contexts[0]['prompt_sha256'] != contexts[1]['prompt_sha256']
    result = decide(s, prepared)['details']['project_edit']
    assert result['specialist_review'] == team
    assert result['verification']['authorized'] is False
    findings = result['verification']['council']['findings']
    assert {row['category'] for row in findings} == {'correctness', 'test_coverage'}
    assert all(row['reproducible'] is False for row in findings)
    from prometheus_assistant.memory import MemoryStore
    with MemoryStore(s.app.memory_path) as memory:
        saved = memory.history(prepared['session'], include_metadata=True)[-1]
    assert saved['details']['project_edit']['specialist_review'] == team
    assert calls == ['correctness', 'test_coverage']


def test_test_coverage_high_risk_requires_exact_review_for_delegated_task(setup, monkeypatch):
    s = setup
    calls = specialist_fake(monkeypatch, risks={'test_coverage': 'high'})
    scope = s.run({**s.req, 'specialist_review': True, 'edit_policy': 'delegated'})
    exact = decide(s, scope)
    assert exact['details']['approval']['kind'] == 'project_edit'
    assert 'return 1' in s.file.read_text()
    result = decide(s, exact)['details']['project_edit']
    assert result['verification']['status'] == 'blocked'
    assert calls == ['correctness', 'test_coverage']


def test_hardware_pressure_between_specialists_retains_partial_exact_review(setup, monkeypatch):
    s = setup
    def pressure(app, role):
        if role == 'correctness':
            app.project_resources.probe = SimpleNamespace(snapshot=lambda **kwargs: dict(
                available_ram_gib=.5, total_ram_gib=16, free_disk_gib=20, cpu_percent=10,
                battery_percent=80, plugged_in=True, observed_at='fixture'))
    calls = specialist_fake(monkeypatch, callback=pressure)
    scope = s.run({**s.req, 'specialist_review': True, 'edit_policy': 'delegated'})
    exact = decide(s, scope)
    review = exact['details']['approval']['review']
    assert review['specialist_review']['status'] == 'incomplete'
    assert review['specialist_review']['stages'][1]['status'] == 'deferred'
    assert exact['details']['approval']['kind'] == 'project_edit'
    assert calls == ['correctness'] and 'return 1' in s.file.read_text()
    decide(s, exact, 'deny')
    assert 'return 1' in s.file.read_text()


@pytest.mark.parametrize('change', ['stop', 'owner', 'source'])
def test_specialist_boundary_changes_prevent_second_stage_and_approval(setup, monkeypatch, change):
    s = setup
    def interrupt(app, role):
        if change == 'stop':
            app.cancel_event.set()
        elif change == 'owner':
            s.auth['owner'] = 'c' * 64
        else:
            s.file.write_text('new owner work\n')
    calls = specialist_fake(monkeypatch, callback=interrupt)
    with pytest.raises((ValueError, GenerationCancelled)):
        s.run({**s.req, 'specialist_review': True})
    assert calls == ['correctness']
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None
    assert 'return 2' not in s.file.read_text()


@pytest.mark.parametrize('corruption', ['receipt', 'missing', 'legacy_mismatch'])
def test_corrupt_retained_team_rejected_before_write(setup, monkeypatch, corruption):
    s = setup
    calls = specialist_fake(monkeypatch)
    prepared = s.run({**s.req, 'specialist_review': True})
    pending = s.app.action_approvals._pending[(OWNER, BINDING)]['payload']
    if corruption == 'receipt':
        pending['specialist_review']['receipt_sha256'] = '0' * 64
    elif corruption == 'missing':
        pending.pop('specialist_review')
    else:
        pending['advisory_review'] = pending['specialist_review']['stages'][1]['advice']
    with pytest.raises(ValueError):
        decide(s, prepared)
    assert 'return 1' in s.file.read_text() and len(calls) == 2


def test_specialist_agreement_cannot_override_failed_check_or_undo(setup, monkeypatch):
    s = setup
    calls = specialist_fake(monkeypatch)
    (s.project/'test_main.py').write_text('import unittest\nfrom main import value\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 1)\n')
    prepared = s.run({**s.req, 'specialist_review': True, 'preset': 'python-unittest', 'target': '.'})
    result = decide(s, prepared)['details']['project_edit']
    assert result['specialist_review']['status'] == 'completed'
    assert result['rollback']['undone'] and 'return 1' in s.file.read_text()
    assert result['verification']['status'] == 'blocked' and len(calls) == 2


@pytest.mark.parametrize('value', [1, 'yes', {}, {'authorized': True}, None])
def test_specialist_request_only_accepts_boolean(setup, value):
    with pytest.raises(ValueError, match='Specialist'):
        setup.run({**setup.req, 'specialist_review': value})


def test_specialist_request_not_accepted_for_idle_jobs():
    with pytest.raises(ValueError, match='Specialist'):
        ui.validate_job({'mode': 'idle_run', 'specialist_review': True})


def test_policy_change_between_specialists_prevents_second_stage(setup, monkeypatch):
    s = setup
    changed = {'denied': False}
    original = tasks.require_dispatch
    def dispatch(action):
        if changed['denied'] and action == 'ui.project_edit':
            raise ValueError('Project editing disabled by policy')
        return original(action)
    monkeypatch.setattr(tasks, 'require_dispatch', dispatch)
    calls = specialist_fake(monkeypatch, callback=lambda app, role: changed.update(denied=True))
    with pytest.raises(ValueError, match='disabled by policy'):
        s.run({**s.req, 'specialist_review': True})
    assert calls == ['correctness'] and 'return 1' in s.file.read_text()
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_policy_revision_change_with_dispatch_allowed_stops_specialists(setup, monkeypatch):
    s = setup
    state = {'revision': 'before'}
    monkeypatch.setattr(tasks, 'revision', lambda: state['revision'])
    calls = specialist_fake(monkeypatch, callback=lambda app, role: state.update(revision='after'))
    with pytest.raises(ValueError, match='policy changed'):
        s.run({**s.req, 'specialist_review': True})
    assert calls == ['correctness'] and 'return 1' in s.file.read_text()
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_post_check_review_retains_proposed_bytes_and_saved_result(setup, monkeypatch):
    from prometheus_assistant import measured_review_runtime
    from prometheus_assistant.memory import MemoryStore
    seen = []
    def review(app, candidate, packet):
        seen.append(packet)
        assert packet['files']['main.py'].splitlines()[-1].strip() == b'return 2'
        assert packet['complete'] is True
        return dict(status='runtime_unavailable', attempted=False, report=None, measurement_identity=None, reviewer=None, prompt_version='measured-evidence-reference-v1', prompt_sha256=None, measurement_kind='native_process_outcomes', semantic_evidence_available=False, authorized=False, production_accepted=False)
    monkeypatch.setattr(measured_review_runtime, 'run_review', review)
    prepared = setup.run(setup.req)
    assert not seen
    result = decide(setup, prepared)['details']['project_edit']
    assert len(seen) == 1 and result['status'] == 'passed'
    assert result['measured_review']['status'] == 'runtime_unavailable'
    with MemoryStore(setup.app.memory_path) as memory:
        saved = memory.history(prepared['session'], include_metadata=True)[-1]
    assert saved['details']['project_edit']['measured_review'] == result['measured_review']
    assert len(seen) == 1


def test_post_check_review_exception_preserves_task_and_undo(setup, monkeypatch):
    from prometheus_assistant import measured_review_runtime
    def broken(*a, **k):
        raise RuntimeError('fixture failure')
    monkeypatch.setattr(measured_review_runtime, 'run_review', broken)
    result = decide(setup, setup.run(setup.req))['details']['project_edit']
    assert result['status'] == 'passed'
    assert result['undo']['available']
    assert result['verification']['status'] == 'passed'
    assert result['measured_review']['status'] == 'runtime_unavailable'


def test_post_check_review_uses_failed_proposal_after_rollback(setup, monkeypatch):
    from prometheus_assistant import measured_review_runtime
    from prometheus_assistant.reviewer_check_evidence import assess
    observed = []
    def review(app, candidate, packet):
        assert 'return 1' in setup.file.read_text()
        assert b'return 2' in packet['files']['main.py']
        report = assess('{"findings":[]}', packet['evidence'], packet['files'])
        assert report['status'] == 'checks_failed'
        assert report['reviewer_omissions']
        observed.append(packet['run_id'])
        value = dict(status='runtime_unavailable', attempted=False, report=None, measurement_identity=None, reviewer=None, prompt_version='measured-evidence-reference-v1', prompt_sha256=None, measurement_kind='native_process_outcomes', semantic_evidence_available=False, authorized=False, production_accepted=False)
        value['report'] = report
        value['measurement_identity'] = measured_review_runtime._prepare(candidate, packet)[3]
        return value
    monkeypatch.setattr(measured_review_runtime, 'run_review', review)
    (setup.project/'test_main.py').write_text('import unittest\nfrom main import value\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 1)\n')
    prepared = setup.run({**setup.req, 'preset': 'python-unittest', 'target': '.'})
    result = decide(setup, prepared)['details']['project_edit']
    assert len(observed) == 1
    assert result['rollback']['undone']
    assert result['verification']['status'] == 'blocked'
    assert result['measured_review']['report']['status'] == 'checks_failed'


def test_optional_capture_limit_does_not_block_approved_edit(setup, monkeypatch):
    from prometheus_assistant import measured_review_runtime
    setup.file.write_text('def value():\n    return 1\n' + '# retained documentation\n' * 3000)
    def never(*a, **k):
        pytest.fail('Unavailable capture must not invoke a model')
    monkeypatch.setattr(measured_review_runtime, 'run_review', never)
    result = decide(setup, setup.run(setup.req))['details']['project_edit']
    assert result['status'] == 'passed' and result['checks']
    assert result['verification']['status'] == 'passed'
    assert result['measured_review']['status'] == 'runtime_unavailable'
    assert result['undo']['available']
