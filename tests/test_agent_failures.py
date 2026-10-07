from prometheus_assistant.agent import ToolRegistry,ToolSpec
from prometheus_assistant.agent_loop import Step,run_plan
from prometheus_assistant.local_tools import default_local_registry
from prometheus_assistant.task_runtime import execute_local_task

def test_tool_exception_stops_following_steps():
    seen=[]; r=ToolRegistry()
    r.register(ToolSpec("bad",lambda:1/0))
    r.register(ToolSpec("later",lambda:seen.append(1)))
    out=run_plan(r,"assistant",[Step("bad","fail"),Step("later","must not run")])
    assert out.status=="failed" and seen==[]

def test_path_traversal_cannot_escape_root(tmp_path):
    root=tmp_path/"safe"; root.mkdir()
    secret=tmp_path/"secret.txt"; secret.write_text("secret",encoding="utf-8")
    r=default_local_registry(read_roots=[root])
    out=r.execute("assistant","read_text","attempt escape",str(root/".."/"secret.txt"))
    assert out.status=="failed" and "PermissionError" in out.reason

def test_missing_file_is_contained(tmp_path):
    root=tmp_path/"safe"; root.mkdir()
    r=default_local_registry(read_roots=[root])
    out=r.execute("assistant","read_text","missing",str(root/"missing.txt"))
    assert out.status=="failed" and "FileNotFoundError" in out.reason

def test_empty_plan_does_not_gain_authority():
    out=execute_local_task("write files and run commands")
    assert out.plan.steps==()
    assert out.observations==()

def test_approval_is_scoped_to_named_tool_only():
    seen=[]; r=ToolRegistry()
    r.register(ToolSpec("a",lambda:seen.append("a"),mutates_state=True))
    r.register(ToolSpec("b",lambda:seen.append("b"),mutates_state=True))
    out=run_plan(r,"assistant",[Step("a","a"),Step("b","b")],approved_tools={"a"})
    assert out.status=="approval_required" and seen==["a"] and out.pending.tool=="b"
