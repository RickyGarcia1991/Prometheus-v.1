from pathlib import Path
import subprocess
import sys

from prometheus_control_center.cli import main


def test_doctor_succeeds(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "PASS python" in output
    assert "PASS home" in output


def test_offline_launcher_runs_without_install(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "prometheusctl.py"), "doctor"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS python" in result.stdout
