import json, os, subprocess
from pathlib import Path
from test_prk_self_repair import make_key, run_ps

ROOT=Path(__file__).resolve().parents[1]
RECOVERY=ROOT/"tools"/"Prometheus-Recovery-Key.ps1"

def stage(base, local):
    env=os.environ.copy()
    env["LOCALAPPDATA"]=str(local)
    return subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(RECOVERY),"-Action","stage","-Drive",str(base)],env=env,capture_output=True,text=True)

def test_stage_rejects_corruption_and_preserves_previous(tmp_path):
    key=make_key(tmp_path)
    local=tmp_path/"profile"
    prior=local/"Prometheus"/"Recovery"/"Staged-PRK"
    prior.mkdir(parents=True)
    (prior/"sentinel.txt").write_text("previous good recovery")
    (key/"src"/"a.txt").write_text("corrupt")
    result=stage(tmp_path,local)
    assert result.returncode != 0
    assert (prior/"sentinel.txt").read_text()=="previous good recovery"

def test_stage_verifies_copy_and_retains_previous(tmp_path):
    make_key(tmp_path)
    local=tmp_path/"profile"
    prior=local/"Prometheus"/"Recovery"/"Staged-PRK"
    prior.mkdir(parents=True)
    (prior/"sentinel.txt").write_text("previous good recovery")
    result=stage(tmp_path,local)
    assert result.returncode==0, result.stderr
    assert (prior/"src"/"a.txt").read_text()=="source"
    assert list(prior.parent.glob("Staged-PRK.previous-*/sentinel.txt"))
    metadata=json.loads((prior.parent/"pending-recovery.json").read_text(encoding="utf-8-sig"))
    assert metadata["verified_files"]==5

def test_old_consistent_stage_is_not_current_key(tmp_path):
    key=make_key(tmp_path)
    local=tmp_path/"local"
    target=tmp_path/"stage"
    assert json.loads(run_ps("repair",tmp_path,target,local).stdout)["success"]
    manifest=json.loads((key/"PRK-MANIFEST.json").read_text())
    manifest["created"]="new-generation"
    (key/"PRK-MANIFEST.json").write_text(json.dumps(manifest))
    health=json.loads(run_ps("health",tmp_path,target,local).stdout)
    assert next(x for x in health["channels"] if x["name"]=="staging")["state"]=="repairable"
