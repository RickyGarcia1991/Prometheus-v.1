import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")
def test_windows_launcher_help_from_another_directory(tmp_path):
    launcher = ROOT / "START_PROMETHEUS.cmd"
    assert launcher.exists(), "The double-click source launcher is missing"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher), "--help"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Offline chat" in result.stdout and "sessions" in result.stdout
