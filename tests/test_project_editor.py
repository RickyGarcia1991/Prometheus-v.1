"""Behavioral coverage of proposal, reviewed execution and exact-byte recovery."""
import json
import threading

import pytest

from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.ollama import GenerationCancelled
from prometheus_assistant.project_editor import ProjectEditor, MAX_CONTEXT_BYTES


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'app.py').write_bytes(b'\xef\xbb\xbfdef greeting():\r\n    return "hello"\r\n')
    return CodingWorkspace(root, tmp_path / 'history', resources=tmp_path / 'resources')


def patch(old='"hello"', new='"welcome"', path='app.py', **extra):
    return {'path': path, 'reason': 'Use the greeting requested by the owner.',
            'benefit': 'Callers receive the updated welcome text.',
            'replacements': [{'old_text': old, 'new_text': new}], **extra}


def generator(value):
    def generate(messages, schema):
        return value
    return generate


def editor(workspace, value=None, **kwargs):
    return ProjectEditor(workspace, generator(patch() if value is None else value), **kwargs)


def unit_tests(workspace, expected='welcome', *, delay=False):
    tests = workspace.root / 'tests'
    tests.mkdir()
    (tests / 'test_app.py').write_text(
        'import unittest\nfrom app import greeting\n'
        'class Greeting(unittest.TestCase):\n'
        '    def test_greeting(self):\n'
        + ('        import time\n        print("test started", flush=True)\n        time.sleep(30)\n' if delay else '')
        + f'        self.assertEqual(greeting(), {expected!r})\n', encoding='utf-8')


def test_propose_is_read_only_and_exact_apply_checks_and_undo_persist(workspace):
    unit_tests(workspace)
    original = (workspace.root / 'app.py').read_bytes()
    stages, output = [], []
    engine = editor(workspace, progress=stages.append)
    candidate = engine.propose('Change the greeting to welcome', target='app.py')
    assert (workspace.root / 'app.py').read_bytes() == original
    assert not workspace.history.exists()
    assert candidate['checks'] == [
        {'preset': 'python-compile', 'target': 'app.py'},
        {'preset': 'python-unittest', 'target': 'tests'}]
    assert candidate['changed'] and '-    return "hello"' in candidate['diff']
    assert candidate['reason'] and candidate['benefit'] and candidate['context']['complete']
    json.dumps(candidate, allow_nan=False)
    result = engine.apply(candidate, on_output=output.append)
    assert result['status'] == 'passed' and result['changed']
    assert all(row['passed'] and row['executed'] for row in result['checks'])
    assert result['checks'][1]['tests_run'] == 1
    assert any('OK' in text for text in output)
    assert (workspace.root / 'app.py').read_bytes() == b'\xef\xbb\xbfdef greeting():\r\n    return "welcome"\r\n'
    assert result['undo']['available']
    reopened = CodingWorkspace(workspace.root, workspace.history.parent, resources=workspace.resources)
    assert reopened.undo_edit(result['edit_id'])['undone']
    assert (workspace.root / 'app.py').read_bytes() == original
    assert any('Running python-unittest' in stage for stage in stages)
    json.dumps(result, allow_nan=False)


def test_selects_existing_file_and_excludes_private_files_from_model(workspace):
    (workspace.root / '.env').write_text('TOKEN=private')
    (workspace.root / '.aws').mkdir()
    (workspace.root / '.aws' / 'credentials').write_text('secret')
    (workspace.root / 'README.md').write_text('A greeting application.')
    calls = []
    def generate(messages, schema):
        calls.append((messages, schema))
        if len(calls) == 1:
            payload = json.loads(messages[-1]['content'])
            assert {row['path'] for row in payload['files']} == {'app.py', 'README.md'}
            assert '.env' not in schema['properties']['path']['enum']
            return {'path': 'app.py'}
        return patch()
    candidate = ProjectEditor(workspace, generate).propose('Make greeting say welcome')
    assert candidate['path'] == 'app.py' and len(calls) == 2
    assert not workspace.history.exists()


@pytest.mark.parametrize('target', ['../outside.py', r'..\outside.py', r'C:\outside.py',
                                   '/outside.py', '.env', '.aws/credentials', 'app.py:stream'])
def test_target_escape_and_private_files_rejected_before_generation(workspace, target):
    def forbidden(*_args):
        raise AssertionError('Generator must not receive forbidden source')
    with pytest.raises((ValueError, OSError)):
        ProjectEditor(workspace, forbidden).propose('Change a file', target=target)
    assert not workspace.history.exists()


def test_model_cannot_select_outside_catalog(workspace):
    with pytest.raises(ValueError, match='outside'):
        editor(workspace, {'path': '../outside.py'}).propose('Update app')


@pytest.mark.parametrize('response, message', [
    ('```json\n{}\n```', 'valid replacement JSON'),
    ('{"path":"app.py","path":"other.py"}', 'duplicate JSON'),
    ('[]', 'JSON object'),
    ('{"path":NaN}', 'non-finite'),
    ({'path': 'app.py'}, 'selected project file'),
    (patch(path='other.py'), 'selected project file'),
    (patch(replacements=[]), 'one and twelve'),
    (patch(replacements=[{'old_text': '', 'new_text': 'x'}]), 'exactly one'),
    (patch(replacements=[{'old_text': 'missing', 'new_text': 'x'}]), 'exactly one'),
    (patch(replacements=[{'old_text': 'hello', 'new_text': 'x', 'command': 'echo no'}]), 'only old_text'),
    (patch(reason=''), 'Change reason'),
    (patch(benefit=''), 'Expected benefit'),
    (patch(command='echo no'), 'selected project file'),
])
def test_invalid_generation_never_writes(workspace, response, message):
    original = (workspace.root / 'app.py').read_bytes()
    with pytest.raises(ValueError, match=message):
        editor(workspace, response).propose('Change greeting', target='app.py')
    assert (workspace.root / 'app.py').read_bytes() == original
    assert not workspace.history.exists()


def test_ambiguous_and_overlapping_replacements_rejected(workspace):
    (workspace.root / 'app.py').write_text('a = "hello"\nb = "hello"\n')
    with pytest.raises(ValueError, match='exactly one'):
        editor(workspace).propose('Update greeting', target='app.py')
    value = patch(replacements=[{'old_text': 'a = "hello"', 'new_text': 'a = 1'},
                                {'old_text': '= "hello"\nb', 'new_text': '= 2\nb'}])
    with pytest.raises(ValueError, match='overlap'):
        editor(workspace, value).propose('Update greeting', target='app.py')


def test_multiple_replacements_use_original_spans_without_cascading(workspace):
    (workspace.root / 'app.py').write_text('first = "cat"\nsecond = "dog"\n')
    value = patch(replacements=[{'old_text': '"cat"', 'new_text': '"dog"'},
                                {'old_text': '"dog"', 'new_text': '"bird"'}])
    candidate = editor(workspace, value).propose('Update names', target='app.py')
    assert candidate['content'] == 'first = "dog"\nsecond = "bird"\n'


def test_useful_file_above_legacy_limit_has_complete_context(workspace):
    source = ''.join(f'SETTING_{i} = {i}\n' for i in range(400)) + '\nTIMEOUT_SECONDS = 30\n'
    (workspace.root / 'settings.py').write_text(source)
    def generate(messages, schema):
        payload = json.loads(messages[-1]['content'])
        assert payload['complete_source']
        assert payload['excerpts'][0]['source'] == source
        return patch('TIMEOUT_SECONDS = 30', 'TIMEOUT_SECONDS = 60', 'settings.py')
    candidate = ProjectEditor(workspace, generate).propose('Set timeout to 60 seconds', target='settings.py')
    assert candidate['context']['source_bytes'] > 1500
    assert 'TIMEOUT_SECONDS = 60' in candidate['content']


def test_large_file_uses_bounded_relevant_excerpts_and_retains_unseen_source(workspace):
    source = ''.join(f'VALUE_{i} = {i}\n' for i in range(6500))
    source += '\nSPECIAL_RETRY_INTERVAL = 15\n'
    (workspace.root / 'settings.py').write_text(source)
    def generate(messages, schema):
        payload = json.loads(messages[-1]['content'])
        assert not payload['complete_source']
        assert sum(len(item['source'].encode()) for item in payload['excerpts']) <= MAX_CONTEXT_BYTES
        assert any('SPECIAL_RETRY_INTERVAL' in item['source'] for item in payload['excerpts'])
        return patch('SPECIAL_RETRY_INTERVAL = 15', 'SPECIAL_RETRY_INTERVAL = 45', 'settings.py')
    candidate = ProjectEditor(workspace, generate).propose('Set SPECIAL_RETRY_INTERVAL to 45', target='settings.py')
    assert candidate['content'] == source.replace('SPECIAL_RETRY_INTERVAL = 15', 'SPECIAL_RETRY_INTERVAL = 45')
    assert not candidate['context']['complete']


def test_model_may_not_patch_unseen_source(workspace):
    source = ''.join(f'VALUE_{i} = {i}\n' for i in range(6500))
    (workspace.root / 'settings.py').write_text(source)
    def generate(messages, schema):
        payload = json.loads(messages[-1]['content'])
        assert not any('VALUE_6000 = 6000\n' in item['source'] for item in payload['excerpts'])
        return patch('VALUE_6000 = 6000\n', 'VALUE_6000 = 9000\n', 'settings.py')
    with pytest.raises(ValueError, match='outside the supplied excerpts'):
        ProjectEditor(workspace, generate).propose('Set VALUE_10', target='settings.py')


def test_oversize_file_output_and_research_rejected(workspace):
    (workspace.root / 'huge.py').write_text('x' * 256001)
    with pytest.raises(ValueError, match='limit'):
        editor(workspace).propose('Update file', target='huge.py')
    with pytest.raises(ValueError, match='bounded JSON'):
        editor(workspace, 'x' * 192001).propose('Update file', target='app.py')
    with pytest.raises(ValueError, match='research exceeds'):
        editor(workspace).propose('Update file', target='app.py', research='x' * 12001)


def test_source_change_during_generation_or_before_apply_preserves_new_work(workspace):
    def generate(messages, schema):
        (workspace.root / 'app.py').write_text('newer = 1\n')
        return patch()
    with pytest.raises(ValueError, match='changed since'):
        ProjectEditor(workspace, generate).propose('Update greeting', target='app.py')
    assert (workspace.root / 'app.py').read_text() == 'newer = 1\n'
    engine = editor(workspace, patch('newer = 1', 'newer = 2'))
    candidate = engine.propose('Update variable', target='app.py')
    (workspace.root / 'app.py').write_text('newest = 3\n')
    with pytest.raises(ValueError, match='changed since'):
        engine.apply(candidate)
    assert (workspace.root / 'app.py').read_text() == 'newest = 3\n'
    assert not workspace.history.exists()


@pytest.mark.parametrize('field,value', [
    ('project', 'C:/another-project'), ('checks', []),
    ('diff', 'fake diff'), ('content', 'arbitrary = 1\n'), ('after_sha256', '0' * 64),
    ('check_request', {'preset': 'shell', 'target': 'echo no'}),
])
def test_tampered_reviewed_candidate_rejected_before_write(workspace, field, value):
    engine = editor(workspace)
    candidate = engine.propose('Update greeting', target='app.py')
    candidate[field] = value
    with pytest.raises(ValueError):
        engine.apply(candidate)
    assert not workspace.history.exists()


def test_newly_discovered_tests_require_new_plan_review(workspace):
    engine = editor(workspace)
    candidate = engine.propose('Update greeting', target='app.py')
    unit_tests(workspace)
    with pytest.raises(ValueError, match='test plan changed'):
        engine.apply(candidate)
    assert not workspace.history.exists()


def test_syntax_failure_restores_exact_bytes(workspace):
    original = (workspace.root / 'app.py').read_bytes()
    engine = editor(workspace, patch('return "hello"', 'return ('))
    candidate = engine.propose('Change greeting', target='app.py')
    result = engine.apply(candidate)
    assert result['status'] == 'failed'
    assert result['checks'][0]['exit_code'] != 0
    assert 'SyntaxError' in result['checks'][0]['output']
    assert result['rollback']['undone'] and not result['changed']
    assert result['sha256'] == candidate['before_sha256']
    assert not result['undo']['available']
    assert (workspace.root / 'app.py').read_bytes() == original


def test_behavioral_failure_after_syntax_success_restores_exact_bytes(workspace):
    unit_tests(workspace, expected='required greeting')
    original = (workspace.root / 'app.py').read_bytes()
    engine = editor(workspace)
    result = engine.apply(engine.propose('Update greeting', target='app.py'))
    assert result['status'] == 'failed'
    assert result['checks'][0]['passed'] and not result['checks'][1]['passed']
    assert 'AssertionError' in result['checks'][1]['output']
    assert result['rollback']['undone']
    assert (workspace.root / 'app.py').read_bytes() == original


def test_empty_unittest_discovery_is_not_reported_as_success(workspace):
    tests = workspace.root / 'tests'
    tests.mkdir()
    (tests / 'test_nothing.py').write_text('import unittest\n')
    engine = editor(workspace)
    result = engine.apply(engine.propose('Update greeting', target='app.py'))
    assert result['status'] == 'failed'
    assert result['checks'][-1]['tests_run'] == 0
    assert result['rollback']['undone']


def test_running_test_cancellation_stops_process_and_restores_file(workspace):
    unit_tests(workspace, delay=True)
    original = (workspace.root / 'app.py').read_bytes()
    cancel = threading.Event()
    engine = editor(workspace)
    candidate = engine.propose('Update greeting', target='app.py')
    def output(text):
        if 'test started' in text:
            cancel.set()
    result = engine.apply(candidate, cancel_event=cancel, on_output=output)
    assert result['status'] == 'cancelled'
    assert result['checks'][-1]['cancelled']
    assert result['checks'][-1]['duration_seconds'] < 10
    assert result['rollback']['undone']
    assert (workspace.root / 'app.py').read_bytes() == original


def test_precancellation_and_cancel_during_generation_never_write(workspace):
    cancel = threading.Event()
    cancel.set()
    engine = editor(workspace)
    with pytest.raises(GenerationCancelled):
        engine.propose('Update greeting', target='app.py', cancel_event=cancel)
    cancel.clear()
    candidate = engine.propose('Update greeting', target='app.py')
    cancel.set()
    with pytest.raises(GenerationCancelled):
        engine.apply(candidate, cancel_event=cancel)
    cancel.clear()
    def generate(messages, schema):
        cancel.set()
        return patch()
    with pytest.raises(GenerationCancelled):
        ProjectEditor(workspace, generate).propose('Update greeting', target='app.py', cancel_event=cancel)
    assert not workspace.history.exists()


def test_check_error_preserves_newer_edits_and_retains_recovery_record(workspace, monkeypatch):
    engine = editor(workspace)
    candidate = engine.propose('Update greeting', target='app.py')
    def run(*_args, **_kwargs):
        (workspace.root / 'app.py').write_text('newer = "owner work"\n')
        raise OSError('Runtime became unavailable')
    monkeypatch.setattr(workspace, 'run_tests', run)
    result = engine.apply(candidate)
    assert result['status'] == 'failed' and result['changed']
    assert not result['checks'][0]['executed']
    assert not result['rollback']['undone'] and 'newer work' in result['rollback']['error']
    assert (workspace.root / 'app.py').read_text() == 'newer = "owner work"\n'
    assert (workspace.history / (result['edit_id'] + '.before')).is_file()


def test_post_check_change_is_reported_instead_of_false_success(workspace, monkeypatch):
    engine = editor(workspace)
    def run(*_args, **_kwargs):
        (workspace.root / 'app.py').write_text('another = 5\n')
        return {'exit_code': 0, 'output': '', 'cancelled': False, 'timed_out': False}
    monkeypatch.setattr(workspace, 'run_tests', run)
    result = engine.apply(engine.propose('Update greeting', target='app.py'))
    assert result['status'] == 'failed' and not result['rollback']['undone']
    assert 'newer work' in result['error']
    assert (workspace.root / 'app.py').read_text() == 'another = 5\n'


def test_explicit_check_plan_wins_over_detection_and_outside_test_target_is_rejected(workspace):
    unit_tests(workspace)
    engine = editor(workspace)
    candidate = engine.propose('Update greeting', target='app.py', preset='python-compile')
    assert candidate['checks'] == [{'preset': 'python-compile', 'target': 'app.py'}]
    with pytest.raises(ValueError, match='inside'):
        engine.propose('Update greeting', target='app.py', preset='python-pytest', test_target='../outside')
    with pytest.raises(ValueError, match='listed'):
        engine.propose('Update greeting', target='app.py', preset='shell', test_target='echo no')


def test_pytest_detection_runs_actual_suite(workspace):
    tests = workspace.root / 'tests'
    tests.mkdir()
    (tests / 'test_app.py').write_text('from app import greeting\ndef test_greeting():\n    assert greeting() == "welcome"\n')
    engine = editor(workspace)
    candidate = engine.propose('Update greeting', target='app.py')
    assert candidate['checks'][-1]['preset'] == 'python-pytest'
    result = engine.apply(candidate)
    assert result['status'] == 'passed', result
    assert '1 passed' in result['checks'][-1]['output']


def test_config_edit_is_useful_and_no_checks_is_explicit(workspace):
    (workspace.root / 'settings.toml').write_text('timeout_seconds = 30\n')
    engine = editor(workspace, patch('timeout_seconds = 30', 'timeout_seconds = 60', 'settings.toml'))
    candidate = engine.propose('Increase the timeout to 60 seconds', target='settings.toml')
    assert candidate['checks'] == [] and 'No supported project test suite' in candidate['check_reason']
    result = engine.apply(candidate)
    assert result['status'] == 'applied_without_checks' and result['undo']['available']
    assert (workspace.root / 'settings.toml').read_text() == 'timeout_seconds = 60\n'


def test_unchanged_proposal_does_not_write_or_claim_tests(workspace):
    engine = editor(workspace, patch('"hello"', '"hello"'))
    candidate = engine.propose('Keep the greeting hello', target='app.py')
    assert not candidate['changed']
    result = engine.apply(candidate)
    assert result['status'] == 'unchanged' and result['checks'] == []
    assert not workspace.history.exists()


def test_disconnected_progress_callback_does_not_prevent_recovery(workspace):
    def progress(_message):
        raise RuntimeError('Browser closed')
    engine = editor(workspace, patch('return "hello"', 'return ('), progress=progress)
    result = engine.apply(engine.propose('Update greeting', target='app.py'))
    assert result['status'] == 'failed' and result['rollback']['undone']


def test_tiny_model_context_is_bounded_and_can_target_a_relevant_span(workspace):
    source = ''.join(f'VALUE_{i} = {i}\n' for i in range(3000))
    source += 'RETRY_WAIT = 30\n'
    (workspace.root / 'settings.py').write_text(source)
    def generate(messages, schema):
        payload = json.loads(messages[-1]['content'])
        assert sum(len(item['source'].encode()) for item in payload['excerpts']) <= 512
        assert any('RETRY_WAIT = 30' in item['source'] for item in payload['excerpts'])
        return patch('RETRY_WAIT = 30', 'RETRY_WAIT = 60', 'settings.py')
    candidate = ProjectEditor(workspace, generate, context_bytes=512).propose('Set RETRY_WAIT to 60', target='settings.py')
    assert candidate['content'] == source.replace('RETRY_WAIT = 30', 'RETRY_WAIT = 60')


def test_newline_and_multibyte_context_budget_uses_actual_bytes(workspace):
    source = '# ' + '界' * 20000 + '\nlabel = "old"\n'
    (workspace.root / 'labels.py').write_text(source, encoding='utf-8')
    def generate(messages, schema):
        payload = json.loads(messages[-1]['content'])
        assert sum(len(item['source'].encode()) for item in payload['excerpts']) <= 512
        return patch('label = "old"', 'label = "new"', 'labels.py')
    candidate = ProjectEditor(workspace, generate, context_bytes=512).propose('Change label to new', target='labels.py')
    assert candidate['content'] == source.replace('label = "old"', 'label = "new"')


def test_node_test_plan_uses_recognized_existing_file_and_actual_node_when_available(workspace):
    if not next(row['available'] for row in workspace.test_presets() if row['id'] == 'node-test'):
        pytest.skip('Node runtime not installed on this host.')
    (workspace.root / 'settings.json').write_text('{"greeting":"hello"}\n')
    (workspace.root / 'greeting.test.cjs').write_text(
        "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
        "const settings = require('./settings.json');\n"
        "test('greeting', () => assert.equal(settings.greeting, 'welcome'));\n")
    engine = editor(workspace, patch('"hello"', '"welcome"', 'settings.json'))
    candidate = engine.propose('Update configured greeting', target='settings.json')
    assert candidate['checks'] == [{'preset': 'node-test', 'target': 'greeting.test.cjs'}]
    result = engine.apply(candidate)
    assert result['status'] == 'passed', result
    assert result['checks'][0]['tests_run'] == 1
