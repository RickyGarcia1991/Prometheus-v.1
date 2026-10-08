from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

def test_shutdown_does_not_force_kill_processes():
    text = (TOOLS / "portable-safe-disconnect.ps1").read_text()
    assert "Stop-Process" not in text
    assert "RequestWindowsRemoval" in text
    assert "portable-eject-preflight.ps1" in text

def test_controller_setup_preserves_approved_installer():
    text = (TOOLS / "portable-controller-setup.ps1").read_text()
    assert "INSTALL-PROMETHEUS-HOST-AGENT.ps1" in text
    assert "-Mode Install" in text
    assert "START-PROMETHEUS-CONTROLLER.cmd" in text

def test_eject_preflight_is_non_destructive():
    text = (TOOLS / "portable-eject-preflight.ps1").read_text()
    assert "Stop-Process" not in text
    assert "CM_Request_Device_Eject" not in text
    assert "quick_check" in text
