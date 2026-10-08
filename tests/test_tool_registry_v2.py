import pytest
from prometheus_assistant.tool_registry import ArgSpec,ToolRegistry,ToolSpec

def registry(): return ToolRegistry([ToolSpec("project","search","Search",lambda r:"ok",{"query":ArgSpec(max_length=20)})])

def test_typed_arguments_are_preserved_and_risk_is_registry_owned():
    req=registry().requests_from_plan([{"worker":"project","tool":"search","summary":"find core","arguments":{"query":"run_core"}}])[0]
    assert req.arguments["query"]=="run_core" and not req.command_execution

def test_unknown_arguments_are_rejected():
    with pytest.raises(ValueError,match="arguments"):
        registry().requests_from_plan([{"worker":"project","tool":"search","summary":"x","arguments":{"query":"x","path":"secret"}}])

def test_missing_and_oversized_arguments_are_rejected():
    with pytest.raises(ValueError,match="Missing"):
        registry().get("project","search").request("x",{})
    with pytest.raises(ValueError,match="Invalid tool argument"):
        registry().get("project","search").request("x",{"query":"x"*21})

def test_model_cannot_inject_risk_flags():
    with pytest.raises(ValueError,match="schema"):
        registry().requests_from_plan([{"worker":"project","tool":"search","summary":"x","arguments":{"query":"x"},"command_execution":False}])
