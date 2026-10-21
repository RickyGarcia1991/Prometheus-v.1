"""Model eligibility and context accounting for real project generation."""
import json
from types import SimpleNamespace
import threading

import pytest

from prometheus_assistant import project_edit_runtime as runtime
from prometheus_assistant.hardware import HardwareProfile, ModelProfile
from prometheus_assistant.coding_workspace import CodingWorkspace


def setup_runtime(tmp_path, monkeypatch, context=4096):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'main.py').write_text('x = 1\n')
    calls = []
    profile = ModelProfile('qwen2.5-coder:1.5b', 4, context, 'coding', 4)
    transport = SimpleNamespace(inventory=[{'name': profile.model}, {'name': 'remote-model', 'cloud': True}],
        inventory_reads=0, stream=None, ensure_error=None)
    class Client:
        def __init__(self, **kwargs):
            self.config = kwargs
        def local_models(self):
            transport.inventory_reads += 1
            if isinstance(transport.inventory, Exception):
                raise transport.inventory
            return transport.inventory() if callable(transport.inventory) else transport.inventory
        def resident_memory_gib(self):
            return 1.5
        def ensure_local_model(self):
            calls.append('verified local')
            if transport.ensure_error is not None:
                raise transport.ensure_error
        def chat_stream(self, messages, **kwargs):
            calls.append((self.config, messages, kwargs))
            if transport.stream is not None:
                return transport.stream(messages, kwargs), {}
            return '{"path":"main.py"}', {}
    monkeypatch.setattr(runtime, 'OllamaClient', Client)
    monkeypatch.setattr(runtime, 'detect_hardware', lambda: HardwareProfile(16, 8, 'Windows', 5))
    def candidates(goal, names, hardware, evidence, **kwargs):
        assert names == {profile.model}
        assert kwargs['task'] == 'coding'
        return 'coding', [profile]
    monkeypatch.setattr(runtime, 'candidates', candidates)
    app = SimpleNamespace(ensure_runtime=lambda: None, cancel_event=threading.Event(), progress=lambda *_: None,
                          model_evidence=SimpleNamespace(record_success=lambda *_: None, record_failure=lambda *_: None),
                          transport=transport)
    editor = runtime.editor(app, CodingWorkspace(project, tmp_path / 'history'), 'Change x')
    return editor, calls, app


def test_local_inventory_names_and_exact_schema_are_used(tmp_path, monkeypatch):
    editor, calls, app = setup_runtime(tmp_path, monkeypatch)
    schema = {'type': 'object'}
    messages = [{'role': 'user', 'content': 'Choose a file'}]
    assert editor.generate(messages, schema) == '{"path":"main.py"}'
    assert calls[0] == 'verified local'
    config, supplied, options = calls[1]
    assert supplied == messages and options['json_format'] == schema
    assert config['num_ctx'] == 4096 and options['cancel_event'].user is app.cancel_event
    assert options['num_predict'] >= 512


def test_small_fitting_context_can_generate_a_small_edit(tmp_path, monkeypatch):
    editor, calls, _ = setup_runtime(tmp_path, monkeypatch, context=2048)
    assert editor.context_bytes == 512
    editor.generate([{'role': 'user', 'content': 'x = 1'}], {'type': 'object'})
    assert calls


def test_overlong_input_stops_before_inference(tmp_path, monkeypatch):
    editor, calls, _ = setup_runtime(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='context'):
        editor.generate([{'role': 'user', 'content': '\u2603' * 2000}], {})
    assert not calls


def test_measured_project_helper_precedes_larger_unqualified_context(tmp_path, monkeypatch):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, 'candidates', lambda *args, **kwargs: ('coding', [
        ModelProfile('qwen2.5-coder:1.5b', 4, 8192, 'coding'),
        ModelProfile('qwen3:1.7b', 6, 3072, 'balanced')]))
    monkeypatch.setattr(runtime, 'grounded_profile', lambda profile, *_: profile)
    selected = runtime.editor(app, editor.workspace, 'Change x')
    assert selected.model == 'qwen3:1.7b'


def inventory(digest='a'*64):
    return [{'name': 'qwen2.5-coder:1.5b', 'digest': digest}]


def generate(editor):
    return editor.generate([{'role': 'user', 'content': 'Choose a file'}], {'type': 'object'})


def test_author_is_observed_before_and_after_each_generation(tmp_path, monkeypatch):
    editor, calls, app = setup_runtime(tmp_path, monkeypatch)
    assert editor.author_identity is None
    app.transport.inventory = inventory('sha256:'+'A'*64)
    reads = app.transport.inventory_reads
    for _ in range(2):
        assert generate(editor) == '{"path":"main.py"}'
        assert editor.author_identity == dict(provider='ollama-local', model=editor.model, artifact_sha256='a'*64)
    assert app.transport.inventory_reads == reads + 4
    assert len(calls) == 4  # Only the existing ensure/chat calls; no extra generation.


@pytest.mark.parametrize('rows', [[], [{'name': 'qwen2.5-coder:1.5b'}],
    inventory('not-a-digest'), inventory()+inventory(),
    [{'name': 'qwen2.5-coder:1.5b', 'digest': 'a'*64, 'cloud': True}],
    [{'name': 'qwen2.5-coder:1.5b', 'digest': 'a'*64, 'model': 'different'}],
    {'models': inventory()}, inventory()*257])
def test_missing_invalid_remote_or_ambiguous_provenance_does_not_fail_edit(tmp_path, monkeypatch, rows):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    app.transport.inventory = rows
    assert generate(editor) == '{"path":"main.py"}'
    assert editor.author_identity is None
    app.transport.inventory = inventory()
    assert generate(editor) == '{"path":"main.py"}'
    assert editor.author_identity is None  # Every call in this editor must be qualified.


@pytest.mark.parametrize('error', [runtime.LocalModelError('inventory offline'), OSError('read failed'),
    ValueError('malformed optional inventory'), TypeError('malformed inventory row')])
def test_optional_inventory_failure_preserves_generated_reply(tmp_path, monkeypatch, error):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    app.transport.inventory = error
    assert generate(editor) == '{"path":"main.py"}'
    assert editor.author_identity is None


@pytest.mark.parametrize('after', [inventory('b'*64), [], inventory()+inventory(), OSError('post read failed')])
def test_post_generation_change_or_missing_observation_removes_identity(tmp_path, monkeypatch, after):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    app.transport.inventory = inventory()
    def stream(*_):
        assert editor.author_identity is None
        app.transport.inventory = after
        return '{"path":"main.py"}'
    app.transport.stream = stream
    assert generate(editor) == '{"path":"main.py"}'
    assert editor.author_identity is None


def test_change_between_successful_generations_cannot_relabel_prior_work(tmp_path, monkeypatch):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    app.transport.inventory = inventory()
    generate(editor)
    assert editor.author_identity['artifact_sha256'] == 'a'*64
    app.transport.inventory = inventory('b'*64)
    generate(editor)
    assert editor.author_identity is None
    app.transport.inventory = inventory()
    generate(editor)
    assert editor.author_identity is None


@pytest.mark.parametrize('phase', ['context', 'ensure', 'stream'])
def test_failed_attempt_clears_prior_identity_and_never_restores_it(tmp_path, monkeypatch, phase):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    app.transport.inventory = inventory()
    generate(editor)
    assert editor.author_identity is not None
    if phase == 'context':
        with pytest.raises(ValueError, match='context'):
            editor.generate([{'role': 'user', 'content': '\u2603'*2000}], {})
    elif phase == 'ensure':
        app.transport.ensure_error = runtime.LocalModelError('ensure failed')
        with pytest.raises(runtime.LocalModelError): generate(editor)
        app.transport.ensure_error = None
    else:
        def fail(*_):
            assert editor.author_identity is None
            raise runtime.LocalModelError('generation failed')
        app.transport.stream = fail
        with pytest.raises(runtime.LocalModelError): generate(editor)
        app.transport.stream = None
    assert editor.author_identity is None
    generate(editor)
    assert editor.author_identity is None


def test_generated_claims_cannot_supply_author_identity(tmp_path, monkeypatch):
    editor, _, app = setup_runtime(tmp_path, monkeypatch)
    app.transport.inventory = inventory()
    forged = json.dumps({'author_identity': {'provider': 'forged', 'model': 'pretend', 'artifact_sha256': 'f'*64}})
    app.transport.stream = lambda *_: forged
    assert generate(editor) == forged
    assert editor.author_identity == dict(provider='ollama-local', model=editor.model, artifact_sha256='a'*64)
    editor.author_identity['artifact_sha256'] = 'f'*64
    generate(editor)
    assert editor.author_identity['artifact_sha256'] == 'a'*64  # Public dictionary is detached.


@pytest.mark.parametrize('changed_call', [None, 1, 2, 3])
def test_selection_and_every_group_member_share_one_observed_author(tmp_path, monkeypatch, changed_call):
    from prometheus_assistant.project_change_set import propose_group
    from prometheus_assistant.project_selection import select_files
    editor, _, app = setup_runtime(tmp_path, monkeypatch, context=8192)
    for name in ('a.txt', 'b.txt'):
        (editor.workspace.root/name).write_text('old\n')
    app.transport.inventory = inventory()
    calls = 0
    def stream(messages, _options):
        nonlocal calls
        calls += 1
        assert editor.author_identity is None
        if calls == changed_call:
            app.transport.inventory = inventory('b'*64)
        data = json.loads(messages[-1]['content'])
        if 'catalog' in data:
            return json.dumps({'files': ['a.txt', 'b.txt']})
        return json.dumps({'path': data['path'], 'reason': 'Requested update', 'benefit': 'Consistent files',
            'replacements': [{'old_text': 'old', 'new_text': 'new'}]})
    app.transport.stream = stream
    selected = select_files(editor, 'Update old to new')
    result = propose_group(editor, 'Update old to new', selected)
    assert calls == 3 and result['version'] == 2 and len(result['candidates']) == 2
    assert (editor.author_identity is not None) is (changed_call is None)
    assert not editor.workspace.history.exists()
    assert all((editor.workspace.root/name).read_text() == 'old\n' for name in selected)
