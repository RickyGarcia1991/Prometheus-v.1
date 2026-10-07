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
