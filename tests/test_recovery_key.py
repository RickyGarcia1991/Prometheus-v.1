from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/"tools"

def test_recovery_key_builder_excludes_sensitive_and_large_payloads():
    t=(TOOLS/"BUILD-RECOVERY-KEY.ps1").read_text(encoding="utf-8")
    assert "resources_included=$false" in t
    assert "secrets_included=$false" in t
    assert "private_memory_included=$false" in t
    assert "PRK-MANIFEST.json" in t and "SHA256" in t

def test_recovery_key_has_single_usb_staging_workflow():
    t=(TOOLS/"Prometheus-Recovery-Key.ps1").read_text(encoding="utf-8")
    assert "Staged-PRK" in t and "pending-recovery.json" in t
    assert "Safely eject Recovery Key, then connect Prometheus SSD." in t

def test_recovery_key_ui_has_required_recovery_actions():
    t=(TOOLS/"Prometheus-Recovery-Key.ps1").read_text(encoding="utf-8")
    for label in ("DIAGNOSE PROMETHEUS","STAGE REPAIR / RECOVERY","BOOTSTRAP WINDOWS HOST","VERIFY RECOVERY KEY","PREPARE KEY FOR EJECT"):
        assert label in t

def test_recovery_key_eject_stages_before_handoff():
    t=(TOOLS/"Prometheus-Recovery-Key.ps1").read_text(encoding="utf-8")
    line=[x for x in t.splitlines() if "if($Action -eq 'eject')" in x][0]
    assert line.index("Stage") < line.index("Prometheus-Removable-Eject.ps1")


def test_recovery_key_exposes_real_health_and_self_repair_actions():
    t=(TOOLS/"Prometheus-Recovery-Key.ps1").read_text(encoding="utf-8")
    assert "'health','self-repair'" in t
    assert "Prometheus-PRK-SelfRepair.ps1" in t
    assert "-Action health" in t and "-Action repair" in t


def test_recovery_key_builder_packages_portable_live_controller():
    t=(TOOLS/"BUILD-RECOVERY-KEY.ps1").read_text(encoding="utf-8")
    assert "Prometheus.PRK.Controller.csproj" in t
    assert "PRK-Controller" in t
    assert "Prometheus.PRK.Controller.exe" in t

def test_portable_controller_uses_real_health_and_repair_engine():
    c=(TOOLS/"Prometheus.PRK.Controller"/"MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "Prometheus-PRK-SelfRepair.ps1" in c
    assert 'RunEngineAsync("health")' in c and 'RunEngineAsync("repair")' in c
    assert 'GetProperty("percent")' in c and 'GetProperty("state")' in c


def test_portable_controller_has_safe_eject_handoff():
    x=(TOOLS/"Prometheus.PRK.Controller"/"MainWindow.xaml").read_text(encoding="utf-8")
    c=(TOOLS/"Prometheus.PRK.Controller"/"MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "Prepare Key for Eject" in x and 'Click="Eject_Click"' in x
    assert 'Busy("Preparing for eject")' in c
    assert "source_verified" in c
    assert "PRK-Eject-Handoff.ps1" in c
    assert "Prometheus-Removable-Eject.ps1" in c
    assert 'RunRecoveryAsync("stage")' in c
    assert "Eject blocked: Recovery Key integrity verification failed." in c


def test_prk_controller_live_state_and_runtime_fallback_contract():
    x=(TOOLS/"Prometheus.PRK.Controller"/"MainWindow.xaml").read_text(encoding="utf-8")
    c=(TOOLS/"Prometheus.PRK.Controller"/"MainWindow.xaml.cs").read_text(encoding="utf-8")
    b=(TOOLS/"BUILD-RECOVERY-KEY.ps1").read_text(encoding="utf-8")
    assert "Last verified:" in c
    assert 'GetProperty("state").GetString() is "error" or "blocked"' in c and 'GetProperty("state").GetString()=="repairable"' in c
    assert "WaitForExitAsync" in c and "Busy(" in c
    assert "MOUNTED / ACTIVE" in x and "Prepare Key for Eject" in x
    assert "start /wait" in b and "PowerShell recovery interface" in b

def test_self_repair_writes_audit_log():
    t=(TOOLS/"Prometheus-PRK-SelfRepair.ps1").read_text(encoding="utf-8")
    assert "self-repair.log" in t
    assert "Write-RepairLog" in t
    assert "timestamp=(Get-Date).ToString('o')" in t
