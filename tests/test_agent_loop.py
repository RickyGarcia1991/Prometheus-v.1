from prometheus_assistant.agent import ToolRegistry,ToolSpec
from prometheus_assistant.agent_loop import Step,run_plan

def test_bounded_read_only_plan_completes_in_order():
    seen=[]; r=ToolRegistry()
    r.register(ToolSpec("status",lambda:seen.append("status") or {"ok":True}))
    r.register(ToolSpec("knowledge",lambda q:seen.append(q) or "evidence"))
    out=run_plan(r,"assistant",[Step("status","inspect"),Step("knowledge","lookup",("robot",))])
    assert out.status=="completed" and seen==["status","robot"]
    assert len(out.observations)==2

def test_plan_stops_before_unapproved_mutation():
    seen=[]; r=ToolRegistry()
    r.register(ToolSpec("read",lambda:seen.append("read") or 1))
    r.register(ToolSpec("write",lambda:seen.append("write") or 2,mutates_state=True))
    out=run_plan(r,"assistant",[Step("read","inspect"),Step("write","change")])
    assert out.status=="approval_required" and seen==["read"]
    assert out.pending.tool=="write"

def test_approved_tool_can_resume_as_explicitly_authorized():
    seen=[]; r=ToolRegistry()
    r.register(ToolSpec("write",lambda:seen.append("write") or 2,mutates_state=True))
    out=run_plan(r,"assistant",[Step("write","change")],approved_tools={"write"})
    assert out.status=="completed" and seen==["write"]

def test_step_limit_blocks_execution():
    seen=[]; r=ToolRegistry(); r.register(ToolSpec("x",lambda:seen.append(1)))
    out=run_plan(r,"assistant",[Step("x","x")]*9,max_steps=8)
    assert out.status=="denied" and seen==[]
