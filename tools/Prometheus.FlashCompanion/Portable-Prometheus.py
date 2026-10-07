"""Portable Windows companion: isolated host workspace and verified USB checkpoints."""
import argparse, datetime, hashlib, json, os, pathlib, platform, shutil, socket, sqlite3, subprocess, sys, time, uuid, ctypes, struct, re
from urllib.request import build_opener, ProxyHandler
from contextlib import closing
P = pathlib.Path
MODEL = 'llama3.2:1b-instruct-q4_K_M'
def hardware():
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','pageTotal','pageAvailable','virtualTotal','virtualAvailable','extended')]
    mem=Memory(); mem.length=ctypes.sizeof(mem)
    if not ctypes.WinDLL('kernel32',use_last_error=True).GlobalMemoryStatusEx(ctypes.byref(mem)): raise ctypes.WinError(ctypes.get_last_error())
    if mem.total<4*1024**3 or mem.available<512*1024**2: raise RuntimeError('Needs 4 GiB installed RAM and 512 MiB currently available')
    return dict(host=platform.node(),windowsBuild=sys.getwindowsversion().build,cpuThreads=os.cpu_count(),ramBytes=mem.total,availableRamBytes=mem.available,checkedAt=now(),gpuSelection='Ollama automatic supported-device selection; no driver installation')
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    q=p.with_name(p.name+'.tmp'); q.write_text(json.dumps(v,indent=2),encoding='utf-8'); os.replace(q,p)
def safe_relative(root,name, sibling=False):
    p=(root/name).resolve()
    boundary=root.parent.resolve() if sibling else root.resolve()
    if not p.is_relative_to(boundary): raise RuntimeError('Path outside package drive')
    return p
def verify(root,entries,sibling=False):
    for e in entries:
        p=safe_relative(root,e['path'],sibling)
        if not p.is_file() or p.stat().st_size!=e['size'] or sha(p)!=e['sha256']: raise RuntimeError('Checksum failed: '+e['path'])
def package_fingerprint(cfg):
    return hashlib.sha256(json.dumps(cfg,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
def stage_snapshot(usb,cfg):
    files={}
    for e in cfg['files']:
        p=safe_relative(usb,e['path'],True); s=p.stat()
        files[e['path']]=[s.st_size,s.st_mtime_ns]
    return files
def fast_stage_ok(work,usb,cfg):
    marker=work/'stage-verification.json'
    if not marker.is_file(): return False
    try:
        cached=json.loads(marker.read_text(encoding='utf-8'))
        verified=datetime.datetime.fromisoformat(cached['verifiedAt'])
        if datetime.datetime.now(datetime.timezone.utc)-verified>datetime.timedelta(days=7): return False
        if cached.get('fingerprint')!=package_fingerprint(cfg): return False
        current=stage_snapshot(usb,cfg)
        if cached.get('usbFiles')!=current: return False
        for e in cfg['files']:
            original=safe_relative(usb,e['path'],True)
            for name in ('release','models','ollama'):
                base=safe_relative(usb,cfg[name],True)
                if original.is_relative_to(base):
                    local=work/name/original.relative_to(base)
                    if not local.is_file() or local.stat().st_size!=e['size']: return False
                    break
        return True
    except (OSError,ValueError,KeyError,TypeError):
        return False
def save_stage_verification(work,usb,cfg):
    put(work/'stage-verification.json',dict(
        fingerprint=package_fingerprint(cfg),verifiedAt=now(),usbFiles=stage_snapshot(usb,cfg)))
def dbcopy(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b)
        if b.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or b.execute('PRAGMA foreign_key_check').fetchall(): raise RuntimeError('Database check failed')
def local_root(identity):
    if not re.fullmatch('[0-9a-f]{32}',identity): raise RuntimeError('Invalid companion identity')
    return P(os.environ['LOCALAPPDATA'])/'PrometheusPortable'/identity
class OwnedJob:
    """Windows closes this job on supervisor exit/crash, terminating owned descendants."""
    def __init__(self):
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.restype=ctypes.c_void_p
        self.api.SetInformationJobObject.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint]
        self.api.AssignProcessToJobObject.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        self.api.CloseHandle.argtypes=[ctypes.c_void_p]
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        info=ctypes.create_string_buffer(144); struct.pack_into('<I',info,16,0x2000)
        if not self.api.SetInformationJobObject(self.handle,9,info,144):
            self.close(); raise ctypes.WinError(ctypes.get_last_error())
    def add(self,child):
        if not self.api.AssignProcessToJobObject(self.handle,int(child._handle)):
            child.terminate(); child.wait(); raise ctypes.WinError(ctypes.get_last_error())
    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None
def attached(usb,identity):
    try: return json.loads((usb/'portable.json').read_text(encoding='utf-8'))['identity']==identity
    except (OSError,ValueError,KeyError): return False
def windows_eject_readiness(usb):
    if platform.system()!='Windows': return dict(ready=False,detail='Windows eject verification is only available on Windows.')
    drive=usb.drive.rstrip('\\').upper()
    if not re.fullmatch(r'[A-Z]:',drive): return dict(ready=False,detail='USB drive letter could not be resolved safely.')
    ps="$d='"+drive+"'; $all=@(Get-CimInstance Win32_Process); $own=@(); $p=$all|Where-Object ProcessId -eq $PID|Select-Object -First 1; while($p){$own+=[int]$p.ProcessId; if(!$p.ParentProcessId){break}; $next=[int]$p.ParentProcessId; $p=$all|Where-Object ProcessId -eq $next|Select-Object -First 1}; $refs=@($all|Where-Object { $_.CommandLine -and $_.CommandLine -match ('(?i)(^|[^A-Z])'+[regex]::Escape($d+'\\')) -and [int]$_.ProcessId -notin $own }|Select-Object -ExpandProperty ProcessId); [pscustomobject]@{refs=$refs.Count; pids=($refs -join ',')} | ConvertTo-Json -Compress"
    cp=subprocess.run(['powershell.exe','-NoProfile','-Command',ps],capture_output=True,text=True,timeout=15)
    if cp.returncode!=0: return dict(ready=False,detail='Windows process-reference check failed.')
    try: data=json.loads(cp.stdout.strip())
    except ValueError: return dict(ready=False,detail='Windows process-reference result was invalid.')
    refs=int(data.get('refs',0)); own={os.getpid(),os.getppid(),int(os.environ.get('PROMETHEUS_VERIFY_CALLER_PID','0') or 0)}; pids=[int(x) for x in str(data.get('pids','')).split(',') if x.strip().isdigit() and int(x) not in own]; refs=len(pids); data['pids']=','.join(map(str,pids))
    if refs: return dict(ready=False,detail='Windows still reports process references to '+drive,processReferences=refs,pids=data.get('pids',''))
    return dict(ready=True,detail='No process command lines reference the USB drive. Ready to attempt normal Windows eject.',processReferences=0)
def wait_for_clients(children,work,usb,identity,state,seconds=60):
    deadline=time.monotonic()+seconds
    while True:
        if not attached(usb,identity):
            raise RuntimeError('Drive disconnected during shutdown; local recovery retained')
        clients=sum(c.poll() is None for c in children[1:])
        remaining=max(0,int(deadline-time.monotonic()+0.999))
        state.update(state='Stopping',checkedAt=now(),activeClients=clients,shutdownSecondsRemaining=remaining)
        put(work/'status.json',state)
        if not clients: return
        if time.monotonic()>=deadline:
            state['shutdownTimedOut']=True
            state['shutdownDetail']='Grace period expired; unfinished answers may be lost. Committed history will be checked before saving.'
            put(work/'status.json',state)
            return
        time.sleep(min(1,max(0,deadline-time.monotonic())))
def checkpoint(work,usb,identity):
    if not attached(usb,identity): raise RuntimeError('USB absent; local recovery retained at '+str(work))
    src=work/'profile/Prometheus'; required=sum(p.stat().st_size for p in src.rglob('*') if p.is_file())
    if shutil.disk_usage(usb).free<required+64*1024*1024: raise RuntimeError('USB space low; local recovery retained')
    folder=usb/'checkpoints'/('checkpoint-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True); entries=[]
    for p in src.rglob('*'):
        if not p.is_file() or p.name.endswith(('-wal','-shm','.sqlite3.json')): continue
        rel=p.relative_to(src); target=folder/'Prometheus'/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        if p.suffix=='.sqlite3': dbcopy(p,target)
        else: shutil.copy2(p,target)
        entries.append(dict(path=target.relative_to(folder).as_posix(),size=target.stat().st_size,sha256=sha(target)))
    memory=folder/'Prometheus/memory.sqlite3'
    if memory.exists():
        with closing(sqlite3.connect(memory)) as db: counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('sessions','turns')}
        meta=memory.with_suffix('.sqlite3.json'); put(meta,dict(sha256=sha(memory),**counts))
        entries.append(dict(path=meta.relative_to(folder).as_posix(),size=meta.stat().st_size,sha256=sha(meta)))
    shutil.copy2(work/'status.json',folder/'host-status.json')
    entries.append(dict(path='host-status.json',size=(folder/'host-status.json').stat().st_size,sha256=sha(folder/'host-status.json')))
    verify(folder,entries); put(folder/'manifest.json',dict(createdAt=now(),identity=identity,files=entries))
    put(usb/'latest-checkpoint.json',dict(path=folder.relative_to(usb).as_posix(),identity=identity,verifiedAt=now()))
    return str(folder)
def stop_children(children):
    # Only Popen objects created by this supervisor; never enumerate/kill unrelated Ollama.
    for c in reversed(children):
        if c.poll() is None:
            # Include model-runner descendants, using only this supervisor's live PID.
            subprocess.run(['taskkill','/PID',str(c.pid),'/T','/F'],capture_output=True,check=True)
            try: c.wait(timeout=20)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill','/PID',str(c.pid),'/T','/F'],capture_output=True,check=True)
                c.wait(timeout=10)
def supervise(work,usb,identity):
    import msvcrt
    lock=(work/'run.lock').open('a+b'); lock.write(b'0'); lock.flush(); lock.seek(0)
    try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError: raise RuntimeError('This portable workspace is already running')
    children=[]; job=OwnedJob(); state=dict(identity=identity,host=platform.node(),startedAt=now(),workspace=str(work),state='Starting',hardware=json.loads((work/'setup-check.json').read_text()),crashContainment='Windows kill-on-close owned process job')
    (work/'temp').mkdir(exist_ok=True)
    env=os.environ.copy(); env.update(LOCALAPPDATA=str(work/'profile'),USERPROFILE=str(work/'profile'),HOME=str(work/'profile'),TEMP=str(work/'temp'),TMP=str(work/'temp'),OLLAMA_MODELS=str(work/'models'),OLLAMA_NO_CLOUD='1',OLLAMA_NOPRUNE='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_NUM_PARALLEL='1',OLLAMA_KEEP_ALIVE='1m')
    with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
    env['OLLAMA_HOST']='127.0.0.1:'+str(port); state['baseUrl']='http://'+env['OLLAMA_HOST']
    (work/'stop.request').unlink(missing_ok=True); log=(work/'ollama.log').open('ab')
    try:
        server=subprocess.Popen([str(work/'ollama/ollama.exe'),'serve'],env=env,cwd=work/'profile',stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW); children.append(server)
        job.add(server)
        state['serverPid']=server.pid; opener=build_opener(ProxyHandler({})); ready=False
        for _ in range(90):
            if not attached(usb,identity) or server.poll() is not None: raise RuntimeError('USB disconnected or portable server stopped during startup')
            try:
                with opener.open(state['baseUrl']+'/api/tags',timeout=1) as r: data=json.loads(r.read(1024*1024))
                if MODEL not in [m['name'] for m in data.get('models',[])]: raise RuntimeError('Portable model missing')
                ready=True; break
            except OSError: time.sleep(1)
        if not ready: raise RuntimeError('Portable server did not become ready')
        state['state']='Running'; state['cpuThreads']=os.cpu_count(); put(work/'status.json',state)
        while not (work/'stop.request').exists():
            if not attached(usb,identity): raise RuntimeError('USB disconnected; stopped processing; local recovery retained')
            if server.poll() is not None: raise RuntimeError('Portable server exited; local recovery retained')
            if shutil.disk_usage(work).free<256*1024*1024: raise RuntimeError('Host disk space low; stopped before further work')
            if int(time.monotonic())%15==0:
                with opener.open(state['baseUrl']+'/api/tags',timeout=3) as r: r.read(1024*1024)
            request=work/'chat.request'
            if request.exists():
                request.unlink(); child=subprocess.Popen([str(work/'release/runtime/python/python.exe'),'-X','utf8',str(work/'release/prometheus.py'),'--memory',str(work/'profile/Prometheus/memory.sqlite3'),'--base-url',state['baseUrl'],'--model',MODEL,'chat'],env=env,cwd=work,creationflags=subprocess.CREATE_NEW_CONSOLE); children.append(child)
                job.add(child)
            state['checkedAt']=now(); state['activeClients']=sum(c.poll() is None for c in children[1:]); put(work/'status.json',state); time.sleep(1)
        state['state']='Stopping'; put(work/'status.json',state)
        # Give interactive clients time to exit and commit normally before stopping.
        wait_for_clients(children,work,usb,identity,state)
        stop_children(children)
        state['state']='Stopped'; state['stoppedAt']=now(); put(work/'status.json',state)
        state['checkpoint']=checkpoint(work,usb,identity); state['state']='StoppedVerified'; state['safeToEject']=False; state['ejectDetail']='Prometheus data is verified; Windows device release has not yet been confirmed.'; put(work/'status.json',state)
        put(usb/'last-run.json',state)
    except Exception as e:
        stop_children(children); state.update(state='NeedsAttention',error=str(e),stoppedAt=now()); put(work/'status.json',state)
        # No USB writes on an unexpected failure. Existing verified checkpoints remain intact.
    finally: job.close(); log.close(); lock.close()
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['start','chat','status','stop','recover','verify-eject','cleanup','supervise']); parser.add_argument('--usb',type=P,default=P(__file__).resolve().parent); parser.add_argument('--work',type=P); a=parser.parse_args()
    if a.action=='supervise':
        cfg=json.loads((a.work/'portable.json').read_text()); supervise(a.work,a.usb,cfg['identity']); return
    usb=a.usb.resolve(); cfg=json.loads((usb/'portable.json').read_text()); work=local_root(cfg['identity'])
    if a.action=='start':
        if platform.system()!='Windows' or platform.machine().lower() not in ('amd64','x86_64'): raise RuntimeError('Requires Windows x64; other systems need a matching runtime')
        if sys.getwindowsversion().build<19045: raise RuntimeError('Requires Windows 10 22H2 or newer')
        work.mkdir(parents=True,exist_ok=True)
        host_info=hardware()
        # Refuse overlapping staging or a second supervisor through a held byte-range lock.
        import msvcrt
        guard=(work/'run.lock').open('a+b'); guard.write(b'0'); guard.flush(); guard.seek(0)
        try: msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: raise RuntimeError('Already running. Use Open Chat or Stop and Prepare to Eject.')
        try:
            reuse=fast_stage_ok(work,usb,cfg)
            if reuse:
                print('Verified stage cache unchanged; reusing local runtime.')
            else:
                print('Verifying portable files and refreshing local stage. This may take a few minutes.')
                verify(usb,cfg['files'],True)
                needed=sum(e['size'] for e in cfg['files'])+1024*1024*1024
                if shutil.disk_usage(work).free<needed: raise RuntimeError('Host needs at least '+str(round(needed/1024**3,1))+' GiB free for staging')
                for name in ['release','models','ollama']:
                    shutil.copytree(safe_relative(usb,cfg[name],True),work/name,dirs_exist_ok=True)
                for e in cfg['files']:
                    original=safe_relative(usb,e['path'],True)
                    for name in ('release','models','ollama'):
                        base=safe_relative(usb,cfg[name],True)
                        if original.is_relative_to(base):
                            local=work/name/original.relative_to(base)
                            if local.stat().st_size!=e['size'] or sha(local)!=e['sha256']: raise RuntimeError('Host staging checksum failed: '+str(local))
                            break
                save_stage_verification(work,usb,cfg)
            shutil.copy2(P(__file__),work/'Portable-Prometheus.py'); put(work/'portable.json',cfg)
            profile=work/'profile/Prometheus'; profile.mkdir(parents=True,exist_ok=True)
            # Existing host recovery is never overwritten. Import USB checkpoint only on first use.
            previous=json.loads((work/'status.json').read_text()) if (work/'status.json').exists() else {}
            if (profile/'memory.sqlite3').exists() and previous.get('state')!='ReadyToEject':
                raise RuntimeError('Local recovery exists from an unfinished run. Inspect Status before restarting; it has not been overwritten.')
            if not (profile/'memory.sqlite3').exists() or previous.get('state')=='ReadyToEject':
                source=safe_relative(usb,cfg['seed'],True)
                latest=usb/'latest-checkpoint.json'
                if latest.exists():
                    info=json.loads(latest.read_text()); source=safe_relative(usb,info['path']); manifest=json.loads((source/'manifest.json').read_text()); verify(source,manifest['files'])
                    if info.get('identity')!=cfg['identity'] or manifest.get('identity')!=cfg['identity']: raise RuntimeError('Checkpoint identity mismatch')
                # Only after a clean stop/verified save: stale SQLite journals must not
                # be applied to a newer checkpoint copied from another host.
                for database in [profile/'memory.sqlite3',profile/'resources/lookup-cache.sqlite3']:
                    for suffix in ('-wal','-shm'): P(str(database)+suffix).unlink(missing_ok=True)
                shutil.copytree(source/'Prometheus',profile,dirs_exist_ok=True,ignore=shutil.ignore_patterns('release'))
            put(work/'status.json',dict(state='Staged',identity=cfg['identity'],host=platform.node(),checkedAt=now(),workspace=str(work)))
            put(work/'setup-check.json',host_info)
        finally: guard.close()
        subprocess.Popen([str(work/'release/runtime/python/python.exe'),'-X','utf8',str(work/'Portable-Prometheus.py'),'supervise','--usb',str(usb),'--work',str(work)],cwd=work,creationflags=subprocess.CREATE_NO_WINDOW)
        print('Background companion starting. Use Status, then Open Chat. Workspace:',work)
    elif a.action=='chat':
        info=json.loads((work/'status.json').read_text())
        if info['state']!='Running' or (datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(info['checkedAt'])).total_seconds()>10: raise RuntimeError('Start companion first')
        (work/'chat.request').write_text(now()); print('Opening chat window.')
    elif a.action=='stop':
        (work/'stop.request').write_text(now())
        for _ in range(90):
            info=json.loads((work/'status.json').read_text())
            if info['state'] in ('StoppedVerified','ReadyToEject','NeedsAttention'):
                print(json.dumps(info,indent=2)); return
            time.sleep(1)
        raise RuntimeError('Stop has not finished. Do not eject yet; inspect Status.')
    elif a.action=='recover':
        import msvcrt
        with (work/'run.lock').open('r+b') as guard:
            msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
            info=json.loads((work/'status.json').read_text()); info.update(state='Stopped',recoveredAt=now()); put(work/'status.json',info)
            info['checkpoint']=checkpoint(work,usb,cfg['identity']);info['state']='StoppedVerified';info['safeToEject']=False;info['ejectDetail']='Prometheus data is verified; Windows device release has not yet been confirmed.'
            if 'error' in info:info['previousError']=info.pop('error')
            put(work/'status.json',info);put(usb/'last-run.json',info)
        print(json.dumps(info,indent=2))
    elif a.action=='verify-eject':
        info=json.loads((work/'status.json').read_text())
        if info.get('state') not in ('StoppedVerified','ReadyToEject'): raise RuntimeError('Stop and verify Prometheus data before checking Windows eject readiness')
        result=windows_eject_readiness(usb)
        info.update(safeToEject=bool(result['ready']),ejectDetail=result['detail'],ejectCheckedAt=now(),windowsEjectCheck=result)
        info['state']='ReadyToEject' if result['ready'] else 'StoppedVerified'
        put(work/'status.json',info); put(usb/'last-run.json',info)
        print(json.dumps(info,indent=2))
    elif a.action=='cleanup':
        info=json.loads((work/'status.json').read_text())
        if info.get('state') not in ('StoppedVerified','ReadyToEject'): raise RuntimeError('A clean verified stop is required before cleanup')
        latest=json.loads((usb/'latest-checkpoint.json').read_text()); folder=safe_relative(usb,latest['path']); manifest=json.loads((folder/'manifest.json').read_text()); verify(folder,manifest['files'])
        if latest.get('identity')!=cfg['identity'] or manifest.get('identity')!=cfg['identity']: raise RuntimeError('Recovery identity mismatch')
        import msvcrt
        with (work/'run.lock').open('r+b') as guard:
            msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
        expected=(P(os.environ['LOCALAPPDATA'])/'PrometheusPortable').resolve()
        if work.resolve().parent!=expected or work.name!=cfg['identity']: raise RuntimeError('Invalid cleanup target')
        shutil.rmtree(work); print('Removed only this companion host workspace. USB backups retained.')
    else: print((work/'status.json').read_text())
if __name__=='__main__':
    try: main()
    except Exception as e: print('Portable companion:',e); sys.exit(1)

