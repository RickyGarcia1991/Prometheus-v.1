"""Machine-checkable release readiness for Prometheus source checkpoints."""
from __future__ import annotations
from dataclasses import dataclass
from .evaluation import Evaluation,evaluate_change

@dataclass(frozen=True)
class ReleaseEvidence:
    tests_passed: int
    tests_failed: int
    diff_clean: bool
    integrity_ok: bool
    rollback_ready: bool
    hygiene_ok: bool
    benchmark_failed: int

def evaluate_release(e:ReleaseEvidence):
    base=evaluate_change(Evaluation(
        tests_passed=e.tests_passed,tests_failed=e.tests_failed,
        diff_clean=e.diff_clean,integrity_ok=e.integrity_ok,
        rollback_ready=e.rollback_ready))
    reasons=list(base.reasons)
    if not e.hygiene_ok:
        reasons.append("repository hygiene failed")
    if e.benchmark_failed:
        reasons.append(f"acceptance benchmark has {e.benchmark_failed} failure(s)")
    return {"passed":not reasons,"reasons":tuple(reasons)}
