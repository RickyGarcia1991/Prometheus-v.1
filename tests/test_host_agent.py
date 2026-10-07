from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "tools" / "Prometheus.HostAgent"


def text(name):
    return (HOST / name).read_text(encoding="utf-8")


def test_host_agent_contract():
    s = text("Prometheus-Host-Agent.ps1")
    assert "eject-mode.json" in s
    assert "suppressed eject-in-progress" in s
    assert ".staging-" in s and ".previous" in s
    assert "Staged integrity failure" in s
    assert "return $fail" in s and "exit $rc" in s
    assert "Duplicate" not in s  # process duplication is avoided by running-controller detection


def test_watcher_is_event_driven_and_eject_aware():
    s = text("Prometheus-Volume-Watcher.ps1")
    assert "Win32_VolumeChangeEvent" in s
    assert "EventType = 2 OR EventType = 3" in s
    assert "Start-Sleep -Seconds 3" not in s
    assert "cleared-eject-lock-on-new-arrival" in s
    assert "arrival-suppressed-eject-lock" in s


def test_installer_removes_legacy_tasks_and_is_repairable():
    s = text("INSTALL-PROMETHEUS-HOST-AGENT.ps1")
    assert "'Install','Repair','Uninstall','Verify'" in s
    assert "Prometheus Host Agent Logon" in s
    assert "Prometheus Device Support" in s
    assert "Prometheus Volume Watcher" in s
    assert "Parser]::ParseFile" in s
    assert "/SC ONLOGON" in s
