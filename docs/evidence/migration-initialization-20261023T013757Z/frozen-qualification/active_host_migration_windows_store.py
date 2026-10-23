"""Production-domain journal/backup store; no launcher or task operations.

The provider supplies physically checked IO, a same-thread lock owner, held signed
package bytes, and a fresh original-authority verifier. Those are prerequisites,
not assertions established by this module. No fixture store subclass/domain is used.
"""
import base64,ctypes,hashlib,hmac,json,os,re,secrets
from ctypes import wintypes as W
from contextlib import contextmanager
import active_host_migration_core as core
from active_host_migration_windows import PRODUCTION_DPAPI_DOMAIN

class StoreRefusal(ValueError):pass
class Blob(ctypes.Structure):
    _fields_=[('size',W.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def digest(value):return hashlib.sha256(value).hexdigest()

def protected(value,scope,encrypt=True):
    entropy=hashlib.sha256(core.encode(scope)).digest()
    buffers=[ctypes.create_string_buffer(x) for x in (value,entropy)]
    source=Blob(len(value),ctypes.cast(buffers[0],ctypes.POINTER(ctypes.c_ubyte)))
    ent=Blob(len(entropy),ctypes.cast(buffers[1],ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=getattr(crypt,'CryptProtectData' if encrypt else 'CryptUnprotectData')
    fn.argtypes=[ctypes.POINTER(Blob),W.LPVOID,ctypes.POINTER(Blob),W.LPVOID,W.LPVOID,W.DWORD,ctypes.POINTER(Blob)];fn.restype=W.BOOL
    kernel.LocalFree.argtypes=[W.LPVOID];kernel.LocalFree.restype=W.LPVOID
    if not fn(ctypes.byref(source),None,ctypes.byref(ent),None,None,1,ctypes.byref(out)):raise ctypes.WinError(ctypes.get_last_error())
    try:return ctypes.string_at(out.data,out.size)
    finally:kernel.LocalFree(out.data)

class ProductionMigrationStore:
    production_activation_available=False
    def __init__(self,plan,files,locks,old,new,other_pins,authority,*,initialize=False,fault=None,mode='forward'):
        core.validate(plan)
        self.plan=json.loads(core.encode(plan));self.files=files;self.root=files.root;self.locks=locks
        self.old=old;self.new=new;self.other_pins=dict(other_pins);self.authority=authority;self.fault=fault or (lambda point:None)
        if mode not in ('forward','rollback') or (mode=='rollback' and initialize):raise StoreRefusal('Explicit operation mode')
        self.mode=mode
        if set(self.other_pins)!={'Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1'} or any(not isinstance(pin,str) or not re.fullmatch('[a-f0-9]{64}',pin) for pin in self.other_pins.values()):raise StoreRefusal('Exact unchanged four-entry closure required')
        if digest(old)!=plan['old_file_sha256'] or digest(new)!=plan['new_file_sha256']:raise StoreRefusal('Pinned signed payload bytes required')
        context=json.loads(plan['profile']);binding=json.loads(plan['task_identity'])
        if os.path.normcase(str(self.root))!=os.path.normcase(context['root']) or files.sid!=context['sid']:raise StoreRefusal('Physical IO owner/root differs from plan')
        self.target=self.root/plan['payload'];self.auth=self.root/'authorized-host.json'
        self.original=self.root/'Enrollment'/binding['enrollment_id']/'journal.json'
        self.parent=self.root/'ActiveHostMigration';self.directory=self.parent/plan['transaction']
        self.keyfile=self.directory/'key.dpapi';self.journal=self.directory/'journal.json'
        self.scope=dict(purpose=PRODUCTION_DPAPI_DOMAIN,root=str(self.root),sid=files.sid,host=plan['host'],transaction=plan['transaction'],auth=plan['auth_sha256'],enrollment=plan['enrollment_sha256'],plan_sha256=digest(core.encode(plan)))
        self._depth=0
        with self.operation():
            if initialize and self.directory.exists():raise StoreRefusal('Initialization cannot adopt an existing transaction')
            self.unchanged()
            if not self.directory.exists():
                if not initialize or self.files.read(self.target)!=old:raise StoreRefusal('Explicit clean R5 initialization required')
                if not self.parent.exists():self.files.mkdir(self.parent)
                self.files.mkdir(self.directory)
                key=secrets.token_bytes(32)
                self.files.create(self.keyfile,protected(core.encode(dict(scope=self.scope,key=base64.b64encode(key).decode())),self.scope))
            # Existing transaction with absent/corrupt key is always preserved/refused.
            self.files.check_directory(self.directory)
            obj=json.loads(protected(self.files.read(self.keyfile,65536),self.scope,False))
            if type(obj) is not dict or set(obj)!={'scope','key'} or obj['scope']!=self.scope:raise StoreRefusal('Protected key scope')
            self.key=base64.b64decode(obj['key'],validate=True)
            if len(self.key)!=32:raise StoreRefusal('Protected key length')
            if mode=='forward':self.read_journal()
            else:self.rollback_state()

    @contextmanager
    def operation(self):
        self.locks._check()
        if self._depth:
            self._depth+=1
            try:yield
            finally:self._depth-=1
        else:
            with self.locks.outer_lock():
                self._depth=1
                try:yield
                finally:self._depth=0

    def unchanged(self):
        self.files.check_directory(self.root)
        if self.authority(self.plan) is not True:raise StoreRefusal('Fresh original authority verification required')
        for path,pin in [(self.auth,self.plan['auth_sha256']),(self.original,self.plan['enrollment_sha256'])]:
            if digest(self.files.read(path))!=pin:raise StoreRefusal('Original authority/receipt changed')
        for name,pin in self.other_pins.items():
            if name==self.plan['payload'] or '/' in name or '\\' in name or ':' in name:raise StoreRefusal('Unchanged payload name')
            if digest(self.files.read(self.root/name))!=pin:raise StoreRefusal('Unchanged runtime changed')

    def phase(self,raw):
        try:
            obj=json.loads(json.loads(raw)['body']);phase=obj['phase']
            if phase not in core.PHASES or raw!=core.seal(self.plan,phase,self.key):raise StoreRefusal('Journal plan/schema/HMAC')
            return phase
        except Exception as error:raise StoreRefusal('Authenticated journal refused') from error

    def read_journal(self):
        with self.operation():
            if self.mode!='forward' or (self.directory/'rollback-intent.json').exists():raise StoreRefusal('Explicit rollback path required')
            self.unchanged();raw=self.files.read(self.journal,65536) if self.journal.exists() else None
            phase=self.phase(raw) if raw is not None else None;actual=self.files.read(self.target)
            if phase in (None,'prepared','disable','stop','backup') and actual!=self.old:raise StoreRefusal('Old payload required')
            if phase=='replace':
                if actual not in (self.old,self.new):raise StoreRefusal('Foreign replace target')
                if actual==self.new:
                    self.intent('replacement',create=False);self.verify_backup()
                if (self.directory/'replacement-receipt.json').exists():self.verify_replacement()
            if phase in ('restore','start','complete'):
                if actual!=self.new:raise StoreRefusal('New payload required')
                self.verify_replacement()
            return raw

    def write_journal(self,raw):
        with self.operation():
            if self.mode!='forward':raise StoreRefusal('Rollback cannot advance forward journal')
            phase=self.phase(raw);prior=self.read_journal();before=self.phase(prior) if prior is not None else None
            if (before is None and phase!='prepared') or (before is not None and core.PHASES.index(phase) not in (core.PHASES.index(before),core.PHASES.index(before)+1)):raise StoreRefusal('Journal skip/rewind')
            if phase in ('restore','start','complete'):self.verify_replacement()
            pending=self.directory/'journal.pending';self.fault('journal-before-write')
            if pending.exists():
                if self.files.read(pending)!=raw:raise StoreRefusal('Foreign pending journal')
            else:self.files.create(pending,raw)
            self.fault('journal-after-flush')
            if self.read_journal()!=prior:raise StoreRefusal('Journal changed')
            if prior is None:
                self.files.publish_new(pending,self.journal,raw)
            else:self.files.replace(pending,self.journal,prior)
            self.fault('journal-after-replace')
            if self.read_journal()!=raw:raise StoreRefusal('Journal readback')

    def seal_record(self,kind,value):
        body=core.encode(dict(scope=self.scope,kind=kind,value=value))
        return core.encode(dict(body=body.decode(),tag=hmac.new(self.key,b'production-migration-record\0'+body,hashlib.sha256).hexdigest()))

    def record(self,name,kind):
        raw=self.files.read(self.directory/name,65536)
        try:value=json.loads(json.loads(raw)['body'])['value']
        except Exception as error:raise StoreRefusal('Record schema') from error
        if raw!=self.seal_record(kind,value):raise StoreRefusal('Record binding/HMAC')
        return value

    def intent(self,kind,create=True):
        if kind not in ('backup','replacement'):raise StoreRefusal('Intent kind')
        path=self.directory/(kind+'-intent.json')
        if not path.exists():
            if not create or list(self.directory.glob(kind+'-*.bin')):raise StoreRefusal('Missing intent/foreign staging')
            self.files.create(path,self.seal_record(kind+'-intent',dict(nonce=secrets.token_hex(16),old=self.plan['old_file_sha256'],new=self.plan['new_file_sha256'])))
        value=self.record(path.name,kind+'-intent')
        if type(value) is not dict or set(value)!={'nonce','old','new'} or not isinstance(value['nonce'],str) or not re.fullmatch('[a-f0-9]{32}',value['nonce']) or value['old']!=self.plan['old_file_sha256'] or value['new']!=self.plan['new_file_sha256']:raise StoreRefusal('Intent scope')
        return value

    def owned(self,kind,intent):
        path=self.directory/(kind+'-'+intent['nonce']+'.bin')
        if any(p!=path for p in self.directory.glob(kind+'-*.bin')):raise StoreRefusal('Foreign staged file')
        return path

    def verify_backup(self):
        value=self.intent('backup',False);path=self.owned('backup',value)
        if self.record('backup-receipt.json','backup-receipt')!={'nonce':value['nonce'],'sha256':self.plan['old_file_sha256']} or self.files.read(path)!=self.old:raise StoreRefusal('Backup receipt/bytes')

    def verify_replacement(self):
        self.verify_backup();value=self.intent('replacement',False)
        if list(self.directory.glob('replacement-*.bin')) or self.files.read(self.target)!=self.new or self.record('replacement-receipt.json','replacement-receipt')!={'nonce':value['nonce'],'sha256':self.plan['new_file_sha256']}:raise StoreRefusal('Replacement evidence')

    def backup_verified(self,plan):
        with self.operation():
            if plan!=self.plan or self.phase(self.read_journal()) not in ('backup','replace'):raise StoreRefusal('Backup phase/plan')
            value=self.intent('backup');path=self.owned('backup',value)
            if (self.directory/'backup-receipt.json').exists():self.verify_backup();return
            if self.files.read(self.target)!=self.old:raise StoreRefusal('Old backup source')
            if path.exists():
                if self.files.read(path)!=self.old:raise StoreRefusal('Partial backup preserved')
            else:self.files.create(path,self.old)
            self.fault('backup-after-flush')
            if self.files.read(path)!=self.old:raise StoreRefusal('Backup readback')
            self.files.create(self.directory/'backup-receipt.json',self.seal_record('backup-receipt',dict(nonce=value['nonce'],sha256=self.plan['old_file_sha256'])))

    def replace_owned(self,plan):
        with self.operation():
            if plan!=self.plan or self.phase(self.read_journal())!='replace':raise StoreRefusal('Replace phase/plan')
            self.backup_verified(plan);current=self.files.read(self.target)
            value=self.intent('replacement',create=current==self.old);path=self.owned('replacement',value)
            if (self.directory/'replacement-receipt.json').exists():self.verify_replacement();return
            if current==self.old:
                if path.exists():
                    if self.files.read(path)!=self.new:raise StoreRefusal('Partial staging preserved')
                else:self.files.create(path,self.new)
                self.fault('replacement-after-flush');self.unchanged()
                self.files.replace(path,self.target,self.old);self.fault('replacement-after-replace')
            elif current!=self.new or path.exists():raise StoreRefusal('Ambiguous replacement preserved')
            if self.files.read(self.target)!=self.new:raise StoreRefusal('Replacement readback')
            self.files.create(self.directory/'replacement-receipt.json',self.seal_record('replacement-receipt',dict(nonce=value['nonce'],sha256=self.plan['new_file_sha256'])))

    def rollback_state(self):
        """Validate rollback evidence separately; never assert forward completion."""
        with self.operation():
            self.unchanged();raw=self.files.read(self.journal,65536)
            phase=self.phase(raw)
            if phase not in ('replace','restore','start','complete'):raise StoreRefusal('Rollback requires forward replace intent')
            self.verify_backup();self.intent('replacement',False)
            if list(self.directory.glob('replacement-*.bin')):raise StoreRefusal('Ambiguous replacement staging before rollback')
            path=self.directory/'rollback-intent.json'
            current=self.files.read(self.target)
            if not path.exists():
                if current!=self.new or list(self.directory.glob('rollback-*.bin')):raise StoreRefusal('Rollback requires transaction-proven new payload')
                if phase!='replace' or (self.directory/'replacement-receipt.json').exists():self.verify_replacement()
                return None
            value=self.record(path.name,'rollback-intent')
            if type(value) is not dict or set(value)!={'nonce','forward_journal','old','new'} or not isinstance(value['nonce'],str) or not re.fullmatch('[a-f0-9]{32}',value['nonce']) or value['forward_journal']!=digest(raw) or value['old']!=self.plan['old_file_sha256'] or value['new']!=self.plan['new_file_sha256']:raise StoreRefusal('Rollback intent binding')
            if current not in (self.old,self.new):raise StoreRefusal('Foreign rollback target')
            receipt=self.directory/'rollback-file-receipt.json'
            if receipt.exists():
                if current!=self.old or list(self.directory.glob('rollback-*.bin')) or self.record(receipt.name,'rollback-file-receipt')!={'nonce':value['nonce'],'sha256':self.plan['old_file_sha256'],'forward_journal':digest(raw)}:raise StoreRefusal('Rollback file completion binding')
            return value

    def rollback_owned(self,plan):
        """Restore only owned old bytes; caller separately owns task rollback."""
        with self.operation():
            if self.mode!='rollback' or plan!=self.plan or getattr(self.locks,'_runtime',False) is not True:raise StoreRefusal('Rollback requires explicit mode and held runtime locks')
            value=self.rollback_state();raw=self.files.read(self.journal,65536)
            if value is None:
                value=dict(nonce=secrets.token_hex(16),forward_journal=digest(raw),old=plan['old_file_sha256'],new=plan['new_file_sha256'])
                self.files.create(self.directory/'rollback-intent.json',self.seal_record('rollback-intent',value))
            staged=self.directory/('rollback-'+value['nonce']+'.bin')
            if any(p!=staged for p in self.directory.glob('rollback-*.bin')):raise StoreRefusal('Foreign rollback staging')
            receipt=self.directory/'rollback-file-receipt.json'
            if receipt.exists():self.rollback_state();return
            current=self.files.read(self.target)
            if current==self.new:
                if staged.exists():
                    if self.files.read(staged)!=self.old:raise StoreRefusal('Partial rollback staging preserved')
                else:self.files.create(staged,self.old)
                self.fault('rollback-after-flush');self.unchanged()
                if self.files.read(self.journal,65536)!=raw:raise StoreRefusal('Forward journal changed during rollback')
                self.files.replace(staged,self.target,self.new);self.fault('rollback-after-replace')
            elif current!=self.old or staged.exists():raise StoreRefusal('Ambiguous rollback target/staging')
            if self.files.read(self.target)!=self.old:raise StoreRefusal('Rollback readback')
            self.files.create(receipt,self.seal_record('rollback-file-receipt',dict(nonce=value['nonce'],sha256=plan['old_file_sha256'],forward_journal=digest(raw))))
            self.rollback_state()
