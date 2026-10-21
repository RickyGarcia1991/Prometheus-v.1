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


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")
def test_windows_launcher_detects_chat_after_global_options():
    launcher = ROOT / "START_PROMETHEUS.cmd"
    text = launcher.read_text(encoding="utf-8")
    assert "for %%A in (%*) do" in text
    assert 'if /i "%%~A"=="chat"' in text
    assert '--shutdown-request "!prometheus_shutdown!" %*' in text


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")
def test_end_prometheus_uses_same_shutdown_request_as_launcher():
    start = (ROOT / "START_PROMETHEUS.cmd").read_text(encoding="utf-8")
    end = (ROOT / "END_PROMETHEUS.cmd").read_text(encoding="utf-8")
    marker = r"%LOCALAPPDATA%\Prometheus\active-chat.shutdown"
    assert marker in start
    assert marker in end
    assert "Prometheus stopped cleanly." in end
    assert "safe eject" in end


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")
def test_configured_runtime_with_spaces(tmp_path):
    import sys
    # Use a path containing spaces through a directory junction to the real runtime.
    runtime = tmp_path / "portable python"
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(runtime),
                             str(Path(sys.executable).parent)], capture_output=True)
    if result.returncode:
        # Restricted Windows hosts can deny junction creation. A real copy
        # exercises the same launcher quoting without requiring that privilege.
        import shutil
        shutil.copytree(Path(sys.executable).parent, runtime,
                        ignore=shutil.ignore_patterns('__pycache__'))
    env = dict(os.environ, PROMETHEUS_PYTHON=str(runtime / Path(sys.executable).name))
    result = subprocess.run(["cmd.exe", "/d", "/c", str(ROOT / "START_PROMETHEUS.cmd"), "--help"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "sessions" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher")
def test_missing_configured_runtime_fails(tmp_path):
    env = dict(os.environ, PROMETHEUS_PYTHON=str(tmp_path / "missing.exe"))
    result = subprocess.run(["cmd.exe", "/d", "/c", str(ROOT / "START_PROMETHEUS.cmd"), "--help"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "runtime missing" in result.stdout
