"""Bounded question-to-evidence pipeline for local agent work."""
from __future__ import annotations
from dataclasses import dataclass
from .evidence import collect_evidence
from .task_runtime import execute_local_task
from .synthesis import SynthesisContext,synthesize_context

@dataclass(frozen=True)
class PipelineResult:
    synthesis: SynthesisContext
    task: object

def build_answer_context(prompt, *, memory=None, use_memory=True, use_offline=False,
                         run_tools=False, read_roots=(), approved_tools=()):
    evidence=list(collect_evidence(prompt,memory=memory,use_memory=use_memory,use_offline=use_offline))
    task=None
    if run_tools:
        task=execute_local_task(prompt,read_roots=read_roots,approved_tools=approved_tools)
        evidence.extend(task.evidence)
    return PipelineResult(synthesize_context(prompt,evidence),task)
