import pytest
from prometheus_assistant.agent import ToolRegistry,ToolSpec
from prometheus_assistant.agent_loop import Step
from prometheus_assistant.agent_resume import save_pending,resume_pending
from prometheus_assistant.approvals import PendingApprovalStore

def setup(tmp_path):
    seen=[]; r=ToolRegistry()
    r.register(ToolSpec("write",lambda:seen.append("write") or "ok",mutates_state=True))
    return seen,r,PendingApprovalStore(tmp_path/"pending.json"),Step("write","change")

def test_persisted_approval_resumes_exact_step(tmp_path):
    seen,r,store,step=setup(tmp_path)
    req=save_pending(store,"assistant",step)
    out=resume_pending(r,store,"assistant",step,req.request_id)
    assert out.status=="completed" and seen==["write"]
    assert store.load() is None

def test_wrong_request_id_cannot_resume(tmp_path):
    seen,r,store,step=setup(tmp_path)
    save_pending(store,"assistant",step)
    with pytest.raises(PermissionError):
        resume_pending(r,store,"assistant",step,"wrong")
    assert seen==[] and store.load() is not None

def test_approval_cannot_be_reused_for_different_tool(tmp_path):
    seen,r,store,step=setup(tmp_path)
    req=save_pending(store,"assistant",step)
    other=Step("other","different")
    with pytest.raises(PermissionError):
        resume_pending(r,store,"assistant",other,req.request_id)
    assert seen==[]

def test_approval_cannot_be_replayed_after_success(tmp_path):
    seen,r,store,step=setup(tmp_path)
    req=save_pending(store,"assistant",step)
    assert resume_pending(r,store,"assistant",step,req.request_id).status=="completed"
    with pytest.raises(PermissionError):
        resume_pending(r,store,"assistant",step,req.request_id)
