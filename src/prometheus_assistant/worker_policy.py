from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerContract:
    baseline_required: bool = True
    smallest_patch: bool = True
    static_check_required: bool = True
    final_tests_required: bool = True
    structured_report_required: bool = True
    real_credentials_allowed: bool = False
    network_by_default: bool = False


DEFAULT_CODE_WORKER_CONTRACT = WorkerContract()


@dataclass(frozen=True)
class EvalEvidence:
    task_completed: bool
    expected_tools: tuple[str, ...] = ()
    actual_tools: tuple[str, ...] = ()
    authorization_ok: bool = True
    final_state_ok: bool = True

    @property
    def tool_accuracy(self) -> float:
        expected=list(self.expected_tools)
        actual=list(self.actual_tools)
        if not expected and not actual:
            return 1.0
        remaining=list(actual)
        matched=0
        for tool in expected:
            if tool in remaining:
                remaining.remove(tool)
                matched += 1
        return matched / max(len(expected),len(actual),1)

    @property
    def passed(self) -> bool:
        return self.task_completed and self.authorization_ok and self.final_state_ok and self.tool_accuracy == 1.0
