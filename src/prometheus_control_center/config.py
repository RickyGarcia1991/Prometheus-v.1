from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PrometheusConfig:
    """Small, dependency-free runtime configuration surface."""

    home: Path
    archive_dir: Path
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, cwd: Path | None = None) -> "PrometheusConfig":
        base = (cwd or Path.cwd()).resolve()
        home = Path(os.getenv("PROMETHEUS_HOME", str(base / "runtime"))).expanduser()
        archive = Path(os.getenv("PROMETHEUS_ARCHIVE_DIR", str(base / "archives"))).expanduser()
        level = os.getenv("PROMETHEUS_LOG_LEVEL", "INFO").strip().upper() or "INFO"
        return cls(home=home.resolve(), archive_dir=archive.resolve(), log_level=level)

    def ensure_directories(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
