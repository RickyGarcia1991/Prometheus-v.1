from prometheus_assistant.pipeline import build_answer_context

def test_pipeline_can_add_agent_tool_evidence(monkeypatch):
    import prometheus_assistant.local_tools as lt
    monkeypatch.setattr(lt,"resource_root",lambda:None)
    out=build_answer_context("show system status",use_memory=False,run_tools=True)
    assert out.task.run.status=="completed"
    assert out.synthesis.evidence_count>=1
    assert any(s["ref"]=="system_status" for s in out.synthesis.sources)

def test_pipeline_without_tools_has_no_agent_authority():
    out=build_answer_context("hello",use_memory=False,run_tools=False)
    assert out.task is None
    assert out.synthesis.evidence_count==0
