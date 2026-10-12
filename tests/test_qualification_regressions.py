import json
from prometheus_assistant.agent import run_agent
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant import builtin_tools


class NoGeneration:
    def chat(self,*a,**k):raise AssertionError('Device enumeration is not a language-model capability.')


def test_unobserved_device_presence_is_explicitly_unknown_agent_and_ui(tmp_path):
    from prometheus_assistant.ui_server import Interface
    prompt='Is an external USB microscope attached to this computer?'
    with MemoryStore(tmp_path/'agent.db') as memory:
        result=run_agent(memory,NoGeneration(),build_builtin_registry(memory),prompt)
        assert 'cannot determine' in result.core.reply
        assert not result.core.evidence
    app=Interface(tmp_path/'ui.db')
    try:
        result=app.execute({'mode':'chat','prompt':prompt})
        assert 'cannot determine' in result['reply']
        assert result['details']['generated_answer'] is False
        assert result['details']['device_presence_verified'] is False
    finally:app.close()


def test_definition_precedes_imports_mentions_and_calls(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir()
    (root/'a.md').write_text('dispatch_exact is a useful function.\n'*8)
    (root/'b.py').write_text('from module import dispatch_exact\nresult=dispatch_exact()\n')
    (root/'z.py').write_text('def dispatch_exact(request):\n    return request\n')
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:root)
    spec=build_builtin_registry(None).get('project','search')
    data=json.loads(spec.executor(spec.request('find definition',{'query':'dispatch_exact'})))
    assert data['results'][0]['path']=='z.py'
    assert data['results'][0]['line']==1
    assert data['results'][0]['excerpt']=='def dispatch_exact(request):'


def test_irrelevant_system_tool_cannot_replace_a_memory_answer(tmp_path):
    class Model:
        def __init__(self):self.calls=0
        def chat(self,messages,**kwargs):
            self.calls+=1
            if self.calls==1:return json.dumps({'tools':[{'worker':'system','tool':'summary','summary':'read host','arguments':{}}]}),0
            assert 'MAPLE-73' in messages[-1]['content']
            return 'The saved bin code is MAPLE-73 (fixture memory).',0
    with MemoryStore(tmp_path/'memory.db') as memory:
        memory.remember('fact','synthetic Vega sensor bin','MAPLE-73',source_type='fixture',source_ref='heldout')
        result=run_agent(memory,Model(),build_builtin_registry(memory),'What is the bin code for the synthetic Vega sensor?')
        assert 'MAPLE-73' in result.core.reply
        assert 'Operating system:' not in result.core.reply


def test_model_refusal_preserves_matched_memory_provenance(tmp_path):
    class Model:
        def chat(self,messages,**kwargs):
            if kwargs.get('json_format'):return '{"tools":[]}',0
            return "I can't answer that.",0
    with MemoryStore(tmp_path/'memory.db') as memory:
        memory.remember('fact','Orion calibration cabinet code','ASH-28',source_type='user_note',source_ref='note-17')
        memory.remember('fact','unrelated private cabinet code','DO-NOT-SHOW',source_type='user_note',source_ref='note-18')
        result=run_agent(memory,Model(),build_builtin_registry(memory),'What is the Orion calibration cabinet code?')
        assert 'ASH-28' in result.core.reply and 'note-17' in result.core.reply
        assert 'not independently verified' in result.core.reply
        assert 'DO-NOT-SHOW' not in result.core.reply


def test_planner_output_limit_still_allows_registered_readonly_search(tmp_path,monkeypatch):
    from prometheus_assistant.ollama import LocalModelError
    root=tmp_path/'project';root.mkdir()
    (root/'worker.py').write_text('def bounded_dispatch():\n    pass\n')
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:root)
    class Limited:
        def chat(self,*args,**kwargs):raise LocalModelError('output limit')
    with MemoryStore(tmp_path/'memory.db') as memory:
        result=run_agent(memory,Limited(),build_builtin_registry(memory),'Search project source for the definition of bounded_dispatch.')
        assert 'worker.py:1' in result.core.reply
        assert result.core.evidence[0].status=='complete'
