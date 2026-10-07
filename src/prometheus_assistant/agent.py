"""Guarded local tool execution boundary for Prometheus agents."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from .guardrails import Decision, ToolRequest, evaluate_tool_request

@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: Callable
    mutates_state: bool=False
    external_network: bool=False
    command_execution: bool=False

@dataclass(frozen=True)
class ToolOutcome:
    status: str
    tool: str
    result: object=None
    reason: str=""

class ToolRegistry:
    def __init__(self):
        self._tools={}

    def register(self, spec: ToolSpec):
        if not spec.name.strip() or spec.name in self._tools:
            raise ValueError("Tool names must be unique and non-empty.")
        self._tools[spec.name]=spec

    def execute(self, worker, name, summary, *args, approved=False, **kwargs):
        spec=self._tools.get(name)
        if spec is None:
            return ToolOutcome("denied",name,reason="Unknown tool.")
        request=ToolRequest(worker,name,summary,spec.mutates_state,spec.external_network,spec.command_execution)
        decision=evaluate_tool_request(request)
        if decision.decision is Decision.DENY:
            return ToolOutcome("denied",name,reason=decision.reason)
        if decision.decision is Decision.APPROVAL and not approved:
            return ToolOutcome("approval_required",name,reason=decision.reason)
        try:
            return ToolOutcome("completed",name,result=spec.handler(*args,**kwargs))
        except Exception as exc:
            return ToolOutcome("failed",name,reason=f"{type(exc).__name__}: {exc}")
