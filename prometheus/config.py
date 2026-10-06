"""Runtime configuration with conservative local-first defaults."""
from __future__ import annotations
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    name: str = "Prometheus"
    model_backend: str = "stub"
    memory_backend: str = "memory"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            name=os.getenv("PROMETHEUS_NAME", "Prometheus"),
            model_backend=os.getenv("PROMETHEUS_MODEL_BACKEND", "stub"),
            memory_backend=os.getenv("PROMETHEUS_MEMORY_BACKEND", "memory"),
            log_level=os.getenv("PROMETHEUS_LOG_LEVEL", "INFO"),
        )
