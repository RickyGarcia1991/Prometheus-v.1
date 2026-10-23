import datetime,hashlib,json,shutil,subprocess
from pathlib import Path
B=Path(__file__).resolve().parent
PS=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
q=B/'windows-enrollment-r5-qualification.json';data=json.loads(q.read_text())
assert data['qualified'] is True and data['activated'] is True
rows=data['files']+[{'path':str(q),'checkpoint_path':'qualification.json','sha256':sha(q)}]
assert len({r['checkpoint_path'] for r in rows})==len(rows)
for r in rows:
 assert not Path(r['checkpoint_path']).is_absolute() and '..' not in Path(r['checkpoint_path']).parts
 assert sha(Path(r['path']))==r['sha256']
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');results=[]
for label,size in [('Prometheus-2TB',1950372327424),('PROMETHEUS',32076988416)]:
 volumes=json.loads(subprocess.check_output([str(PS),'-NoProfile','-Command','Get-Volume | Select-Object DriveLetter,FileSystemLabel,Size,SizeRemaining,HealthStatus | ConvertTo-Json'],text=True))
 matches=[v for v in volumes if v['FileSystemLabel']==label and v['Size']==size and v['HealthStatus']=='Healthy']
 assert len(matches)==1
 v=matches[0];assert v['SizeRemaining']>sum(Path(r['path']).stat().st_size for r in rows)+1048576
 target=Path(v['DriveLetter']+':/')/'Prometheus-Repository-Checkpoints'/('windows-enrollment-r5-'+stamp)
 assert str(target.absolute()).casefold()==str(target.resolve()).casefold()
 target.mkdir(parents=True,exist_ok=False)
 for r in rows:
  dest=target/r['checkpoint_path'];dest.parent.mkdir(parents=True,exist_ok=True)
  shutil.copyfile(r['path'],dest)
  assert sha(dest)==r['sha256'],r['checkpoint_path']
 results.append({'label':label,'size':size,'path':str(target),'verified_files':len(rows),'mismatches':0})
(B/'windows-enrollment-r5-drive-receipt.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results))
