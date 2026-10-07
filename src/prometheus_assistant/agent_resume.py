"""Persist and safely resume one guarded agent step."""
from __future__ import annotations
from uuid import uuid4
from .approvals import PendingApprovalStore,permission_request
from .agent_loop import Step,run_plan

def save_pending(store:PendingApprovalStore, worker:str, step:Step):
    request=permission_request(uuid4().hex,worker,f"tool:{step.tool}")
    store.save(request)
    return request

def resume_pending(registry, store:PendingApprovalStore, worker:str, step:Step, request_id:str):
    request=store.load()
    if request is None:
        raise PermissionError("No pending approval exists.")
    if request.request_id != request_id or request.worker != worker or request.action != f"tool:{step.tool}":
        raise PermissionError("Approval does not match the pending tool step.")
    result=run_plan(registry,worker,[step],approved_tools={step.tool},max_steps=1)
    if result.status=="completed":
        store.clear()
    return result
