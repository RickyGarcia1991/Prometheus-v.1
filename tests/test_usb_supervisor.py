from pathlib import Path
TOOLS=Path(__file__).resolve().parents[1]/"tools"
CTRL=TOOLS/"Prometheus.DriveController"

def test_handoff_stops_all_drive_backed_processes_before_remote():
    t=(CTRL/"Prometheus-Eject-Handoff.ps1").read_text(encoding="utf-8")
    assert "ExecutablePath.StartsWith($drivePrefix" in t
    assert "SSD-backed processes remain; refusing remote disconnect." in t
    assert "runner-paused.request" in t
    assert t.index("$remaining=")<t.index("$dc=@(")

def test_usb_supervisor_restarts_remote_after_prometheus_drive_is_gone():
    t=(TOOLS/"Prometheus-USB-Supervisor.ps1").read_text(encoding="utf-8")
    assert "$mode -ne 'prometheus'" in t
    assert "Remove-Item $pause" in t
    assert "Run-DesktopCommander.ps1" in t
    assert "Start-Process powershell.exe" in t
