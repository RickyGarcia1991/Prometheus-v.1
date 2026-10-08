import json, os, shutil, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/"tools"/"Prometheus-PRK-SelfRepair.ps1"

def run_ps(action, drive, stage, local, cleanup=False):
    cmd=["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(ENGINE),"-Action",action,"-Drive",str(drive),"-StageRoot",str(stage),"-LocalPrometheusRoot",str(local)]
    if cleanup: cmd.append("-AllowStateCleanup")
    return subprocess.run(cmd,capture_output=True,text=True,check=True)

def make_key(base):
    key=base/"Prometheus-Recovery-Key"; (key/"tools").mkdir(parents=True); (key/"src").mkdir(); (key/"tests").mkdir()
    files={"PRK-STATUS.json":"{}","tools/Prometheus-Recovery-Key.ps1":"# prk","tools/Prometheus-USB-Supervisor.ps1":"# supervisor","src/a.txt":"source","tests/a.txt":"tests"}
    import hashlib
    manifest=[]
    for rel,data in files.items():
        p=key/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(data,encoding="utf-8")
        b=p.read_bytes(); manifest.append({"path":rel.replace("/","\\"),"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest().upper()})
    (key/"PRK-MANIFEST.json").write_text(json.dumps({"algorithm":"SHA256","files":manifest}),encoding="utf-8")
    return key

def test_engine_has_real_repair_contract():
    t=ENGINE.read_text(encoding="utf-8")
    for s in ("Copy-VerifiedFile","Post-repair verification failed","source integrity failed","rebuilt_staging","restored_host_supervisor"):
        assert s in t

def test_health_reports_manifest_count_and_armed_repair():
    with tempfile.TemporaryDirectory() as td:
        b=Path(td); make_key(b); r=json.loads(run_ps("health",b,b/"stage",b/"local").stdout)
        assert r["protected_files"]==5 and r["source_verified"] is True
        assert next(x for x in r["channels"] if x["name"]=="self_repair")["state"]=="armed"

def test_repair_restores_host_and_staging_then_reverifies():
    with tempfile.TemporaryDirectory() as td:
        b=Path(td); make_key(b); local=b/"local"; stage=b/"stage"
        r=json.loads(run_ps("repair",b,stage,local).stdout)
        assert r["success"] is True
        assert "rebuilt_staging" in r["actions"] and "restored_host_supervisor" in r["actions"]
        assert (local/"Prometheus-USB-Supervisor.ps1").exists() and (stage/"PRK-MANIFEST.json").exists()

def test_corrupt_prk_source_blocks_self_repair():
    with tempfile.TemporaryDirectory() as td:
        b=Path(td); key=make_key(b); (key/"tools"/"Prometheus-USB-Supervisor.ps1").write_text("CORRUPT",encoding="utf-8")
        r=json.loads(run_ps("repair",b,b/"stage",b/"local").stdout)
        assert r["success"] is False and r["state"]=="blocked"
        assert not (b/"local"/"Prometheus-USB-Supervisor.ps1").exists()

def test_stale_state_cleanup_is_explicit_and_repairable():
    with tempfile.TemporaryDirectory() as td:
        b=Path(td); make_key(b); local=b/"LocalAppData"/"Prometheus"; local.mkdir(parents=True)
        dc=local.parent/"DesktopCommanderStartup"; dc.mkdir(); (local/"eject-mode.json").write_text("{}"); (dc/"runner-paused.request").write_text("1")
        r=json.loads(run_ps("repair",b,b/"stage",local,cleanup=True).stdout)
        assert r["success"] is True
        assert "cleared_stale_eject_lock" in r["actions"] and "cleared_stale_commander_pause" in r["actions"]
        assert not (local/"eject-mode.json").exists() and not (dc/"runner-paused.request").exists()


def test_corrupt_staged_file_is_detected_and_rebuilt():
    with tempfile.TemporaryDirectory() as td:
        b=Path(td); key=make_key(b); local=b/"local"; stage=b/"stage"
        first=json.loads(run_ps("repair",b,stage,local).stdout)
        assert first["success"] is True
        (stage/"src"/"a.txt").write_text("CORRUPT STAGE",encoding="utf-8")
        health=json.loads(run_ps("health",b,stage,local).stdout)
        staging=next(x for x in health["channels"] if x["name"]=="staging")
        assert staging["state"]=="repairable" and staging["percent"]<100
        repaired=json.loads(run_ps("repair",b,stage,local).stdout)
        assert repaired["success"] is True and "rebuilt_staging" in repaired["actions"]
        assert (stage/"src"/"a.txt").read_text(encoding="utf-8")=="source"


def test_prk_preserves_host_bound_security_state():
    t=ENGINE.read_text(encoding="utf-8")
    assert "security_state" in t
    assert "PRK will not replace authorization or lower anti-rollback state" in t
    with tempfile.TemporaryDirectory() as td:
        b=Path(td); make_key(b); local=b/"local"; local.mkdir()
        state=local/"security-state.json"; auth=local/"authorized-host.json"
        state.write_text('{"highest_controller_version":"9.9.9"}',encoding="utf-8")
        auth.write_text('{"fingerprint":"BOUND-HOST"}',encoding="utf-8")
        import hashlib
        for p in (state,auth):
            (Path(str(p)+".sha256")).write_text(hashlib.sha256(p.read_bytes()).hexdigest().upper(),encoding="ascii")
        before=(state.read_bytes(),auth.read_bytes())
        r=json.loads(run_ps("repair",b,b/"stage",local).stdout)
        assert r["success"] is True
        assert (state.read_bytes(),auth.read_bytes())==before
