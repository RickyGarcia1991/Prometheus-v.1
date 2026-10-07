from prometheus_assistant.guardrails import (
    Decision, GuardrailResult, ToolRequest, evaluate_tool_request,
)


def test_read_only_local_tool_is_allowed():
    result=evaluate_tool_request(ToolRequest("code","inspect","read source"))
    assert result.decision is Decision.ALLOW


def test_command_execution_requires_approval():
    result=evaluate_tool_request(ToolRequest("code","shell","run tests",command_execution=True))
    assert result.decision is Decision.APPROVAL


def test_state_mutation_requires_approval():
    result=evaluate_tool_request(ToolRequest("code","write","edit source",mutates_state=True))
    assert result.decision is Decision.APPROVAL


def test_external_network_requires_approval():
    result=evaluate_tool_request(ToolRequest("research","http","fetch source",external_network=True))
    assert result.decision is Decision.APPROVAL


def test_custom_deny_overrides_approval():
    def deny(_):
        return GuardrailResult(Decision.DENY,"blocked")
    result=evaluate_tool_request(ToolRequest("code","shell","danger",command_execution=True),[deny])
    assert result.decision is Decision.DENY
    assert result.reason=="blocked"
