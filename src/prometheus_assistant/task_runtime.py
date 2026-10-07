"""End-to-end bounded local task runtime."""
from __future__ import annotations
from dataclasses import dataclass
from .agent_loop import RunResult,run_plan
from .local_tools import default_local_registry
from .task_planner import TaskPlan,plan_task

@dataclass(frozen=True)
class TaskRuntimeResult:
    plan: TaskPlan
    run: RunResult
    observations: tuple[dict,...]

def execute_local_task(prompt, *, read_roots=(), approved_tools=()):
    plan=plan_task(prompt)
    registry=default_local_registry(read_roots=read_roots)
    run=run_plan(registry,"assistant",plan.steps,approved_tools=approved_tools)
    observations=tuple(
        {"tool":o.tool,"status":o.status,"result":o.result,"reason":o.reason}
        for o in run.observations
    )
    return TaskRuntimeResult(plan,run,observations)
