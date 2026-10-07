"""Measured progress model for the Prometheus Drive Controller."""
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

class CheckState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"

@dataclass(frozen=True)
class Check:
    key: str
    label: str
    weight: int
    state: CheckState = CheckState.PENDING
    fraction: float = 0.0

    @property
    def earned(self) -> float:
        if self.state is CheckState.PASSED:
            return float(self.weight)
        if self.state is CheckState.RUNNING:
            return self.weight * min(1.0, max(0.0, self.fraction))
        return 0.0

STARTUP = (
    Check("drive_identity", "Prometheus SSD identity verified", 10),
    Check("runtime", "Runtime files validated", 10),
    Check("ollama_api", "Ollama API online", 15),
    Check("model", "Requested model available / loaded", 20),
    Check("memory", "Memory database verified", 10),
    Check("resources", "Knowledge/resource stores verified", 10),
    Check("index", "Local index/resource initialization", 15),
    Check("chat", "Chat runtime ready", 10),
)

SHUTDOWN = (
    Check("signal", "Graceful shutdown signal sent", 10),
    Check("clients", "Active chat clients closed", 20),
    Check("integrity", "Session/database integrity verified", 20),
    Check("models_stopped", "AI/model processes stopped", 15),
    Check("refs_clear", "SSD-backed process references cleared", 15),
    Check("pnp_clear", "PnP/UASP removal checks clear", 20),
)

def percent(checks: Iterable[Check]) -> int:
    checks = tuple(checks)
    total = sum(c.weight for c in checks)
    if total != 100:
        raise ValueError(f"progress weights must total 100, got {total}")
    return round(sum(c.earned for c in checks))

def ready_to_eject(checks: Iterable[Check]) -> bool:
    checks = tuple(checks)
    return bool(checks) and all(c.state is CheckState.PASSED for c in checks)

if __name__ == "__main__":
    assert sum(c.weight for c in STARTUP) == 100
    assert sum(c.weight for c in SHUTDOWN) == 100
    assert percent(STARTUP) == 0
    assert percent(tuple(Check(c.key,c.label,c.weight,CheckState.PASSED) for c in SHUTDOWN)) == 100
    print("progress-contract: PASS")
