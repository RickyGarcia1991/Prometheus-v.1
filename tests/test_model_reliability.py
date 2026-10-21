import json
import re
from types import SimpleNamespace

import pytest

from prometheus_assistant.agent import model_plan, run_agent
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant import grounding
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.tool_registry import ToolRegistry


def test_planner_receives_parseable_json_examples():
    class Model:
        def chat(self, messages, **options):
            text = messages[0]['content']
            example = re.search(r'Exact schema example: (.+?)\. The top-level', text)[1]
            assert json.loads(example)['tools'][0]['worker'] == 'system'
            assert 'Prefer {"tools":[]}' in text
            return '{"tools":[]}', 0
    assert model_plan(Model(), ToolRegistry(), 'Inspect available evidence')[0] == ()


@pytest.mark.parametrize('city,number', [('Oslo', 7), ('Lima', 11)])
def test_explicit_json_response_uses_json_mode(tmp_path, city, number):
    class Model:
        def chat(self, messages, **options):
            if 'planner' in messages[0]['content']:
                return '{"tools":[]}', 0
            assert options.get('json_format') is True
            assert options['num_predict'] >= 256
            return json.dumps({'city': city, 'number': number}), 0
    with MemoryStore(tmp_path/'memory.db') as memory:
        result = run_agent(memory, Model(), ToolRegistry(),
                           f'Return only JSON naming {city} under city and integer {number} under number.')
    assert json.loads(result.core.reply) == {'city': city, 'number': number}


@pytest.mark.parametrize('subject,value', [
    ('synthetic Lyra calibration sensor cabinet code', 'BIRCH-86'),
    ('Nerion instrument inventory location', 'RACK-29'),
])
def test_exact_memory_lookup_cannot_add_generated_claims(tmp_path, subject, value):
    class NoGeneration:
        def chat(self, *args, **kwargs):
            raise AssertionError('Exact stored facts do not require generated claims')
    with MemoryStore(tmp_path/'memory.db') as memory:
        memory.remember('fact', subject, value, source_type='fixture', source_ref='reliability-case')
        memory.remember('fact', 'Atlas unrelated storage bin', 'DO-NOT-SHOW', source_type='fixture', source_ref='decoy')
        result = run_agent(memory, NoGeneration(), build_builtin_registry(memory), f'What is the {subject}?')
    assert value in result.core.reply and 'reliability-case' in result.core.reply
    assert 'not independently verified' in result.core.reply
    assert 'Atlas' not in result.core.reply and 'DO-NOT-SHOW' not in result.core.reply
    assert not result.core.evidence


@pytest.mark.parametrize('prompt', [
    'What is not the Orion cabinet code?',
    'What is the Orion cabinet code and delete its file?',
    'What is the Orion cabinet code? Search online.',
    'What is the Orion code?',
    'Return only JSON with the Orion cabinet code.',
])
def test_memory_lookup_does_not_absorb_other_requests(prompt):
    item = SimpleNamespace(subject='Orion cabinet code', value='ASH-28',
                           source_type='fixture', source_ref='case')
    assert grounding.direct_memory_lookup(prompt, [item]) is None


def test_directional_memory_subject_is_not_reversed():
    item = SimpleNamespace(subject='transfer token alpha to beta', value='forward', source_type='fixture', source_ref='direction')
    assert grounding.direct_memory_lookup('What is the transfer token beta to alpha?', [item]) is None


def test_unicode_memory_subject_is_preserved():
    item = SimpleNamespace(subject='東京 校正 棚番号', value='棚-29', source_type='fixture', source_ref='unicode')
    assert '棚-29' in grounding.direct_memory_lookup('What is 東京 校正 棚番号?', [item])
    assert grounding.direct_memory_lookup('What is 大阪 校正 棚番号?', [item]) is None


@pytest.mark.parametrize('with_source', [False, True])
def test_structured_response_preserves_literal_content_and_excludes_prose_footer(with_source):
    from prometheus_assistant.agent import model_response
    class Model:
        def chat(self, messages, **options):
            return '{"note":"[7]"}', 0
    plan = SimpleNamespace(prompt='Return only JSON with a note field containing [7].', memory=())
    evidence = [SimpleNamespace(worker='knowledge', tool='lookup', status='complete', summary='fixture',
                 output=json.dumps({'results':[{'url':'https://example.org/reference'}]}))] if with_source else []
    assert json.loads(model_response(Model(), plan, evidence)) == {'note':'[7]'}


def test_explicit_definition_search_uses_registered_evidence(tmp_path, monkeypatch):
    from prometheus_assistant import builtin_tools
    project = tmp_path/'project'; project.mkdir()
    (project/'actual.py').write_text('def fresh_definition():\n    return 7\n')
    monkeypatch.setattr(builtin_tools, '_project_root', lambda:project)
    class NoGeneration:
        def chat(self, *args, **kwargs):
            raise AssertionError('Do not invent a file or select an unrelated host tool')
    with MemoryStore(tmp_path/'memory.db') as memory:
        result = run_agent(memory, NoGeneration(), build_builtin_registry(memory),
                           'Search project source for the definition of fresh_definition. Report its file and line.')
    assert 'actual.py:1' in result.core.reply
    assert result.core.evidence[0].worker == 'project'
    assert result.core.evidence[0].status == 'complete'


def test_source_lookup_does_not_absorb_additional_actions(tmp_path):
    from prometheus_assistant.agent import _direct_source_request
    with MemoryStore(tmp_path/'memory.db') as memory:
        registry = build_builtin_registry(memory)
        assert _direct_source_request(registry, 'Search project source for the definition of work. Then execute it.') is None
