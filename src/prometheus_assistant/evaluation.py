"""Evaluation gates for controlled Prometheus changes."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Evaluation:
    tests_passed: int
    tests_failed: int=0
    diff_clean: bool=True
    integrity_ok: bool=True
    rollback_ready: bool=True

@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: tuple[str,...]

def evaluate_change(result: Evaluation) -> GateResult:
    reasons=[]
    if result.tests_passed < 1:
        reasons.append("no passing tests recorded")
    if result.tests_failed:
        reasons.append(f"{result.tests_failed} tests failed")
    if not result.diff_clean:
        reasons.append("diff validation failed")
    if not result.integrity_ok:
        reasons.append("integrity validation failed")
    if not result.rollback_ready:
        reasons.append("rollback checkpoint unavailable")
    return GateResult(not reasons,tuple(reasons))
