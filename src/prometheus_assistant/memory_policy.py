"""Rules for promoting information into durable memory."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class MemoryProposal:
    kind: str
    subject: str
    value: str
    source_type: str
    source_ref: str
    confidence: float=1.0
    retention: str="persistent"

def durable_memory_allowed(proposal, *, explicit_user=False, verified=False):
    if proposal.source_type == "assistant":
        return False
    if proposal.source_type == "user":
        return bool(explicit_user)
    if proposal.source_type == "verified":
        return bool(verified)
    return False

def commit_memory(store, proposal, *, explicit_user=False, verified=False):
    if not durable_memory_allowed(proposal, explicit_user=explicit_user, verified=verified):
        raise PermissionError("Durable memory write is not authorized.")
    return store.remember(
        proposal.kind, proposal.subject, proposal.value,
        source_type=proposal.source_type, source_ref=proposal.source_ref,
        confidence=proposal.confidence, retention=proposal.retention,
    )
