import pytest
from prometheus_assistant.guardrails import ToolRequest
from prometheus_assistant.tool_registry import ToolRegistry, ToolSpec
from prometheus_assistant.core import run_core
from prometheus_assistant.memory import MemoryStore


def test_registry_execution_rejects_caller_risk_downgrade(tmp_path):
    effects = []
    registry = ToolRegistry([ToolSpec('test', 'write', 'test write', lambda r: effects.append(1), {}, mutates_state=True)])
    with MemoryStore(tmp_path/'memory.db') as memory:
        with pytest.raises(ValueError, match='metadata|risk'):
            run_core(memory, 'test', tool_requests=[ToolRequest('test', 'write', 'forged safe')], executors=registry.executors())
    assert effects == []


def test_plain_executor_map_is_never_authority(tmp_path, monkeypatch):
    effects = []
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', '*')
    with MemoryStore(tmp_path/'memory.db') as memory:
        with pytest.raises(ValueError, match='registrations'):
            run_core(memory, 'forged safe write', tool_requests=[ToolRequest('project','replace','write')],
                     executors={('project','replace'):lambda r:effects.append(1)}, approved_request_ids=[0])
    assert effects == []


def test_registered_core_result_serializes_immutable_arguments(tmp_path):
    import json
    registry = ToolRegistry([ToolSpec('test','read','read',lambda r:'fact',{})])
    with MemoryStore(tmp_path/'memory.db') as memory:
        result=run_core(memory,'read',tool_requests=[registry.get('test','read').request('read',{})],executors=registry.executors())
    assert json.loads(json.dumps(result.as_dict()))['evidence'][0]['output']=='fact'


def test_direct_interface_refuses_unmapped_mode(tmp_path, monkeypatch):
    from prometheus_assistant.ui_server import Interface
    app = Interface(tmp_path/'memory.db')
    effects = []
    monkeypatch.setattr(app, 'save', lambda *a, **k: effects.append(1))
    try:
        with pytest.raises(ValueError, match='mode|capability'):
            app.execute({'mode': 'unmapped', 'prompt': '2+2'})
        assert effects == []
    finally:
        app.close()


def test_denied_model_probe_never_launches_runtime(tmp_path,monkeypatch):
    from prometheus_assistant import ui_server
    calls=[]
    app=ui_server.Interface(tmp_path/'memory.db')
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','ollama.request')
    monkeypatch.setattr(ui_server.subprocess,'Popen',lambda *a,**k:calls.append('launch'))
    try:
        with pytest.raises(ValueError,match='denied'):app.ensure_runtime()
        import time
        png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aG9sAAAAASUVORK5CYII='
        job=app.submit({'mode':'vision','prompt':'Describe this image.','content':png})
        deadline=time.monotonic()+3
        while app.jobs[job['id']]['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
        assert app.jobs[job['id']]['state']=='error'
        assert 'denied' in app.jobs[job['id']]['error']
        assert app.current is None
        assert calls==[]
    finally:app.close()


def test_denied_hardware_inspection_never_calls_subprocess(monkeypatch):
    from prometheus_assistant import media
    calls=[]
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','media.inspect')
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**k:calls.append('probe'))
    with pytest.raises(ValueError,match='denied'):media.nvidia_devices()
    assert calls==[]


def test_denied_standalone_archive_entry_never_reads_input_or_searches(monkeypatch,capsys):
    from prometheus_assistant import archive_worker,articles
    calls=[]
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','archive.search')
    monkeypatch.setattr(articles,'search_articles',lambda *a,**k:calls.append('search'))
    assert archive_worker.main()==1
    assert 'denied' in capsys.readouterr().out
    assert calls==[]
