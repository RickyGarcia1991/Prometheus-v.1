"""Stage the exact enrollment closure; no signing or activation."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parent
SOURCE=Path('C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/source/tools/Prometheus.HostAgent')
TARGET=ROOT/'reconnect-enrollment-candidate-r1'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    build=ROOT/'reconnect-quiet-build'
    receipt=json.loads((build/'quiet-host-build-receipt.json').read_text())
    assert receipt['source']['sha256']==sha(SOURCE/'Prometheus-QuietHost-v3.cs')
    assert receipt['output_sha256']==sha(build/'Prometheus-QuietHost-v3.exe')
    local=Path.home()/'AppData/Local'
    legacy=local/'DesktopCommanderStartup/Run-DesktopCommander.ps1'
    runner=legacy if legacy.exists() else local/'DesktopCommander/Start-AtSignIn.ps1'
    assert runner.is_file()
    TARGET.mkdir(exist_ok=False)
    shutil.copyfile(build/'Prometheus-QuietHost-v3.exe',TARGET/'Prometheus-QuietHost-v3.exe')
    for name in ['Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1',
                 'Prometheus-Arrival.ps1','Prometheus-Host-Agent.ps1','ReconnectEnrollment.psm1']:
        shutil.copyfile(SOURCE/name,TARGET/name)
    policy=dict(schema=1,mode='reconnect',quiet_host='Prometheus-QuietHost-v3.exe',external_runner_sha256=sha(runner))
    (TARGET/'RECONNECT-ENROLLMENT.json').write_text(json.dumps(policy,indent=2))
    rows=[dict(path=p.name,sha256=sha(p),size_bytes=p.stat().st_size) for p in sorted(TARGET.iterdir())]
    assert len(rows)==7
    manifest=TARGET/'SHA256-MANIFEST.json'
    manifest.write_text(json.dumps(rows,indent=2))
    record=dict(path=str(TARGET),manifest_sha256=sha(manifest),runner_sha256=sha(runner),activated=False)
    (ROOT/'enrollment-candidate-index.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record))

if __name__=='__main__':main()
