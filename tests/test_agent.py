from prometheus_assistant.agent import ToolRegistry, ToolSpec

def test_read_only_local_tool_executes_without_approval():
    r=ToolRegistry(); r.register(ToolSpec("lookup",lambda x:x.upper()))
    out=r.execute("assistant","lookup","read local data","ok")
    assert out.status=="completed" and out.result=="OK"

def test_mutating_tool_stops_at_approval_boundary():
    called=[]
    r=ToolRegistry(); r.register(ToolSpec("write",lambda:called.append(1),mutates_state=True))
    out=r.execute("assistant","write","change state")
    assert out.status=="approval_required" and called==[]

def test_explicit_approval_allows_guarded_mutation():
    called=[]
    r=ToolRegistry(); r.register(ToolSpec("write",lambda:called.append(1) or "done",mutates_state=True))
    out=r.execute("assistant","write","change state",approved=True)
    assert out.status=="completed" and called==[1]

def test_network_and_command_tools_require_approval():
    r=ToolRegistry()
    r.register(ToolSpec("net",lambda:1,external_network=True))
    r.register(ToolSpec("shell",lambda:1,command_execution=True))
    assert r.execute("assistant","net","fetch").status=="approval_required"
    assert r.execute("assistant","shell","run").status=="approval_required"

def test_unknown_tool_is_denied_and_handler_failure_is_contained():
    r=ToolRegistry(); r.register(ToolSpec("bad",lambda:1/0))
    assert r.execute("assistant","missing","x").status=="denied"
    out=r.execute("assistant","bad","x")
    assert out.status=="failed" and "ZeroDivisionError" in out.reason
