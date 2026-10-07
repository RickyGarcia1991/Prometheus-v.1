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

def test_runtime_writes_audit_trail(tmp_path,monkeypatch):
    import prometheus_assistant.local_tools as lt
    monkeypatch.setattr(lt,"resource_root",lambda:None)
    log=tmp_path/"agent.jsonl"
    out=execute_local_task("Show system status",activity_log=log)
    rows=log.read_text(encoding="utf-8").splitlines()
    assert out.run.status=="completed"
    assert len(rows)==3
    assert '"stage": "agent-plan"' in rows[0]
    assert '"stage": "agent-tool"' in rows[1]
    assert '"stage": "agent-run"' in rows[2]
