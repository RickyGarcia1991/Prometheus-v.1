"""Read-only qualification of already archived model chunks for backup reuse."""
import datetime, hashlib, json, math, re, stat, subprocess
from pathlib import Path
B=Path(__file__).resolve().parent
A=Path('C:/Users/rolon/Documents/Codex/Recovery-Archives/Flash-Gemma4-26b-20261022')
PS=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
plan=json.loads((A/'backup-plan.json').read_bytes())
inventory=json.loads((B/'ubuntu-backup-file-inventory.private.json').read_bytes())['files']
def volume():
    values=json.loads(subprocess.check_output([str(PS),'-NoProfile','-Command','Get-Volume | Select-Object DriveLetter,FileSystemLabel,Size,HealthStatus | ConvertTo-Json'],text=True))
    matches=[v for v in values if v['FileSystemLabel']=='Prometheus-2TB' and v['Size']==1950372327424 and v['HealthStatus']=='Healthy']
    if len(matches)!=1:raise ValueError('SSD identity changed')
    return Path(matches[0]['DriveLetter']+':/')
def stream(paths):
    h=hashlib.sha256();total=0
    for p in paths:
        for node in (p,*p.parents):
            if node.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:raise ValueError('Reparse refused')
        before=p.stat()
        with p.open('rb') as f:
            while block:=f.read(4*1024*1024):h.update(block);total+=len(block)
        after=p.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Input changed during verification')
    return h.hexdigest(),total
rows=[];private=[]
for item in plan['files']:
    digest=item['digest'];size=item['size']
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid digest')
    matches=[r for r in inventory if r['path'].endswith('sha256-'+digest) and r['bytes']==size]
    if len(matches)!=1:raise ValueError('Unique SSD blob required')
    root=volume();source=root/matches[0]['path']
    source.resolve().relative_to(root.resolve())
    count=math.ceil(size/plan['chunk_size'])
    folder=A/'archive'/digest
    parts=[folder/f'part-{i:05d}' for i in range(count)]
    if set(folder.iterdir())!=set(parts):raise ValueError('Exact archive chunk set required')
    for i,p in enumerate(parts):
        if p.stat().st_size!=min(plan['chunk_size'],size-i*plan['chunk_size']):raise ValueError('Chunk length mismatch')
    if stream(parts)!=(digest,size):raise ValueError('Archived reconstruction mismatch')
    if stream([source])!=(digest,size):raise ValueError('SSD blob mismatch')
    row=dict(sha256=digest,bytes=size,chunks=count,archive_reconstruction_verified=True,ssd_bytes_verified=True)
    rows.append(row);private.append(dict(**row,source=str(source),archive=str(folder)))
    print(json.dumps(row),flush=True)
result=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=rows,reusable_bytes=sum(r['bytes'] for r in rows),read_only=True,full_backup_complete=False,restore_to_disk_tested=False,qualification='Exact reconstruction stream and current source byte equality; future backup must bind and retain this existing off-SSD archive.')
(B/'ubuntu-existing-model-backup-public.json').write_text(json.dumps(result,indent=2))
(B/'ubuntu-existing-model-backup.private.json').write_text(json.dumps(private,indent=2))
