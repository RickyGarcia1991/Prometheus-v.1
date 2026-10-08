from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL = "approval"


@dataclass(frozen=True)
class GuardrailResult:
    decision: Decision
    reason: str = ""


@dataclass(frozen=True)
class ToolRequest:
    worker: str
    tool: str
    summary: str
    arguments: Mapping[str, object] | None = None
    mutates_state: bool = False
    external_network: bool = False
    command_execution: bool = False


Guardrail = Callable[[ToolRequest], GuardrailResult]


def default_tool_guardrail(request: ToolRequest) -> GuardrailResult:
    if request.external_network:
        return GuardrailResult(Decision.APPROVAL, "External network access requires explicit approval.")
    if request.command_execution or request.mutates_state:
        return GuardrailResult(Decision.APPROVAL, "State-changing or command tools require explicit approval.")
    return GuardrailResult(Decision.ALLOW)


def evaluate_tool_request(request: ToolRequest, guardrails: Iterable[Guardrail] = ()) -> GuardrailResult:
    checks = (default_tool_guardrail, *tuple(guardrails))
    approval: GuardrailResult | None = None
    for check in checks:
        result = check(request)
        if result.decision is Decision.DENY:
            return result
        if result.decision is Decision.APPROVAL and approval is None:
            approval = result
    return approval or GuardrailResult(Decision.ALLOW)
