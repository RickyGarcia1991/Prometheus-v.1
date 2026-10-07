"""Bounded planner -> tool -> observation execution loop."""
from __future__ import annotations
from dataclasses import dataclass
from .agent import ToolRegistry, ToolOutcome

@dataclass(frozen=True)
class Step:
    tool: str
    summary: str
    args: tuple=()
    kwargs: tuple=()

@dataclass(frozen=True)
class RunResult:
    status: str
    observations: tuple[ToolOutcome,...]
    pending: Step|None=None

def run_plan(registry: ToolRegistry, worker: str, steps, *, approved_tools=(), max_steps=8):
    steps=tuple(steps)
    if len(steps)>max_steps:
        return RunResult("denied",(),None)
    observations=[]
    approved=set(approved_tools)
    for step in steps:
        out=registry.execute(worker,step.tool,step.summary,*step.args,
                             approved=step.tool in approved,**dict(step.kwargs))
        observations.append(out)
        if out.status=="approval_required":
            return RunResult("approval_required",tuple(observations),step)
        if out.status!="completed":
            return RunResult("failed",tuple(observations),None)
    return RunResult("completed",tuple(observations),None)
