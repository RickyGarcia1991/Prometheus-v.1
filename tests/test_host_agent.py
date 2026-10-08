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


def test_security_gate_is_fail_closed_and_host_bound():
    s = text("Prometheus-Host-Agent.ps1")
    assert "authorized-host.json" in s
    assert "Host authorization fingerprint mismatch" in s
    assert "Anti-rollback blocked controller" in s
    assert "package-validation' 'quarantined" in s
    assert "security-audit.jsonl" in s
    assert "previous=$prev" in s


def test_activation_has_bounded_rollback():
    s = text("Prometheus-Host-Agent.ps1")
    assert "controller-rollback" in s
    assert "Move-Item $backup $dst" in s
    assert "Set-SecurityState $pkg.Version" in s


def test_installer_authorizes_only_after_signed_package_validation():
    s = text("INSTALL-PROMETHEUS-HOST-AGENT.ps1")
    assert "Prometheus-Host-Agent-v0.6.2" in s
    assert "UAC-approved-install" in s
    assert s.index("Host Agent manifest signature invalid") < s.index("UAC-approved-install")


def test_host_agent_serializes_concurrent_invocations():
    s = text("Prometheus-Host-Agent.ps1")
    assert "PrometheusHostAgent" in s
    assert "WaitOne(15000)" in s
    assert "host-agent-busy" in s
    assert "ReleaseMutex()" in s
