"""Owned native AgentMemory lifecycle. Invoked only by the approved provider.

No persistent daemon, global hooks, model-selected commands or paths. The raw
engine socket is private loopback and has no independent bearer authentication;
this is not an operating-system sandbox against other same-user processes.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener
ROOT=Path(__file__).resolve().parent
MAX_MEMORY=1024*1024*1024
RESERVE_MEMORY=2*1024*1024*1024
BUNDLE=None
NODE=None


def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False)+'\n', encoding='utf-8')

def available_memory():
    class Memory(ctypes.Structure):
        _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD)]+[(name,ctypes.c_ulonglong) for name in
            ('total_phys','available_phys','total_page','available_page','total_virtual','available_virtual','extended')]
    memory=Memory();memory.length=ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError('Cannot read free physical memory')
    return memory.available_phys

def engine_config(ports, data):
    rest, stream, viewer, engine = ports
    def worker(name, config): return {'name':name, 'config':config}
    def store(path): return {'adapter':{'name':'kv','config':{'store_method':'file_based','save_interval_ms':2000,'file_path':path.as_posix()}}}
    return {'workers':[
        worker('iii-worker-manager', {'host':'127.0.0.1','port':engine}),
        worker('iii-http', {'host':'127.0.0.1','port':rest,'default_timeout':120000,
                           'cors':{'allowed_origins':[f'http://127.0.0.1:{viewer}'],'allowed_methods':['GET','POST']}}),
        worker('iii-state',store(data/'state_store.db')),
        worker('iii-stream',{'host':'127.0.0.1','port':stream,**store(data/'stream_store')}),
        worker('iii-queue',{'adapter':{'name':'builtin'}}),
        worker('iii-pubsub',{'adapter':{'name':'local'}}),
        worker('iii-cron',{'adapter':{'name':'kv'}}),
        worker('iii-observability',{'enabled':False}),
    ]}

def environment(home, data, runtime, ports, token, stop):
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC','PATHEXT','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS') if k in os.environ}
    env.update({
        'PATH':str(NODE.parent)+os.pathsep+str(BUNDLE/'engine')+os.pathsep+os.path.join(env['SystemRoot'],'System32'),
        'HOME':str(home),'USERPROFILE':str(home),'APPDATA':str(home/'AppData/Roaming'),
        'LOCALAPPDATA':str(home/'AppData/Local'),'TEMP':str(home/'Temp'),'TMP':str(home/'Temp'),
        'CI':'1','NO_COLOR':'1','III_TELEMETRY_ENABLED':'false','IIIWORKER_DISABLE_BUILTIN_DAEMONS':'1',
        'DO_NOT_TRACK':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
        'OMP_NUM_THREADS':'2','ORT_NUM_THREADS':'2','RAYON_NUM_THREADS':'2',
        'AGENTMEMORY_DATA_DIR':str(data),'AGENTMEMORY_RUNTIME_DIR':str(runtime),
        'AGENTMEMORY_SECRET':token,'AGENTMEMORY_STATE_BACKEND':'file',
        'AGENTMEMORY_INDEX_SAVE_INTERVAL_MS':'2000','AGENTMEMORY_AUDIT_RETENTION_MONTHS':'0',
        'EMBEDDING_PROVIDER':'local','RERANK_ENABLED':'false','AGENTMEMORY_VIEWER_HOST':'127.0.0.1',
        'PROMETHEUS_AGENTMEMORY_BUNDLE':str(BUNDLE),'PROMETHEUS_AGENTMEMORY_STOP':str(stop),
    })
    for flag in ('AGENTMEMORY_AUTO_COMPRESS','AGENTMEMORY_ALLOW_AGENT_SDK','CONSOLIDATION_ENABLED',
        'AUTO_FORGET_ENABLED','LESSON_DECAY_ENABLED','INSIGHT_DECAY_ENABLED','AGENTMEMORY_SESSION_SWEEP_ENABLED',
        'AGENTMEMORY_GRAPH_COMPACT_ON_BOOT','AGENTMEMORY_CAPTURE_SPOOL','AGENTMEMORY_INJECT_CONTEXT','CLAUDE_MEMORY_BRIDGE'):
        env[flag]='false'
    for key,port in zip(('III_REST_PORT','III_STREAM_PORT','III_VIEWER_PORT','III_ENGINE_PORT'),ports): env[key]=str(port)
    env['III_ENGINE_URL']=f'ws://127.0.0.1:{ports[3]}'
    env['VIEWER_ALLOWED_ORIGINS']=','.join(f'http://127.0.0.1:{p}' for p in ports[:3])
    env['VIEWER_ALLOWED_HOSTS']=','.join(f'127.0.0.1:{p}' for p in ports[:3])
    return env

class Basic(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),
        ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',wintypes.DWORD),
        ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]

class IO(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in ('reads','writes','other','read_bytes','write_bytes','other_bytes')]

class Limits(ctypes.Structure):
    _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
        ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]

class OwnedJob:
    """One kill-on-close Job Object; 1 GiB combined committed-memory limit."""
    def __init__(self):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,args,ret in [
          ('CreateJobObjectW',[ctypes.c_void_p,wintypes.LPCWSTR],wintypes.HANDLE),
          ('SetInformationJobObject',[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD],wintypes.BOOL),
          ('QueryInformationJobObject',[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p],wintypes.BOOL),
          ('AssignProcessToJobObject',[wintypes.HANDLE,wintypes.HANDLE],wintypes.BOOL),
          ('TerminateJobObject',[wintypes.HANDLE,wintypes.UINT],wintypes.BOOL),
          ('CloseHandle',[wintypes.HANDLE],wintypes.BOOL)]:
            getattr(self.k,name).argtypes=args;getattr(self.k,name).restype=ret
        self.handle=self.k.CreateJobObjectW(None,None)
        limit=Limits();limit.basic.flags=0x2000|0x200;limit.job_memory=MAX_MEMORY
        if not self.handle or not self.k.SetInformationJobObject(self.handle,9,ctypes.byref(limit),ctypes.sizeof(limit)):
            self.close();raise OSError('Cannot create bounded job')
    def add(self,process):
        if not self.k.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            process.kill();process.wait();raise OSError('Cannot own child process')
        nt=ctypes.WinDLL('ntdll');nt.NtResumeProcess.argtypes=[wintypes.HANDLE];nt.NtResumeProcess.restype=wintypes.LONG
        if nt.NtResumeProcess(wintypes.HANDLE(int(process._handle))) != 0:
            raise OSError('Cannot resume owned child')
    def peak(self):
        limit=Limits()
        if not self.k.QueryInformationJobObject(self.handle,9,ctypes.byref(limit),ctypes.sizeof(limit),None):
            raise OSError('Cannot query job memory')
        return limit.peak_job
    def close(self):
        if getattr(self,'handle',None):self.k.CloseHandle(self.handle);self.handle=None
    def terminate(self):
        if self.handle:self.k.TerminateJobObject(self.handle,1)

def private_ports():
    sockets=[]
    try:
        for _ in range(4):
            s=socket.socket();s.bind(('127.0.0.1',0));sockets.append(s)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:s.close()

def engine_ctrl_c(pid):
    # Signal only the hidden console created for our exact engine child.
    code='''import ctypes,sys,time
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):sys.exit(2)
k.SetConsoleCtrlHandler(None,True)
ok=k.GenerateConsoleCtrlEvent(0,0)
time.sleep(.3)
k.FreeConsole()
sys.exit(0 if ok else 3)
'''
    return subprocess.run([sys.executable,'-I','-B','-c',code,str(pid)],stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW).returncode

class Service:
    def __init__(self,run_root,round_no,deadline,cancel_file):
        self.root=run_root;self.round=round_no;self.deadline=deadline;self.cancel=cancel_file
        self.ports=private_ports();self.token=secrets.token_hex(32);self.processes=[];self.logs=[]
        self.stop=run_root/f'stop-{round_no}.flag';self.job=OwnedJob();self.closed=False
        self.memory_before=available_memory();self.minimum_available=self.memory_before
        self.resource_error=None;self.monitor_stop=threading.Event();self.monitor=None
        home=run_root/'home';data=run_root/'state';runtime=run_root/'runtime'
        # Engine persists its configuration separately and otherwise carries old
        # dynamic ports across restarts. Keep each control-plane cwd private;
        # the absolute memory store, HOME and indexes remain the same.
        self.cwd=run_root/f'process-{round_no}'
        for p in (home,home/'AppData/Roaming',home/'AppData/Local',home/'Temp',data,runtime,self.cwd):p.mkdir(parents=True,exist_ok=True)
        self.env=environment(home,data,runtime,self.ports,self.token,self.stop)
        config=run_root/f'engine-{round_no}.json';save(config,engine_config(self.ports,data))
        self.start_commands=[([str(BUNDLE/'engine/iii.exe'),'--config',str(config),'--no-update-check'],True),
            ([str(NODE),'--max-old-space-size=384',str(ROOT/'agent_memory_service_worker.mjs')],False)]
    def guard(self):
        if self.resource_error:raise RuntimeError(self.resource_error)
        if time.monotonic()>self.deadline:raise TimeoutError('Qualification deadline exceeded')
        if self.cancel and self.cancel.exists():raise InterruptedError('Qualification cancelled')
        if any((self.root/f'round-{self.round}-{i}.log').stat().st_size>32*1024*1024 for i in range(len(self.logs))):
            raise RuntimeError('Owned service log exceeded 32 MiB')
        for proc in self.processes:
            if proc.poll() is not None:raise RuntimeError('Owned service exited early; inspect per-round logs')
    def start(self):
        if self.memory_before<MAX_MEMORY+RESERVE_MEMORY:
            raise RuntimeError('RAM gate: 1 GiB job budget plus 2 GiB reserve is unavailable')
        def monitor():
            while not self.monitor_stop.wait(.5):
                try:
                    free=available_memory();self.minimum_available=min(self.minimum_available,free)
                    if free<RESERVE_MEMORY:
                        self.resource_error='RAM reserve fell below 2 GiB; owned processes stopped'
                        self.job.terminate();return
                except OSError:
                    self.resource_error='RAM guard failed; owned processes stopped'
                    self.job.terminate();return
        self.monitor=threading.Thread(target=monitor,daemon=True);self.monitor.start()
        for index,(cmd,console) in enumerate(self.start_commands):
            log=self.root/f'round-{self.round}-{index}.log'
            stream=log.open('wb');self.logs.append(stream)
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
            flags=0x4|(subprocess.CREATE_NEW_CONSOLE if console else subprocess.CREATE_NO_WINDOW)
            proc=subprocess.Popen(cmd,cwd=self.cwd,env=self.env,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                creationflags=flags,startupinfo=startup)
            self.processes.append(proc);self.job.add(proc)
            if console:
                end=time.monotonic()+30
                while not listening(self.ports[3]):
                    self.guard()
                    if time.monotonic()>end:raise TimeoutError('Engine worker socket did not open')
                    time.sleep(.2)
        end=time.monotonic()+120
        while True:
            self.guard()
            try:
                _,body=self.request('/agentmemory/livez',timeout=2)
                if body.get('status')=='ok':return
            except (URLError,TimeoutError,OSError,ValueError):pass
            if time.monotonic()>end:raise TimeoutError('Full worker did not become ready')
            time.sleep(.25)
    def request(self,path,body=None,auth=True,port=None,timeout=90):
        self.guard()
        headers={'Accept':'application/json'}
        if auth:headers['Authorization']='Bearer '+self.token
        if body is not None:headers['Content-Type']='application/json'
        request=Request(f'http://127.0.0.1:{port or self.ports[0]}'+path,
            data=None if body is None else json.dumps(body).encode(),headers=headers)
        opener=build_opener(ProxyHandler({}))
        try:response=opener.open(request,timeout=min(timeout,max(1,self.deadline-time.monotonic())))
        except HTTPError as exc:response=exc
        with response:
            payload=response.read(8*1024*1024+1)
            if len(payload)>8*1024*1024:raise RuntimeError('HTTP output exceeded 8 MiB')
            try:value=json.loads(payload)
            except ValueError:value={'text':payload.decode('utf-8','replace')[:1000]}
            return response.status,value
    def good(self,path,body=None):
        code,value=self.request(path,body)
        if code not in (200,201):raise RuntimeError(f'HTTP {code} at {path}: {str(value)[:500]}')
        return value
    def close(self):
        if self.closed:return self.close_result
        self.closed=True;result={'round':self.round,'worker_clean_exit':False,'engine_clean_exit':False}
        try:
            if len(self.processes)>1:
                worker=self.processes[1];self.stop.touch()
                try:worker.wait(timeout=15)
                except subprocess.TimeoutExpired:pass
                result['worker_clean_exit']=worker.poll()==0
            # Match upstream CLI's documented file-store quiescence wait.
            if self.processes and self.processes[0].poll() is None:
                time.sleep(3.5)
                result['ctrl_c_signal_exit']=engine_ctrl_c(self.processes[0].pid)
                try:self.processes[0].wait(timeout=20)
                except subprocess.TimeoutExpired:pass
            result['engine_clean_exit']=bool(self.processes and self.processes[0].poll()==0)
            result['peak_job_committed_bytes']=self.job.peak()
        finally:
            self.monitor_stop.set()
            if self.monitor:self.monitor.join(timeout=2)
            self.job.close()
            for proc in self.processes:
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            for stream in self.logs:stream.close()
            result['ports_closed']=all(not listening(p) for p in self.ports)
            result['owned_children_exited']=all(p.poll() is not None for p in self.processes)
            result['physical_memory_before_bytes']=self.memory_before
            result['minimum_available_physical_bytes']=self.minimum_available
            result['resource_error']=self.resource_error
            self.close_result=result
        return result

def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.2)
        return sock.connect_ex(('127.0.0.1',port))==0


def memories(service, project):
    value=service.good('/agentmemory/memories')
    rows=value.get('memories')
    if (not isinstance(rows,list) or len(rows)>500 or value.get('total')!=len(rows)
            or any(not isinstance(m,dict) or not isinstance(m.get('id'),str)
                   or m.get('project')!=project or not isinstance(m.get('content'),str)
                   or len(m['content'])>8000 for m in rows)):
        raise ValueError('Project memory schema or size is invalid')
    result={m['id']:m for m in rows}
    if len(result)!=len(rows):raise ValueError('Duplicate memory identity')
    return result


def perform(action,args,scratch):
    deadline=time.monotonic()+180
    shutdowns=[];service=None;started=time.monotonic()
    def stop():
        nonlocal service
        if service is None:return
        outcome=service.close();service=None;shutdowns.append(outcome)
        if not all(outcome[k] for k in ('worker_clean_exit','engine_clean_exit','ports_closed','owned_children_exited')):
            raise RuntimeError('AgentMemory did not finish a clean durable shutdown')
    try:
        service=Service(scratch,1,deadline,None);service.start()
        if listening(service.ports[2]):raise RuntimeError('The disabled upstream viewer opened a port')
        if service.request('/agentmemory/status',auth=False)[0]!=401:
            raise RuntimeError('The memory REST interface did not enforce its bearer')
        before=memories(service,args['project'])
        if action=='remember':
            if len(before)>=500:raise ValueError('This project reached its 500-note service limit')
            raw=service.good('/agentmemory/remember',{'content':args['content'],'project':args['project'],'type':'fact'})
            note=raw.get('memory',{});identity=note.get('id')
            after=memories(service,args['project'])
            if (raw.get('success') is not True or identity in before or not isinstance(identity,str)
                    or set(after)!=set(before)|{identity}
                    or any(after[k]!=v for k,v in before.items())
                    or after[identity].get('content')!=args['content'] or after[identity].get('type')!='fact'):
                raise ValueError('The worker changed more than the approved new note')
            result={'saved':identity,'type':'fact'}
        elif action=='forget':
            selected=set(args['memory_ids']);deleted=0
            for identity in args['memory_ids']:
                if identity not in before:continue
                raw=service.good('/agentmemory/forget',{'memoryId':identity,'project':args['project'],'reason':args['reason']})
                if raw.get('success') is not True or raw.get('deleted')!=1:
                    raise ValueError('The worker did not confirm exact deletion')
                deleted+=1
            after=memories(service,args['project'])
            if after!={k:v for k,v in before.items() if k not in selected}:
                raise ValueError('The worker changed more than the selected notes')
            result={'deleted':deleted,'requested':len(selected),'reason':args['reason']}
        elif action=='recall':
            raw=service.good('/agentmemory/search',{'query':args['query'],'project':args['project'],'limit':args['limit'],'format':'full'})
            rows=raw.get('results')
            if not isinstance(rows,list) or len(rows)>args['limit']:raise ValueError('Invalid recall result')
            results=[]
            for row in rows:
                identity=row.get('observation',{}).get('id')
                if identity not in before:raise ValueError('Recall escaped the selected project')
                note=before[identity]
                results.append({'id':identity,'content':note['content'],'type':note.get('type','fact'),
                                'project':args['project'],'score':row.get('score')})
            result={'results':results,'total':len(results),'retrieval':'local-semantic-and-keyword'}
            after=memories(service,args['project'])
            if after!=before:raise ValueError('Recall unexpectedly changed stored notes')
        else:raise ValueError('Unsupported full-memory operation')
        status=service.good('/agentmemory/status')
        if status.get('provider',{}).get('embeddings')!='local (384 dims)':
            raise RuntimeError('The local semantic provider was not active')
        stop()
        if action!='recall':
            service=Service(scratch,2,deadline,None);service.start()
            if memories(service,args['project'])!=after:
                raise RuntimeError('The exact selected note changes did not survive restart')
            stop()
        return {'result':result,'durable':True,'viewer_disabled':True,'seconds':round(time.monotonic()-started,3),
                'peak_job_committed_bytes':max(x['peak_job_committed_bytes'] for x in shutdowns),
                'all_owned_processes_exited':True,'all_owned_ports_closed':True}
    finally:
        if service is not None:service.close()


def main():
    global BUNDLE,NODE
    raw=sys.stdin.buffer.readline(20001)
    if len(raw)>20000:raise ValueError('Oversized provider request')
    message=json.loads(raw)
    if set(message)!={'bundle','node','scratch','action','arguments'}:raise ValueError('Invalid provider request')
    BUNDLE=Path(message['bundle']);NODE=Path(message['node']);scratch=Path(message['scratch'])
    result=perform(message['action'],message['arguments'],scratch)
    sys.stdout.write('PROMETHEUS_MEMORY:'+json.dumps(result,ensure_ascii=True,allow_nan=False)+'\n')
    sys.stdout.flush()


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Preserve neither raw note content nor an upstream traceback in the UI.
        sys.stdout.write('PROMETHEUS_MEMORY:'+json.dumps({'error':'The local memory worker could not confirm the operation; the active project was preserved.',
            'diagnostic_type':type(exc).__name__,'diagnostic_errno':getattr(exc,'errno',None),'diagnostic_winerror':getattr(exc,'winerror',None)})+'\n')
        raise SystemExit(1)
