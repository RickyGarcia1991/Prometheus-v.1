"""Deterministic bounded planning for safe local agent tasks."""
from __future__ import annotations
from dataclasses import dataclass
from .agent_loop import Step

@dataclass(frozen=True)
class TaskPlan:
    steps: tuple[Step,...]
    reason: str

STATUS_HINTS=("system status","hardware","computer status","prometheus status")
RESOURCE_HINTS=("resources","knowledge resources","what is installed","inventory")
KNOWLEDGE_HINTS=("who ","what ","when ","where ","define ","explain ","history ","tell me about")

def plan_task(prompt: str) -> TaskPlan:
    text=" ".join(prompt.lower().split())
    steps=[]
    reasons=[]
    if any(h in text for h in STATUS_HINTS):
        steps.append(Step("system_status","Inspect local Prometheus system status."))
        reasons.append("system status requested")
    if any(h in text for h in RESOURCE_HINTS):
        steps.append(Step("resource_inventory","Inspect local knowledge resources."))
        reasons.append("resource inventory requested")
    dedicated=bool(steps)
    if not dedicated and any(text.startswith(h) or h in text for h in KNOWLEDGE_HINTS):
        steps.append(Step("offline_knowledge","Search installed offline knowledge.",(prompt,)))
        reasons.append("factual knowledge requested")
    # Deduplicate while preserving order.
    unique=[]; seen=set()
    for step in steps:
        if step.tool not in seen:
            unique.append(step); seen.add(step.tool)
    return TaskPlan(tuple(unique),"; ".join(reasons) if reasons else "No tool is required.")
