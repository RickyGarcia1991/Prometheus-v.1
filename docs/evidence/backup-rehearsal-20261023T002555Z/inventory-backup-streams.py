"""Read-only NTFS alternate-stream sizing, with an isolated native self-check."""
import ctypes as c
from ctypes import wintypes as w
import datetime, json, os, stat, subprocess, tempfile, uuid
from pathlib import Path

B=Path(__file__).resolve().parent
K=c.WinDLL('kernel32',use_last_error=True)
class StreamData(c.Structure):
    _fields_=[('size',c.c_longlong),('name',w.WCHAR*296)]
K.FindFirstStreamW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(StreamData),w.DWORD]
K.FindFirstStreamW.restype=w.HANDLE
K.FindNextStreamW.argtypes=[w.HANDLE,c.POINTER(StreamData)]
K.FindNextStreamW.restype=w.BOOL
K.FindClose.argtypes=[w.HANDLE];K.FindClose.restype=w.BOOL
INVALID=c.c_void_p(-1).value
def streams(path):
    data=StreamData();h=K.FindFirstStreamW(str(path),0,c.byref(data),0)
    if h==INVALID:
        err=c.get_last_error()
        if err==38:return []
        raise OSError(err,'FindFirstStreamW failed',str(path))
    rows=[]
    try:
        while True:
            if data.name!='::$DATA':rows.append(dict(name=data.name,bytes=data.size))
            if not K.FindNextStreamW(h,c.byref(data)):
                err=c.get_last_error()
                if err!=38:raise OSError(err,'FindNextStreamW failed',str(path))
                return rows
    finally:
        if not K.FindClose(h):raise OSError(c.get_last_error(),'FindClose failed')

fixture=Path(tempfile.gettempdir())/('prometheus-stream-inventory-'+uuid.uuid4().hex)
fixture.mkdir();plain=fixture/'plain.bin';plain.write_bytes(b'base')
assert streams(plain)==[]
Path(str(plain)+':fixture').write_bytes(b'1234567')
assert streams(plain)==[dict(name=':fixture:$DATA',bytes=7)]
Path(str(fixture)+':directory-fixture').write_bytes(b'123')
assert streams(fixture)==[dict(name=':directory-fixture:$DATA',bytes=3)]
# Only exact files and streams created above are removed; no recursive deletion.
os.unlink(str(plain)+':fixture');plain.unlink()
os.unlink(str(fixture)+':directory-fixture');fixture.rmdir()
ps=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
vols=json.loads(subprocess.check_output([str(ps),'-NoProfile','-Command',
 'Get-Volume | Select-Object DriveLetter,FileSystemLabel,FileSystem,Size,HealthStatus | ConvertTo-Json'],text=True))
found=[v for v in vols if v['FileSystemLabel']=='Prometheus-2TB' and v['Size']==1950372327424 and v['HealthStatus']=='Healthy' and v['FileSystem']=='NTFS']
assert len(found)==1 and found[0]['DriveLetter']
root=Path(found[0]['DriveLetter']+':/')
stack=[root];records=[];errors=[];reparses=[];count=0
while stack:
    directory=stack.pop()
    paths=[directory]
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                p=Path(entry.path)
                if directory==root and entry.name in ('$RECYCLE.BIN','System Volume Information'):continue
                try:
                    s=entry.stat(follow_symlinks=False)
                    if s.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        reparses.append(p.relative_to(root).as_posix());continue
                    if entry.is_dir(follow_symlinks=False):stack.append(p)
                    else:paths.append(p)
                except OSError as e:errors.append(dict(path=str(p),error=str(e)))
        for p in paths:
            try:
                rows=streams(p);count+=1
                if rows:records.append(dict(path=p.relative_to(root).as_posix(),streams=rows))
            except OSError as e:errors.append(dict(path=str(p),error=str(e)))
    except OSError as e:errors.append(dict(path=str(directory),error=str(e)))
    if count//25000 != (count-len(paths))//25000:print(json.dumps(dict(entries=count,stream_files=len(records),errors=len(errors))),flush=True)
public=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),entries_checked=count,
 named_stream_count=sum(len(r['streams']) for r in records),named_stream_bytes=sum(s['bytes'] for r in records for s in r['streams']),
 errors=len(errors),reparses=len(reparses),native_selfcheck_passed=True,read_only_source=True,
 consistent_snapshot=False,backup_created=False,excluded=['$RECYCLE.BIN','System Volume Information'])
(B/'ubuntu-backup-streams.private.json').write_text(json.dumps(dict(summary=public,records=records,errors=errors,reparses=reparses),indent=2))
(B/'ubuntu-backup-streams-public.json').write_text(json.dumps(public,indent=2))
print(json.dumps(public),flush=True)
