"""Small deterministic acceptance benchmark for the v0.6 agent core."""
from __future__ import annotations
from dataclasses import dataclass
from .task_planner import plan_task
from .model_planner import constrain_suggestions
from .memory_policy import MemoryProposal,durable_memory_allowed
from .observations import observation_evidence
from .context import Evidence
from .synthesis import synthesize_context

@dataclass(frozen=True)
class BenchmarkResult:
    passed: int
    failed: int
    failures: tuple[str,...]

def run_acceptance_benchmark():
    checks=[
        ("status routes locally",[s.tool for s in plan_task("show system status").steps]==["system_status"]),
        ("casual stays tool free",plan_task("hello friend").steps==()),
        ("factual uses offline",[s.tool for s in plan_task("explain hydraulics").steps]==["offline_knowledge"]),
        ("model cannot add authority",constrain_suggestions("hello",["system_status"]).steps==()),
        ("assistant cannot self-write memory",not durable_memory_allowed(MemoryProposal("fact","x","y","assistant","bench"),explicit_user=True,verified=True)),
        ("failed tools cannot become evidence",observation_evidence("system",[{"tool":"system_status","status":"failed","result":{"system":"Windows"}}])==[]),
        ("synthesis marks evidence untrusted","untrusted data" in synthesize_context("robot",[Evidence("tool","x","robot fact",50)]).system_suffix),
    ]
    failures=tuple(name for name,ok in checks if not ok)
    return BenchmarkResult(len(checks)-len(failures),len(failures),failures)
