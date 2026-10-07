"""Constrain model-suggested plans to deterministic Prometheus authority."""
from __future__ import annotations
from .agent_loop import Step
from .task_planner import TaskPlan,plan_task

def constrain_suggestions(prompt, suggestions):
    authority=plan_task(prompt)
    allowed={step.tool:step for step in authority.steps}
    chosen=[]
    for name in suggestions:
        if name in allowed and name not in {s.tool for s in chosen}:
            chosen.append(allowed[name])
    # A model may narrow/reorder allowed read-only work, never add authority.
    return TaskPlan(tuple(chosen),f"Model suggestions constrained by deterministic authority: {authority.reason}")

def parse_tool_suggestions(text):
    if not isinstance(text,str):
        return ()
    names=[]
    for raw in text.replace(","," ").split():
        name=raw.strip().lower()
        if name in {"system_status","resource_inventory","offline_knowledge","read_text"}:
            names.append(name)
    return tuple(names)
