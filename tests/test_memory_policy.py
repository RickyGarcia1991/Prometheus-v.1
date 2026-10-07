import pytest
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.memory_policy import MemoryProposal,commit_memory,durable_memory_allowed

def proposal(source):
    return MemoryProposal("fact","actuator","electric hands",source,"test")

def test_assistant_claim_never_promotes_itself():
    assert not durable_memory_allowed(proposal("assistant"),explicit_user=True,verified=True)

def test_user_fact_requires_explicit_memory_intent():
    p=proposal("user")
    assert not durable_memory_allowed(p)
    assert durable_memory_allowed(p,explicit_user=True)

def test_verified_fact_requires_verification_gate():
    p=proposal("verified")
    assert not durable_memory_allowed(p)
    assert durable_memory_allowed(p,verified=True)

def test_unknown_source_is_denied():
    assert not durable_memory_allowed(proposal("web"),verified=True)

def test_commit_enforces_policy(tmp_path):
    with MemoryStore(tmp_path/"m.db") as store:
        with pytest.raises(PermissionError):
            commit_memory(store,proposal("assistant"))
        row=commit_memory(store,proposal("user"),explicit_user=True)
        assert row and store.knowledge(subject="actuator")[0]["value"]=="electric hands"
