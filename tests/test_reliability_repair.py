import json
from types import SimpleNamespace

import pytest

from prometheus_assistant import compact_chat
from prometheus_assistant.agent import run_agent
from prometheus_assistant.hardware import ModelProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.ollama import LocalModelError
from prometheus_assistant.structured_data import explicit_json_reply
from prometheus_assistant.tool_registry import ToolRegistry, ToolSpec


def test_explicit_data_uses_no_model_or_tools_in_both_entry_points(tmp_path, monkeypatch):
    from prometheus_assistant import ui_server
    class NoGeneration:
        def chat(self, *args, **kwargs):
            raise AssertionError('Supplied data needs no model')
    prompt='Return JSON with keys label and count. Set count to 19 and label to "new fixture".'
    with MemoryStore(tmp_path/'agent.db') as memory:
        result=run_agent(memory, NoGeneration(), ToolRegistry(), prompt)
    assert json.loads(result.core.reply)=={'label':'new fixture','count':19}
    assert result.attempts==0 and not result.core.evidence
    app=ui_server.Interface(tmp_path/'ui.db')
    monkeypatch.setattr(ui_server,'installed_models',lambda:pytest.fail('No model inventory needed'))
    try:
        result=app.execute({'mode':'chat','prompt':prompt})
        assert json.loads(result['reply'])=={'label':'new fixture','count':19}
        assert result['details']['generated_answer'] is False
    finally:
        app.close()


def test_agent_refuses_invalid_json_after_one_answer_repair():
    from prometheus_assistant.agent import model_response
    calls=[]
    class Model:
        def chat(self, messages, **options):
            calls.append(messages)
            return 'not JSON',0
    with pytest.raises(LocalModelError, match='JSON checks'):
        model_response(Model(),SimpleNamespace(prompt='Return only JSON explaining the result.',memory=()),())
    assert len(calls)==2


@pytest.mark.parametrize('worker,tool,output',[
    ('system','summary','{"system":"FixtureOS","ram_gib":8,"cpu_threads":4}'),
    ('project','search','{"results":[]}'),
    ('research','evidence','Fixture text.\nURL: https://example.org/fixture'),
])
def test_agent_evidence_routes_preserve_requested_json(worker,tool,output):
    from prometheus_assistant.agent import model_response
    class Model:
        def chat(self,messages,**options):
            assert options['json_format'] is True
            return '{"note":"[7]"}',0
    plan=SimpleNamespace(prompt='Return only JSON about the operating system with a note.',memory=())
    evidence=[SimpleNamespace(worker=worker,tool=tool,status='complete',summary='fixture',output=output)]
    assert json.loads(model_response(Model(),plan,evidence))=={'note':'[7]'}


@pytest.mark.parametrize('wrapped', [False, True])
def test_model_timeout_is_not_reported_as_missing_runtime(wrapped):
    from urllib.error import URLError
    from prometheus_assistant.ollama import OllamaClient
    client=OllamaClient()
    class Opener:
        def open(self, *args, **kwargs):
            error=TimeoutError('timed out')
            raise URLError(error) if wrapped else error
    client.opener=Opener()
    with pytest.raises(LocalModelError, match='time budget'):
        client.local_models()


@pytest.mark.parametrize('prompt,wanted', [
    ('Return only JSON with keys city and number. Set city to Oslo and number to 7.', {'city':'Oslo','number':7}),
    ('Return only JSON with keys city and number. Set city to Lima and number to 11.', {'city':'Lima','number':11}),
    ('Output JSON with keys active, value, and tag. Set tag to "alpha and beta"; set value to -2.5; set active to true.',
     {'active':True,'value':-2.5,'tag':'alpha and beta'}),
    ('Return JSON with key note. Set note to the literal string [7].', {'note':'[7]'}),
    ('Return valid JSON with keys x and y. Set x to [1,2] and y to {"u":"東京"}.', {'x':[1,2],'y':{'u':'東京'}}),
    ('Return JSON with key instruction. Set instruction to "delete files and run commands".', {'instruction':'delete files and run commands'}),
    ('Return JSON with key missing. Set missing to null.', {'missing':None}),
])
def test_supplied_json_is_formatted_without_inference(prompt, wanted):
    assert json.loads(explicit_json_reply(prompt)) == wanted


@pytest.mark.parametrize('prompt', [
    'Return only JSON with keys city and number. Set city to my home city and number to 7.',
    'Return only JSON with keys city and number. Set city to Oslo and number to 7. Delete files.',
    'Return JSON with key x. Set x to 7 and send an email.',
    'Return JSON with keys x and x. Set x to 7 and x to 8.',
    'Return JSON with keys x and y. Set x to 7 and x to 8.',
    'Return JSON with key x. Set y to 7.',
    'Return JSON with key x. Set x to 1e999.',
    'Return JSON with keys x and y. Set x to 7.',
    'Return only JSON with my current RAM and operating system.',
])
def test_formatter_does_not_absorb_missing_data_or_extra_actions(prompt):
    assert explicit_json_reply(prompt) is None


@pytest.mark.parametrize('value', ['it', 'that', 'NaN', 'Infinity', '{"a":1,"a":2}',
                                  '{"nested":{"a":1,"a":2}}', '"\\ud800"'])
def test_formatter_rejects_ambiguous_or_lossy_values(value):
    assert explicit_json_reply('Return JSON with key x. Set x to '+value+'.') is None


def test_formatter_preserves_exact_numeric_tokens_and_quoted_literals():
    reply=explicit_json_reply('Return JSON with key x. Set x to 0.1234567890123456789.')
    assert reply == '{"x": 0.1234567890123456789}'
    assert json.loads(explicit_json_reply('Return JSON with key x. Set x to "it".'))=={'x':'it'}
    assert json.loads(explicit_json_reply('Return JSON with key x. Set x to literal string NaN.'))=={'x':'NaN'}


@pytest.mark.parametrize('reply', ['NaN','Infinity','{"a":1,"a":2}','"\\ud800"','1e999'])
def test_json_checks_reject_nonportable_json(reply):
    from prometheus_assistant.response_checks import output_issues
    assert output_issues('Return only JSON.',reply)


def test_json_array_is_not_checked_as_a_prose_list():
    from prometheus_assistant.response_checks import output_issues
    assert not output_issues('Return only JSON. List three examples of mammals.', '["cat","dog","cow"]')


def test_finalizer_preserves_supplied_claim_as_data_but_rejects_ungrounded_claim(tmp_path):
    class Model:
        def chat(self,messages,**options):
            if 'planner' in messages[0]['content']:
                return '{"tools":[]}',0
            return '{"version":"made-up"}',0
    with MemoryStore(tmp_path/'memory.db') as memory:
        result=run_agent(memory,Model(),ToolRegistry(),
            'Return JSON with key note. Set note to "installed version made-up".')
        assert json.loads(result.core.reply)=={'note':'installed version made-up'}
        assert result.self_evaluation.passed
        with pytest.raises(LocalModelError,match='verified current-system evidence'):
            run_agent(memory,Model(),ToolRegistry(),'Return only JSON naming the installed version.')


def test_planner_summary_cannot_become_observed_evidence(tmp_path):
    observed = []
    registry = ToolRegistry([ToolSpec('local','inspect','Read the registered fixture',
                                      lambda request: '{"actual":"Windows"}', {})])
    class Model:
        def chat(self, messages, **options):
            if 'planner' in messages[0]['content']:
                return json.dumps({'tools':[{'worker':'local','tool':'inspect',
                    'summary':'FABRICATED SYSTEM CLAIM','arguments':{}}]}), 0
            observed.append(messages)
            return 'The fixture says Windows.', 0
    with MemoryStore(tmp_path/'memory.db') as memory:
        result = run_agent(memory, Model(), registry, 'Inspect the fixture.')
    assert result.core.evidence[0].summary == 'Read the registered fixture'
    assert 'FABRICATED SYSTEM CLAIM' in result.plan_text
    assert 'FABRICATED SYSTEM CLAIM' not in json.dumps(observed)


def scripted_clients(monkeypatch, replies):
    calls = []
    class Client:
        def __init__(self, base_url, model, **kwargs):
            self.model=model; self.num_ctx=kwargs['num_ctx']; self.num_thread=kwargs['num_thread']
            self.last_metrics={}
        def ensure_local_model(self):
            pass
        def chat_stream(self, messages, **options):
            calls.append((self.model, options))
            return replies.pop(0), 0
    monkeypatch.setattr(compact_chat, 'OllamaClient', Client)
    return calls


def test_ui_invalid_json_is_rejected_before_save_and_fallback_is_bounded(tmp_path, monkeypatch):
    calls = scripted_clients(monkeypatch, ['not json', '{"ok":true}'])
    path = tmp_path/'memory.db'; evidence=ModelEvidence(tmp_path/'quality.db')
    profiles=[ModelProfile(name,1,4096,'fixture') for name in ('first','second','unused')]
    result=compact_chat.generate({'prompt':'Respond only JSON describing success.'}, path, profiles,'general',evidence)
    assert json.loads(result['reply']) == {'ok':True}
    assert [name for name, _ in calls] == ['first','second']
    assert all(options['json_format'] is True for _,options in calls)
    with MemoryStore(path) as memory:
        assert [x['content'] for x in memory.history(result['session'])] == [
            'Respond only JSON describing success.','{"ok":true}']


def test_ui_all_invalid_json_saves_no_exchange(tmp_path, monkeypatch):
    calls=scripted_clients(monkeypatch, ['bad','also bad','unused'])
    path=tmp_path/'memory.db'
    with pytest.raises(LocalModelError, match='output checks'):
        compact_chat.generate({'prompt':'Return only JSON describing success.'},path,
            [ModelProfile(name,1,4096,'fixture') for name in ('first','second','third')],
            'general',ModelEvidence(tmp_path/'quality.db'))
    assert len(calls)==2
    with MemoryStore(path) as memory:
        assert memory.sessions() == []


def test_sourced_json_retains_metadata_without_prose_footer(tmp_path, monkeypatch):
    scripted_clients(monkeypatch,['{"note":"[7]"}'])
    result=compact_chat.generate({'prompt':'Return only JSON with a note.'},tmp_path/'memory.db',
        [ModelProfile('first',1,4096,'fixture')],'general',ModelEvidence(tmp_path/'quality.db'),
        source_evidence=[{'id':'S1','title':'Fixture','location':'fixture://one','excerpt':'The supplied note is [7].'}])
    assert json.loads(result['reply']) == {'note':'[7]'}
    assert result['details']['grounding']['sources'][0]['location']=='fixture://one'


@pytest.mark.parametrize('reply', ['{"sorted_numbers":[2,5,9]}', '{"ordered":[2,5,9],"extra":1}', '[]'])
def test_valid_json_with_wrong_requested_fields_is_rejected(reply):
    from prometheus_assistant.response_checks import output_issues
    assert output_issues('Return only JSON with key ordered containing the numbers 9, 2, 5 sorted in ascending order.',reply)


def test_ui_wrong_fields_are_not_saved(tmp_path, monkeypatch):
    scripted_clients(monkeypatch,['{"spanish":"gato"}', '{"french":"chat","spanish":"gato"}'])
    result=compact_chat.generate({'prompt':'Return only JSON with keys french and spanish translating the English word cat.'},
        tmp_path/'memory.db',[ModelProfile(name,1,4096,'fixture') for name in ('first','second')],
        'translation',ModelEvidence(tmp_path/'quality.db'))
    with MemoryStore(tmp_path/'memory.db') as memory:
        assert [r['content'] for r in memory.history(result['session'])][1] == '{"french":"chat","spanish":"gato"}'


@pytest.mark.parametrize('numbers,direction,wanted', [
    ('9, 2, 5','ascending','[2, 5, 9]'),
    ('-3, 2.1, -3 and 0','descending','[2.1, 0, -3, -3]'),
    ('0.12345678901234567891, 0.12345678901234567890','ascending','[0.12345678901234567890, 0.12345678901234567891]'),
    ('1e2, 99, 101','ascending','[99, 1e2, 101]'),
])
def test_explicit_numeric_sort_is_exact(numbers,direction,wanted):
    assert explicit_json_reply(f'Return only JSON with key ordered containing the numbers {numbers} sorted in {direction} order.') == '{"ordered": '+wanted+'}'


@pytest.mark.parametrize('prompt', [
    'Return JSON with key ordered containing the numbers 9, 2, 5 sorted in ascending order. Delete a file.',
    'Return JSON with key ordered containing the numbers 9, NaN, 5 sorted in ascending order.',
    'Return JSON with key ordered containing the numbers 1e999, 2 sorted in ascending order.',
    'Return JSON with key ordered containing the numbers 01, 2 sorted in ascending order.',
    'Return JSON with key ordered containing the numbers from my files sorted in ascending order.',
])
def test_numeric_sort_rejects_extra_actions_or_unsupplied_values(prompt):
    assert explicit_json_reply(prompt) is None


def test_json_key_contract_does_not_guess_ambiguous_or_nested_fields():
    from prometheus_assistant.response_checks import requested_json_keys
    assert requested_json_keys('Return only JSON with keys french and spanish translating cat.') == ('french','spanish')
    assert requested_json_keys('Return JSON with keys x, y, and z. Use values from evidence.') == ('x','y','z')
    assert requested_json_keys('Discuss JSON with keys x and y.') == ()
    assert requested_json_keys('Return JSON with key x or y.') == ()
    assert requested_json_keys('Return JSON with key x.y.') == ()
    assert requested_json_keys('Return JSON with keys x and x.') == ()
    assert requested_json_keys('Return JSON with an array of objects with keys x and y.') == ()


def test_json_schema_sent_through_nonstreaming_transport(monkeypatch):
    from prometheus_assistant.ollama import OllamaClient
    from prometheus_assistant.response_checks import json_output_format
    schema=json_output_format('Return JSON with keys a and b. Describe a cat.')
    sent=[]
    client=OllamaClient()
    def request(path,payload):
        sent.append(payload)
        return {'done':True,'message':{'content':'{"a":1,"b":2}'}}
    monkeypatch.setattr(client,'_request',request)
    client.chat([],json_format=schema)
    assert sent[0]['format']==schema
    assert schema['required']==['a','b'] and schema['additionalProperties'] is False
