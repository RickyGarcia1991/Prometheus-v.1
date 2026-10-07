from prometheus_assistant.task_runtime import execute_local_task

def test_status_task_runs_end_to_end(monkeypatch):
    import prometheus_assistant.local_tools as lt
    monkeypatch.setattr(lt,"resource_root",lambda:None)
    out=execute_local_task("Show system status")
    assert out.run.status=="completed"
    assert out.observations[0]["tool"]=="system_status"
    assert out.observations[0]["status"]=="completed"

def test_no_tool_task_completes_without_observations():
    out=execute_local_task("hello there")
    assert out.run.status=="completed"
    assert out.observations==()

def test_runtime_exposes_plan_reason(monkeypatch):
    import prometheus_assistant.local_tools as lt
    monkeypatch.setattr(lt,"resource_root",lambda:None)
    out=execute_local_task("What knowledge resources are installed?")
    assert "resource inventory requested" in out.plan.reason
    assert out.observations[0]["tool"]=="resource_inventory"
