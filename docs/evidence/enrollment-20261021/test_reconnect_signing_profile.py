import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import pytest

ROOT=Path(__file__).resolve().parent
PWSH=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
NAMES=['Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1',
       'Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-Host-Agent.ps1','ReconnectEnrollment.psm1']

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@pytest.mark.parametrize('mutation',['none','extra','missing','array','schema','mode','quiet_host','external_runner_sha256','field','changed','traversal'])
def test_fixed_enrollment_profile(mutation):
    root=Path(tempfile.mkdtemp(prefix='enrollment-profile-',dir=ROOT))
    try:
        for name in NAMES:(root/name).write_bytes(b'non-executable fixture')
        policy=dict(schema=1,mode='reconnect',quiet_host='Prometheus-QuietHost-v3.exe',external_runner_sha256='a'*64)
        if mutation=='schema':policy['schema']='1'
        elif mutation=='array':policy['mode']=['reconnect']
        elif mutation in ['mode','quiet_host','external_runner_sha256']:policy[mutation]='invalid'
        elif mutation=='field':policy['extra']=True
        (root/'RECONNECT-ENROLLMENT.json').write_text(json.dumps(policy))
        if mutation=='extra':(root/'unlisted.exe').write_bytes(b'bad')
        if mutation=='missing':(root/'ReconnectEnrollment.psm1').unlink()
        rows=[dict(path=p.name,sha256=sha(p),size_bytes=p.stat().st_size) for p in root.iterdir()]
        if mutation=='traversal':rows[0]['path']='../escape'
        (root/'SHA256-MANIFEST.json').write_text(json.dumps(rows))
        if mutation=='changed':(root/NAMES[0]).write_bytes(b'changed')
        before={p.name:sha(p) for p in root.iterdir()}
        result=subprocess.run([str(PWSH),'-NoProfile','-File',str(ROOT/'qualify-reconnect-enrollment.ps1'),
            '-Operation','Validate','-PackageKind','ReconnectEnrollment','-Candidate',str(root),
            '-ExpectedManifestSha256',sha(root/'SHA256-MANIFEST.json')],capture_output=True,text=True,timeout=20)
        assert (result.returncode==0)==(mutation=='none'),result.stdout+result.stderr
        assert before=={p.name:sha(p) for p in root.iterdir()}
    finally:
        assert root.parent==ROOT and root.name.startswith('enrollment-profile-')
        shutil.rmtree(root)
