"""Copy only the frozen, hash-bound qualification list to identified drives."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parent
PWSH=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    qualification=ROOT/'enrollment-final-qualification.json'
    data=json.loads(qualification.read_text())
    assert data['qualified'] is True and data['activated'] is False
    files={}
    for row in data['files']:
        name=row['checkpoint_path']
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert name not in files
        p=Path(row['path']);assert sha(p)==row['sha256'],name
        files[name]=p
    files['qualification.json']=qualification
    volumes=json.loads(subprocess.check_output([str(PWSH),'-NoProfile','-Command',
        'Get-Volume | Select-Object DriveLetter,FileSystemLabel,Size,HealthStatus | ConvertTo-Json'],text=True))
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    results=[]
    for label,size in [('Prometheus-2TB',1950372327424),('PROMETHEUS',32076988416)]:
        matches=[v for v in volumes if v['FileSystemLabel']==label and v['Size']==size and v['HealthStatus']=='Healthy']
        assert len(matches)==1,'Drive identity ambiguous or unavailable'
        target=Path(matches[0]['DriveLetter']+':/')/'Prometheus-Repository-Checkpoints'/('enrollment-'+stamp)
        target.mkdir(parents=True,exist_ok=False)
        for name,p in files.items():
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(p,dest)
            assert sha(dest)==sha(p),name
        results.append(dict(label=label,path=str(target),verified_files=len(files),mismatches=0))
    (ROOT/'enrollment-drive-receipt.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results))

if __name__=='__main__':main()
