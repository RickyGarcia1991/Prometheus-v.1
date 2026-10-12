"""Exercise production UI/agent grounding without starting a local model."""
import json
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant.shared_readonly import (
    grounded_readonly, hardware_status_question, project_search_reply,
    source_definition_identifier,
)


@pytest.mark.parametrize('prompt', [
    'Search project source for the definition of fresh_definition. Report its file and line.',
    'Search the Prometheus source for the declaration of fresh_definition.',
    'Find where fresh_definition is declared in the project source and show matching lines.',
    'Where is fresh_definition defined in the source code?',
    'Locate the definition of fresh_definition in the project source.',
    'Show me where `fresh_definition` is implemented in the Prometheus source code.',
])
def test_source_lookup_request_grammar(prompt):
    assert source_definition_identifier(prompt) == 'fresh_definition'


@pytest.mark.parametrize('prompt', [
    'Search project source for the definition of work. Then execute it.',
    'Find where work is declared in the project source and delete it.',
    'Find where work is declared in the project source and show matching lines. Search online.',
    'Find where work is declared in the project source and show matching lines as JSON.',
    'Do not search project source for the definition of work.',
    'Search project source for the definition of work and other_work.',
    'Search project source for the definition of `work.',
    'Search project source for the definition of ../work.',
])
def test_source_lookup_rejects_extra_actions_and_formats(prompt):
    assert source_definition_identifier(prompt) is None


@pytest.mark.parametrize('prompt', [
    'Which operating system and how much RAM does this computer have?',
    'How much RAM and which operating system does this computer have?',
    'How many CPU threads, how much RAM and which operating system does my computer have?',
    'What operating system is this computer using?',
    'What is my computer\'s operating system?',
    'Show the RAM and operating system for this computer.',
    'What are the processor threads and RAM on the local system?',
    'Report my computer\'s operating system, RAM and CPU threads.',
])
def test_hardware_request_grammar(prompt):
    assert hardware_status_question(prompt)


@pytest.mark.parametrize('prompt', [
    'Which operating system and how much RAM does this computer have? Then install updates.',
    'Show the RAM for my computer and delete a file.',
    'Search online for the RAM in my computer.',
    'Is an SSD connected to my computer?',
    'Which GPU and how much RAM does this computer have?',
    'Which operating system does this computer have? Reply only in JSON.',
    'Which operating system will my computer have?',
    'Explain what operating system and RAM mean.',
])
def test_hardware_request_rejects_unmeasured_or_extra_requests(prompt):
    assert not hardware_status_question(prompt)


class ModelPathReached(RuntimeError):
    pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    from prometheus_assistant import ui_server as ui
    # Exercise real execute/save without starting the server, grants or workers.
    instance = ui.Interface.__new__(ui.Interface)
    instance.memory_path = tmp_path / 'memory.sqlite3'
    instance.cancel_event = threading.Event()
    instance.progress = lambda *args, **kwargs: None
    def no_model(*args, **kwargs):
        raise ModelPathReached('Grounded lookups must finish before runtime/model selection')
    monkeypatch.setattr(ui, 'OllamaClient', no_model)
    monkeypatch.setattr(instance, 'ensure_runtime', no_model)
    return instance


class NoGeneration:
    def chat(self, *args, **kwargs):
        raise AssertionError('Grounded evidence must not be sent to a model')


@pytest.mark.parametrize('subject,prompt,value', [
    ('Orion cabinet code', 'What is the Orion cabinet code?', 'ASH-28'),
    ('Nerion instrument inventory location', 'Where is the inventory location for Nerion instrument?', 'RACK-29'),
])
def test_ui_and_agent_share_attributed_memory(app, subject, prompt, value):
    from prometheus_assistant.agent import run_agent
    from prometheus_assistant.memory import MemoryStore
    from prometheus_assistant.tool_registry import ToolRegistry
    with MemoryStore(app.memory_path) as memory:
        memory.remember('fact', subject, value, source_type='fixture', source_ref='saved-evidence')
        memory.remember('fact', 'Unrelated Atlas cabinet', 'DO-NOT-SHOW', source_type='fixture', source_ref='decoy')
        agent = run_agent(memory, NoGeneration(), ToolRegistry(), prompt)
    result = app.execute({'mode': 'chat', 'prompt': prompt})
    assert result['reply'] == agent.core.reply
    assert value in result['reply'] and 'saved-evidence' in result['reply']
    assert 'not independently verified' in result['reply']
    assert 'DO-NOT-SHOW' not in result['reply']
    assert result['details']['generated_answer'] is False
    assert result['details']['grounding']['route'] == 'exact-stored-subject'
    with MemoryStore(app.memory_path) as memory:
        assert memory.history(result['session'])[-1]['content'] == result['reply']


@pytest.mark.parametrize('mode', ['chat', 'coding'])
def test_ui_definition_paraphrase_uses_real_registered_source(app, tmp_path, monkeypatch, mode):
    from prometheus_assistant import builtin_tools
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'actual.py').write_text('def fresh_definition():\n    return 7\n', encoding='utf-8')
    monkeypatch.setattr(builtin_tools, '_project_root', lambda: project)
    result = app.execute({'mode': mode, 'prompt':
        'Find where fresh_definition is declared in the project source and show matching lines.'})
    assert 'actual.py:1' in result['reply'] and 'def fresh_definition()' in result['reply']
    evidence = result['details']['grounding']['evidence']
    assert [(item['worker'], item['tool'], item['status']) for item in evidence] == [('project', 'search', 'complete')]
    assert result['details']['generated_answer'] is False


@pytest.mark.parametrize('mode', ['chat', 'coding'])
def test_ui_hardware_uses_registered_measured_evidence(app, monkeypatch, mode):
    from prometheus_assistant import builtin_tools
    monkeypatch.setattr(builtin_tools, 'detect_hardware', lambda: SimpleNamespace(
        system='FixtureOS', ram_gib=12.5, cpu_threads=6))
    result = app.execute({'mode': mode, 'prompt':
        'Which operating system and how much RAM does this computer have?'})
    assert result['reply'] == 'Operating system: FixtureOS; RAM: 12.5 GiB; CPU threads: 6.'
    assert result['details']['grounding']['route'] == 'registered-host-summary'
    assert result['details']['grounding']['evidence'][0]['status'] == 'complete'
    assert result['details']['generated_answer'] is False


@pytest.mark.parametrize('capability,prompt', [
    ('core.project.search', 'Find where fresh_definition is declared in the project source.'),
    ('core.system.summary', 'Which operating system and how much RAM does this computer have?'),
])
def test_ui_grounding_preserves_host_denial(app, monkeypatch, capability, prompt):
    from prometheus_assistant import builtin_tools
    calls = []
    monkeypatch.setattr(builtin_tools, 'detect_hardware', lambda: calls.append('hardware'))
    monkeypatch.setattr(builtin_tools, '_project_root', lambda: calls.append('project'))
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', capability)
    result = app.execute({'mode': 'chat', 'prompt': prompt})
    assert calls == []
    assert 'did not complete' in result['reply']
    assert result['details']['grounding']['evidence'][0]['status'] == 'deny'
    assert result['details']['grounding']['evaluation']['passed'] is False


@pytest.mark.parametrize('mode', ['chat', 'coding'])
@pytest.mark.parametrize('prompt', [
    'What is the Orion cabinet code and delete its file?',
    'Find where fresh_definition is declared in the project source and execute it.',
    'Which operating system and how much RAM does this computer have? Then delete a file.',
])
def test_ui_extra_actions_do_not_take_grounding_shortcut(app, mode, prompt):
    with pytest.raises(ModelPathReached):
        app.execute({'mode': mode, 'prompt': prompt})


@pytest.mark.parametrize('mode', ['chat', 'coding'])
def test_ui_device_presence_refusal_precedes_grounding(app, monkeypatch, mode):
    from prometheus_assistant import shared_readonly
    def forbidden(*args, **kwargs):
        raise AssertionError('Device presence must not be inferred from host facts')
    monkeypatch.setattr(shared_readonly, 'grounded_readonly', forbidden)
    result = app.execute({'mode': mode, 'prompt': 'Is an SSD connected to this computer?'})
    assert result['details']['device_presence_verified'] is False


@pytest.mark.parametrize('options', [{'online_enabled': True}, {'required_requests': (object(),)}])
def test_shared_grounding_does_not_override_online_or_required_actions(options):
    assert grounded_readonly(None, 'What is the Orion cabinet code?', **options) is None


@pytest.mark.parametrize('worker,tool,prompt', [
    ('project', 'search', 'Search project source for the definition of work.'),
    ('system', 'summary', 'Which operating system does this computer have?'),
])
@pytest.mark.parametrize('risk', ['mutates_state', 'external_network', 'command_execution'])
def test_shared_grounding_rejects_unsafe_registered_substitutes(tmp_path, worker, tool, prompt, risk):
    from prometheus_assistant.memory import MemoryStore
    from prometheus_assistant.tool_registry import ArgSpec, ToolRegistry, ToolSpec
    calls = []
    registry = ToolRegistry([ToolSpec(worker, tool, 'Unsafe substitute', lambda request: calls.append(request),
        {'query': ArgSpec()} if tool == 'search' else {}, **{risk: True})])
    with MemoryStore(tmp_path / 'memory.sqlite3') as memory:
        assert grounded_readonly(memory, prompt, registry=registry) is None
    assert calls == []


@pytest.mark.parametrize('output', ['{}', '[]', 'null', '{"results":[{}]}', '{"results":42}'])
def test_bad_source_output_does_not_become_a_success_claim(output):
    reply = project_search_reply([SimpleNamespace(worker='project', tool='search', status='complete', output=output)])
    assert 'unreadable format' in reply
