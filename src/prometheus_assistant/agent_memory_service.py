"""Approved, on-demand full AgentMemory with local semantic embeddings.

The trusted caller owns owner/task approval. This provider owns pinned runtime
selection, project transactions and process cleanup. Raw iii sockets are temporary
loopback listeners without independent bearer authentication; same-user operating
system isolation is not claimed. No automatic capture or remote LLM is enabled.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .application_policy import require_dispatch
from .agent_memory import AgentMemoryError, canonical_arguments, _checked_path, _lock

VERSION='0.9.30'
PROVIDER='agentmemory_service'
BUNDLE=Path('Integrations/AgentMemoryService/0.9.30')
MANIFEST_SHA256='cf6fdc3c99f6b76b87b40dbe6aacb3386d5145e74d6066f195c1e223d3f67d93'
NODE=Path('Node/node-v24.21.0-win-x64/node.exe')
NODE_SHA256='ba4e6d110e8c1592a1ecd390f6b05f3da124b13871a5be62b341a07a853c6c32'
OPERATIONS=('remember','recall','forget')
MAX_STORE_BYTES=64*1024*1024
MAX_FILES=4096
MAX_SECONDS=240
# Enabled only after the source adapter, disabled viewer and transaction lifecycle
# pass the real portable-runtime qualification, independently of upstream smoke.
QUALIFIED=True
_SLOT=threading.Lock()
_GENERATION=re.compile(r'[a-f0-9]{32}\Z')
_LIMITATIONS=[
    'Local CPU semantic and keyword recall; no cloud LLM, automatic capture or account hooks.',
    'Only the approved project is opened; up to 500 notes and 64 MiB of persisted state per project.',
    'Services start for one operation and stop; durable writes include a verification restart.',
    'The upstream viewer is disabled. Raw engine sockets are temporary loopback without independent bearer authentication.',
    'Other programs running as the same OS user are not isolated from these portable files or raw local sockets.',
]


def _cancel(event):
    if event is not None and event.is_set():
        raise AgentMemoryError('The full-memory operation was cancelled; no staged change was published.')


def _hash(path,event=None):
    h=hashlib.sha256()
    with _checked_path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):
            _cancel(event);h.update(block)
    return h.hexdigest()


def _manifest(root):
    if not root:raise AgentMemoryError('The portable full-memory resource folder is not configured.')
    bundle=_checked_path(Path(root)/BUNDLE)
    path=_checked_path(bundle/'manifest.json')
    if path.stat().st_size>4*1024*1024:raise AgentMemoryError('The full-memory manifest exceeded its size bound.')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA256:raise AgentMemoryError('The full-memory manifest differs from its reviewed pin.')
    manifest=json.loads(raw)
    return bundle,manifest


def _runtime(root,event):
    bundle,manifest=_manifest(root)
    expected=manifest['files'];actual=set();total=0
    for folder,dirs,files in os.walk(bundle,followlinks=False):
        for name in dirs:
            info=(Path(folder)/name).lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise AgentMemoryError('Full-memory bundle links and reparse points are refused.')
        for name in files:
            _cancel(event);path=Path(folder)/name;relative=path.relative_to(bundle).as_posix();info=path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise AgentMemoryError('Full-memory bundle links and reparse points are refused.')
            if relative=='manifest.json':continue
            actual.add(relative);row=expected.get(relative);total+=info.st_size
            if len(actual)>30000 or total>1024*1024*1024 or row is None or row['bytes']!=info.st_size:
                raise AgentMemoryError('The full-memory bundle contains changed or unreviewed files.')
            # Ancestors were checked at the root and during this no-links walk.
            # Hash every file; do not replace integrity with an mtime cache.
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                while block:=stream.read(1024*1024):_cancel(event);digest.update(block)
            if digest.hexdigest()!=row['sha256']:
                raise AgentMemoryError('The full-memory bundle contains changed or unreviewed files.')
    if actual!=set(expected):raise AgentMemoryError('The full-memory bundle is incomplete.')
    node=_checked_path(Path(root)/NODE)
    if node.stat().st_size>128*1024*1024 or _hash(node,event)!=NODE_SHA256:
        raise AgentMemoryError('The full-memory Node runtime changed.')
    return bundle,node


def status(resource_root=None):
    row={'provider':PROVIDER,'name':'AgentMemory full local service','version':VERSION,
         'installed':False,'available':False,'integrity':'missing','operations':list(OPERATIONS),
         'mode':'full-local-semantic','network_used':False,'limitations':list(_LIMITATIONS)}
    try:
        bundle,_=_manifest(resource_root);row['installed']=True
        row['integrity']='manifest-verified; payload checked before each execution'
        node=_checked_path(Path(resource_root)/NODE)
        node_ok=node.is_file() and node.stat().st_size<=128*1024*1024 and _hash(node)==NODE_SHA256
        row['available']=QUALIFIED and os.name=='nt' and (bundle/'engine/iii.exe').is_file() and node_ok
        if not node_ok:row['integrity']='shared-node-missing-or-unverified'
        if not QUALIFIED:row['limitations'].append('The source provider qualification is pending.')
    except (OSError,ValueError,KeyError,TypeError):row['integrity']='missing-or-unverified'
    return row


def _tree(path,event=None):
    path=_checked_path(path);files={};total=0
    if not path.exists():return files
    for folder,dirs,names in os.walk(path,followlinks=False):
        for name in dirs:_checked_path(Path(folder)/name)
        for name in names:
            _cancel(event);item=_checked_path(Path(folder)/name);relative=item.relative_to(path).as_posix()
            total+=item.stat().st_size
            if total>MAX_STORE_BYTES or len(files)>=MAX_FILES:
                raise AgentMemoryError('The project reached its bounded full-memory store limit.')
            files[relative]={'bytes':item.stat().st_size,'sha256':_hash(item,event)}
    return files


def _current(project,event=None):
    pointer=_checked_path(project/'current.json')
    if not pointer.exists():return b'',None,{}
    if pointer.stat().st_size>4096:raise AgentMemoryError('The project generation pointer is invalid.')
    raw=pointer.read_bytes()
    try:
        value=json.loads(raw);identity=value['generation']
        if set(value)!={'generation'} or not isinstance(identity,str) or not _GENERATION.fullmatch(identity):raise ValueError()
    except (ValueError,KeyError,TypeError):raise AgentMemoryError('The project generation pointer is invalid; it was preserved.') from None
    generation=_checked_path(project/'generations'/identity)
    if not generation.is_dir():raise AgentMemoryError('The active memory generation is missing; it was preserved for recovery.')
    return raw,generation,_tree(generation,event)


def _publish(project,scratch,original,event):
    _cancel(event);require_dispatch('integrations.agentmemory_service')
    raw,old,old_files=_current(project,event)
    if raw!=original:raise AgentMemoryError('The active memory generation changed; the staged operation was not published.')
    staged=_checked_path(scratch/'state');staged_files=_tree(staged,event)
    identity=uuid.uuid4().hex;generations=_checked_path(project/'generations');generations.mkdir(exist_ok=True)
    target=_checked_path(generations/identity)
    pending=_checked_path(project/('pointer-'+identity+'.tmp'))
    committed=False
    try:
        os.replace(staged,target)
        body=(json.dumps({'generation':identity},sort_keys=True)+'\n').encode()
        with pending.open('xb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
        _cancel(event);require_dispatch('integrations.agentmemory_service')
        if _current(project,event)[0]!=original:raise AgentMemoryError('The project changed before publication.')
        os.replace(pending,_checked_path(project/'current.json'))
        committed=True
    finally:
        try:pending.unlink(missing_ok=True)
        except OSError:pass
        if not committed and target.exists():
            try:
                checked=_checked_path(target)
                if (checked.parent!=_checked_path(generations) or checked.name!=identity
                        or _current(project)[1]==checked or _tree(checked)!=staged_files):
                    raise AgentMemoryError('The staged generation cannot be safely removed.')
                shutil.rmtree(checked)
            except (OSError,ValueError):
                raise AgentMemoryError('Publication did not complete; its staged generation was retained for recovery.') from None
    # The pointer change is the commit. Delete only its exact previous snapshot,
    # and only if it is still byte-for-byte the same bounded generation. This
    # prevents successful forget operations accumulating old active-note copies.
    retained=old is not None
    if old is not None:
        try:
            checked=_checked_path(old)
            if (checked.parent!=_checked_path(generations) or not _GENERATION.fullmatch(checked.name)
                    or checked==target or _tree(checked)!=old_files):
                raise AgentMemoryError('Previous generation changed; preserve it for recovery.')
            shutil.rmtree(checked)
            retained=False
        except (OSError,ValueError):
            # Publication already succeeded; report retained recovery data without
            # misreporting the committed operation as an unexecuted failure.
            retained=True
    return {'generation':identity,'previous_generation_retained':retained}


def _invoke(bundle,node,scratch,action,args,event):
    from .autonomous_promotion import _owned_test_process
    asset=Path(__file__).with_name('vendor')/'agent_memory_service_host.py'
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}}
    env.update({k:str(scratch) for k in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP')})
    message={'bundle':str(bundle),'node':str(node),'scratch':str(scratch),'action':action,'arguments':args}
    payload=(json.dumps(message,ensure_ascii=True)+'\n').encode()
    if len(payload)>20000:raise AgentMemoryError('The full-memory invocation exceeded its bound.')
    deadline=time.monotonic()+MAX_SECONDS
    require_dispatch('integrations.agentmemory_service');_cancel(event)
    with (scratch/'host-output.log').open('w+b') as output:
        process=subprocess.Popen([sys.executable,'-I','-S','-B',str(asset)],cwd=scratch,env=env,
            stdin=subprocess.PIPE,stdout=output,stderr=output,creationflags=subprocess.CREATE_NO_WINDOW)
        with _owned_test_process(process):
            process.stdin.write(payload);process.stdin.close()
            while process.poll() is None:
                _cancel(event)
                if time.monotonic()>deadline:raise AgentMemoryError('The full-memory operation exceeded its time limit.')
                if os.fstat(output.fileno()).st_size>2*1024*1024:
                    raise AgentMemoryError('The full-memory worker output exceeded its bound.')
                time.sleep(.05)
            output.seek(0);body=output.read(2*1024*1024+1)
        _cancel(event)
    lines=[line[len(b'PROMETHEUS_MEMORY:'):] for line in body.splitlines() if line.startswith(b'PROMETHEUS_MEMORY:')]
    if process.returncode!=0 or len(lines)!=1 or len(body)>2*1024*1024:
        raise AgentMemoryError('The full-memory worker did not confirm the operation; the active project was preserved.')
    value=json.loads(lines[0])
    if (value.get('durable') is not True or value.get('viewer_disabled') is not True
            or value.get('all_owned_processes_exited') is not True or value.get('all_owned_ports_closed') is not True
            or not isinstance(value.get('result'),dict)):
        raise AgentMemoryError('The full-memory worker did not confirm durable cleanup.')
    return value


def execute(action,arguments,*,data_root,resource_root,cancel_event=None):
    args=canonical_arguments(action,arguments)
    require_dispatch('integrations.agentmemory_service');_cancel(cancel_event)
    if not QUALIFIED:raise AgentMemoryError('The full-memory source provider has not completed qualification.')
    if os.name!='nt':raise AgentMemoryError('The qualified full-memory runtime currently requires Windows x64.')
    if not _SLOT.acquire(blocking=False):raise AgentMemoryError('Another full-memory operation is running; retry after it finishes.')
    try:
        bundle,node=_runtime(resource_root,cancel_event)
        project=_checked_path(Path(data_root)/'Integrations/AgentMemoryService'/('project-'+args['project']))
        if bundle==project or bundle in project.parents:raise AgentMemoryError('Memory data must be separate from its runtime.')
        project.mkdir(parents=True,exist_ok=True)
        with _lock(project):
            original,current,before=_current(project,cancel_event)
            with tempfile.TemporaryDirectory(prefix='transaction-',dir=project) as folder:
                scratch=_checked_path(folder);state=scratch/'state'
                if current is None:state.mkdir()
                else:shutil.copytree(current,state,symlinks=False)
                if _tree(state,cancel_event)!=before:raise AgentMemoryError('The project snapshot changed while staging.')
                result=_invoke(bundle,node,scratch,action,args,cancel_event)
                _cancel(cancel_event)
                if _current(project,cancel_event)[0]!=original or (current is not None and _tree(current,cancel_event)!=before):
                    raise AgentMemoryError('The active project changed during the operation; no staged change was published.')
                if action=='recall':
                    require_dispatch('integrations.agentmemory_service');_cancel(cancel_event)
                    publication={}
                else:publication=_publish(project,scratch,original,cancel_event)
                return {'provider':PROVIDER,'version':VERSION,'action':action,'project':args['project'],
                    'result':result['result'],'network_used':False,'generated_answer':False,
                    'retrieval':'local-semantic-and-keyword','duration_seconds':result.get('seconds'),
                    'previous_generation_retained':publication.get('previous_generation_retained',False)}
    except AgentMemoryError:raise
    except (OSError,ValueError,KeyError,TypeError):
        raise AgentMemoryError('The full-memory runtime or project store is unavailable; the operation was not retried.') from None
    finally:_SLOT.release()
