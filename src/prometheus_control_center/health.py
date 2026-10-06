from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys

from .config import PrometheusConfig


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(config: PrometheusConfig) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check("python", sys.version_info >= (3, 11), sys.version.split()[0]))
    for name, path in (("home", config.home), ("archive_dir", config.archive_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".prometheus-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append(Check(name, True, str(path)))
        except OSError as exc:
            checks.append(Check(name, False, f"{path}: {exc}"))
    exposed = [key for key in os.environ if any(token in key.upper() for token in ("PASSWORD", "TOKEN", "SECRET", "API_KEY"))]
    checks.append(Check("secret_hygiene", True, f"{len(exposed)} secret-like environment variable names detected; values were not read"))
    return checks
