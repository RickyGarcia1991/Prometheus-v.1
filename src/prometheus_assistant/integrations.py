from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .hardware import coding_agents, resource_root


def ollama_executable() -> str | None:
    root = resource_root()
    if not root:
        return None
    candidate = Path(root) / "Ollama" / "runtime" / "ollama.exe"
    return str(candidate) if candidate.is_file() else None


def launch_integrations(executable: str | None = None) -> set[str]:
    exe = executable or ollama_executable()
    if not exe:
        return set()
    try:
        result = subprocess.run(
            [exe, "launch", "--help"],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    supported: set[str] = set()
    in_section = False
    for line in result.stdout.splitlines():
        if line.strip() == "Supported integrations:":
            in_section = True
            continue
        if in_section and not line.strip():
            break
        if in_section:
            match = re.match(r"^\s{2}([a-z0-9-]+)\s+", line)
            if match:
                supported.add(match.group(1))
    return supported


def available_local_workers(executable: str | None = None) -> dict[str, str]:
    supported = launch_integrations(executable)
    return {name: path for name, path in coding_agents().items() if name in supported}
