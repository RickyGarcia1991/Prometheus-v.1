import hashlib, json, subprocess, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HOST=ROOT/"tools"/"Prometheus.HostAgent"
RECOVERY=HOST/"Prometheus-Security-Recovery.ps1"

def ps(mode, root):
    r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(RECOVERY),"-Mode",mode,"-HostRoot",str(root)],capture_output=True,text=True)
    return r, json.loads(r.stdout)

def protect(path):
    path.with_name(path.name+".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest().upper(),encoding="ascii")

def audit_line(previous="GENESIS", detail="ok"):
    body={"timestamp":"2026-10-07T00:00:00Z","event":"test","result":"pass","host":"TEST","detail":detail,"previous":previous}
    raw=json.dumps(body,separators=(",",":"))
    body["hash"]=hashlib.sha256(raw.encode()).hexdigest().upper()
    return json.dumps(body,separators=(",",":")),body["hash"]

def test_security_recovery_contract():
    s=(HOST/"Prometheus-Host-Agent.ps1").read_text(encoding="utf-8")
    assert ".recovery-1" in s and ".recovery-2" in s and ".recovery-3" in s
    assert "security-state.json'+'.bak1" not in s
    assert "Security state is malformed or failed integrity verification" in s
    assert "Test-ProtectedFile $AuthorizedHost" in s

def test_audit_verifier_detects_tampering():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); line,h=audit_line(); (root/"security-audit.jsonl").write_text(line+"\n",encoding="utf-8")
        r,o=ps("VerifyAudit",root); assert r.returncode==0 and o["ok"]
        data=json.loads(line); data["detail"]="tampered"; (root/"security-audit.jsonl").write_text(json.dumps(data,separators=(",",":"))+"\n",encoding="utf-8")
        r,o=ps("VerifyAudit",root); assert r.returncode==2 and not o["ok"]

def test_state_sidecars_fail_closed_on_corruption():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); root.mkdir(exist_ok=True)
        for name,data in [("security-state.json",{"highest_controller_version":"0.5.4"}),("authorized-host.json",{"fingerprint":"TEST"})]:
            p=root/name; p.write_text(json.dumps(data),encoding="utf-8"); protect(p)
        r,o=ps("VerifyState",root); assert r.returncode==0 and o["ok"]
        (root/"security-state.json").write_text('{"highest_controller_version":"0.0.1"}',encoding="utf-8")
        r,o=ps("VerifyState",root); assert r.returncode==2 and not o["ok"]

def test_snapshot_verification_detects_modified_payload():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); snap=root/"Controllers"/"Prometheus-Controller-v1.0.0.recovery-1"; snap.mkdir(parents=True)
        payload=snap/"controller.bin"; payload.write_bytes(b"known-good")
        manifest=[{"path":"controller.bin","sha256":hashlib.sha256(payload.read_bytes()).hexdigest().upper()}]
        (snap/"SHA256-MANIFEST.json").write_text(json.dumps(manifest),encoding="utf-8")
        r,o=ps("VerifySnapshots",root); assert r.returncode==0 and o["ok"]
        payload.write_bytes(b"corrupt")
        r,o=ps("VerifySnapshots",root); assert r.returncode==2 and not o["ok"]

def test_installer_packages_security_recovery_and_protects_authorization():
    s=(HOST/"INSTALL-PROMETHEUS-HOST-AGENT.ps1").read_text(encoding="utf-8")
    assert "Prometheus-Security-Recovery.ps1" in s
    assert "$AuthorizedHost+'.sha256'" in s
