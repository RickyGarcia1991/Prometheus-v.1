"""Metadata-only backup sizing; no file contents, drive changes, or copy."""
import datetime
import json
import os
from pathlib import Path
import stat
import subprocess

B=Path(__file__).resolve().parent
ps=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
vols=json.loads(subprocess.check_output([str(ps),'-NoProfile','-Command','Get-Volume | Select-Object DriveLetter,FileSystemLabel,Size,SizeRemaining,HealthStatus | ConvertTo-Json'],text=True))
matches=[v for v in vols if v['FileSystemLabel']=='PROMETHEUS' and v['Size']==32076988416 and v['HealthStatus']=='Healthy']
assert len(matches)==1
volume=matches[0];root=Path(volume['DriveLetter']+':/')
rows=[];errors=[];reparse=[];system=[];encrypted=[];top={}
def error(e): errors.append(dict(path=str(e.filename),error=type(e).__name__))
stack=[root];directories=0
while stack:
    directory=stack.pop();directories+=1
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                p=Path(entry.path)
                try:
                    s=entry.stat(follow_symlinks=False);rel=p.relative_to(root).as_posix()
                    if s.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        reparse.append(rel);continue
                    if entry.is_dir(follow_symlinks=False):
                        if directory==root and entry.name in ('$RECYCLE.BIN','System Volume Information'):
                            system.append(rel)
                        else: stack.append(p)
                        continue
                    if s.st_file_attributes & stat.FILE_ATTRIBUTE_ENCRYPTED: encrypted.append(rel)
                    rows.append(dict(path=rel,bytes=s.st_size,mtime_ns=s.st_mtime_ns,attributes=s.st_file_attributes))
                    key=rel.split('/')[0];top[key]=top.get(key,0)+s.st_size
                except OSError as e:error(e)
    except OSError as e:error(e)
    if directories%1000==0: print(json.dumps(dict(directories=directories,files=len(rows),pending_directories=len(stack))),flush=True)
local=next(v for v in vols if v['DriveLetter']=='C')
summary=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),volume=volume,file_count=len(rows),logical_file_bytes=sum(r['bytes'] for r in rows),top_level_bytes=top,errors=errors,reparse_entries=reparse,excluded_system_directories=system,encrypted_files=encrypted,internal_free_bytes=local['SizeRemaining'],backup_created=False,snapshot_consistency=False,content_hashes_verified=False)
(B/'ubuntu-flash-file-inventory.private.json').write_text(json.dumps(dict(summary=summary,files=rows),indent=2))
(B/'ubuntu-flash-capacity.private.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k not in ('top_level_bytes','encrypted_files','reparse_entries')},indent=2))
