from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys
import tempfile

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
            # Exclusive creation cannot overwrite or follow an existing probe
            # name; the context cleans up only the temporary file we created.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path,
                                             prefix=".prometheus-write-test-") as probe:
                probe.write("ok")
                probe.flush()
            checks.append(Check(name, True, str(path)))
        except OSError as exc:
            checks.append(Check(name, False, f"{path}: {exc}"))
    exposed = [key for key in os.environ if any(token in key.upper() for token in ("PASSWORD", "TOKEN", "SECRET", "API_KEY"))]
    checks.append(Check("secret_hygiene", True, f"{len(exposed)} secret-like environment variable names detected; values were not read"))
    return checks
