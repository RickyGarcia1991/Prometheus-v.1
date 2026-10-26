"""Generated-root Windows filesystem/DPAPI migration qualification only.

No task operations, production target names, CLI activation, or live provider.
Cooperative single-writer fixture contract; fsync+replace tests do not prove power-loss durability.
"""
from contextlib import contextmanager
import base64
import ctypes
from ctypes import wintypes as W
import hashlib,hmac,json,os,re,secrets,subprocess,tempfile
from pathlib import Path
import active_host_migration_core as core
import active_migration_native_chain as chain
from active_migration_windows_locks import WindowsMigrationLocks,Native,SessionContext

BASE=Path(__file__).resolve().parent
BRIDGE_SHA='df0ac6c102024e1e8a6f0dd13dc3ec3b84329cdcc6b8b46bc83eb06562cc0288'
PURPOSE='generated-active-migration-store-v1'
PAYLOAD='Prometheus-Host-Agent.ps1'
LIMIT=1024*1024
class StoreRefusal(ValueError):pass

def digest(data):return hashlib.sha256(data).hexdigest()
def safe(path,root=None):
 p=Path(path).absolute()
 if root is not None:
  try:p.relative_to(root)
  except ValueError:raise StoreRefusal('Path outside generated root')
 for item in [p,*p.parents]:
  if item.exists() or item.is_symlink():
   st=item.lstat()
   if item.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:raise StoreRefusal('Reparse path refused')
 return p

def bounded(path,limit=LIMIT):
 p=safe(path)
 if not p.is_file() or p.stat().st_size>limit:raise StoreRefusal('Missing/oversized file')
 with p.open('rb') as f:
  b=f.read(limit+1)
 if len(b)>limit:raise StoreRefusal('Read bound')
 return b

def exclusive(path,data,mid=None):
 p=safe(path)
 with p.open('xb') as f:
  if mid:
   half=len(data)//2;f.write(data[:half]);f.flush();os.fsync(f.fileno());mid();f.write(data[half:])
  else:f.write(data)
  f.flush();os.fsync(f.fileno())
 if bounded(p,max(LIMIT,len(data)))!=data:raise StoreRefusal('Create readback mismatch')

def root_check(root):
 p=safe(root)
 if p.parent!=Path(tempfile.gettempdir()).absolute() or not re.fullmatch(r'prometheus-active-chain-store-[a-f0-9]{32}',p.name):
  raise StoreRefusal('Only exact generated store root accepted')
 if not p.is_dir():raise StoreRefusal('Existing generated root required')
 return p

def protect_key(data,root,transaction,protect=True):
 # Distinct migration-store domain, additionally bound to canonical root/transaction.
 entropy=hashlib.sha256(core.encode({'purpose':PURPOSE,'root':str(root),'transaction':transaction})).digest()
 buffers=[ctypes.create_string_buffer(x) for x in (data,entropy)]
 incoming=chain.Blob(len(data),ctypes.cast(buffers[0],ctypes.POINTER(ctypes.c_ubyte)))
 ent=chain.Blob(len(entropy),ctypes.cast(buffers[1],ctypes.POINTER(ctypes.c_ubyte)));out=chain.Blob()
 api=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 fn=getattr(api,'CryptProtectData' if protect else 'CryptUnprotectData')
 fn.argtypes=[ctypes.POINTER(chain.Blob),ctypes.c_void_p,ctypes.POINTER(chain.Blob),ctypes.c_void_p,ctypes.c_void_p,W.DWORD,ctypes.POINTER(chain.Blob)];fn.restype=W.BOOL
 kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
 if not fn(ctypes.byref(incoming),None,ctypes.byref(ent),None,None,1,ctypes.byref(out)):raise ctypes.WinError(ctypes.get_last_error())
 try:return ctypes.string_at(out.data,out.size)
 finally:kernel.LocalFree(out.data)

def locked(fn):
 def call(self,*args,**kwargs):
  with self._operation():return fn(self,*args,**kwargs)
 return call

class WindowsMigrationStore:
 production_activation_available=False
 def __init__(self,root,*,fault=None):
  self.root=root_check(root);self.fault=fault or (lambda point:None)
  self.plan=json.loads(bounded(self.root/'plan.json'));core.validate(self.plan)
  if bounded(self.root/'plan.json')!=core.encode(self.plan):raise StoreRefusal('Noncanonical plan')
  if bounded(self.root/'STORE-FIXTURE.json')!=core.encode({'purpose':PURPOSE,'root':str(self.root),'transaction':self.plan['transaction']}):raise StoreRefusal('Fixture binding')
  if digest(bounded(chain.BRIDGE))!=BRIDGE_SHA:raise StoreRefusal('Signed-chain bridge changed')
  self.runtime=safe(self.root/'runtime',self.root);self.target=safe(self.runtime/PAYLOAD,self.root)
  self.auth=self.runtime/'authorized-host.json';self.original=chain.receipt_path(self.root)
  safe(self.original,self.root)
  self._unchanged()
  # Reuse the actual signed closure + original DPAPI receipt verifier. Its existing
  # API validates R7 completion only, so verification uses a separate bounded view
  # with signed R7 payloads and exact original authority/receipt bytes. It does NOT
  # claim that this fixture's target is R7 or that production authority was observed.
  self._verify_signed_view()
  self.old=bounded(chain.OLD/PAYLOAD);self.new=bounded(chain.NEW/PAYLOAD)
  if digest(self.old)!=self.plan['old_file_sha256'] or digest(self.new)!=self.plan['new_file_sha256']:raise StoreRefusal('Signed source changed after verification')
  self.directory=safe(self.root/'ActiveHostMigration'/self.plan['transaction'],self.root)
  session=Native().session_id();self.locks=WindowsMigrationLocks(self.plan['transaction'],SessionContext(session,session,(session,)))
  self._depth=0
  self.keyfile=self.directory/'key.dpapi';self.journal=self.directory/'journal.json'
  with self._operation():
   created=not self.directory.exists()
   if created and bounded(self.target)!=self.old:raise StoreRefusal('Initial target must be exact signed old bytes')
   self.directory.mkdir(parents=True,exist_ok=True)
   self._load_key(created)
   self.read_journal()
 @contextmanager
 def _operation(self):
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
 def _unchanged(self):
  root_check(self.root)
  if bounded(self.root/'STORE-FIXTURE.json')!=core.encode({'purpose':PURPOSE,'root':str(self.root),'transaction':self.plan['transaction']}):raise StoreRefusal('Fixture marker changed')
  for p,pin in [(self.auth,self.plan['auth_sha256']),(self.original,self.plan['enrollment_sha256']),(self.root/'task.xml',self.plan['task_config_sha256'])]:
   if digest(bounded(safe(p,self.root)))!=pin:raise StoreRefusal('Original authority/receipt changed')
  if bounded(self.root/'plan.json')!=core.encode(self.plan):raise StoreRefusal('Plan changed')
  if hasattr(self,'other_pins'):
   for name,pin in self.other_pins.items():
    if digest(bounded(safe(self.runtime/name,self.root)))!=pin:raise StoreRefusal('Unchanged runtime entry changed')
 def _verify_signed_view(self):
  view=Path(tempfile.mkdtemp(prefix='prometheus-active-chain-store-verification-'))
  runtime=view/'runtime';runtime.mkdir()
  for name in chain.PAYLOADS:exclusive(runtime/name,bounded(chain.NEW/name))
  for name in ('plan.json','observations.json','task.xml'):exclusive(view/name,bounded(self.root/name))
  exclusive(runtime/'authorized-host.json',bounded(self.auth))
  receipt=runtime/'Enrollment'/self.original.parent.name/'journal.json';receipt.parent.mkdir(parents=True)
  exclusive(receipt,bounded(self.original))
  evidence=chain.native(view)
  if not evidence['native_verified'] or evidence['receipt_sha256']!=self.plan['enrollment_sha256']:raise StoreRefusal('Original signed-chain verification refused')
  self.signed_evidence=evidence
  rows=json.loads(bounded(chain.NEW/'SHA256-MANIFEST.json'))
  self.other_pins={r['path']:r['sha256'] for r in rows if r['path'] in chain.PAYLOADS and r['path']!=PAYLOAD}
  self._unchanged()
 def _load_key(self,created):
  if not self.keyfile.exists():
   # Never regenerate a missing key around any prior transaction artifact.
   if not created or list(self.directory.iterdir()):raise StoreRefusal('Missing key with existing transaction state')
   key=secrets.token_bytes(32)
   envelope=core.encode({'purpose':PURPOSE,'root':str(self.root),'transaction':self.plan['transaction'],'key':base64.b64encode(key).decode()})
   exclusive(self.keyfile,protect_key(envelope,self.root,self.plan['transaction']))
  try:
   obj=json.loads(protect_key(bounded(self.keyfile),self.root,self.plan['transaction'],False))
   if set(obj)!={'purpose','root','transaction','key'} or obj['purpose']!=PURPOSE or obj['root']!=str(self.root) or obj['transaction']!=self.plan['transaction']:raise StoreRefusal('DPAPI key scope')
   self.key=base64.b64decode(obj['key'],validate=True)
   if len(self.key)!=32:raise StoreRefusal('Key length')
  except Exception as exc:raise StoreRefusal('Protected key invalid') from exc
 def _validate_journal(self,raw):
  try:
   record=json.loads(raw);body=record['body'].encode();obj=json.loads(body)
   if set(record)!={'body','tag'} or obj['plan']!=self.plan or obj['phase'] not in core.PHASES or raw!=core.seal(self.plan,obj['phase'],self.key):raise StoreRefusal('Journal identity/HMAC/schema')
   return obj['phase']
  except Exception as exc:raise StoreRefusal('Authenticated journal refused') from exc
 @locked
 def read_journal(self):
  self._unchanged()
  data=bounded(self.journal,65536) if self.journal.exists() else None
  phase=self._validate_journal(data) if data is not None else None
  actual=bounded(self.target)
  if phase in (None,'prepared','disable','stop','backup') and actual!=self.old:raise StoreRefusal('Old runtime required before replace intent')
  if phase=='replace':
   if actual not in (self.old,self.new):raise StoreRefusal('Foreign runtime during replace')
   if actual==self.new:
    self._existing_intent('replacement')
    self._verified_backup()
    if (self.directory/'replacement-receipt.json').exists():self._verified_replacement()
  if phase in ('restore','start','complete'):
   if actual!=self.new:raise StoreRefusal('New runtime required after replace')
   self._verified_replacement()
  return data
 @locked
 def write_journal(self,raw):
  self._unchanged();phase=self._validate_journal(raw)
  prior=self.read_journal();old=self._validate_journal(prior) if prior else None
  if (old is None and phase!='prepared') or (old is not None and core.PHASES.index(phase) not in (core.PHASES.index(old),core.PHASES.index(old)+1)):raise StoreRefusal('Journal phase cannot skip or rewind')
  if phase in ('restore','start','complete'):
   if bounded(self.target)!=self.new:raise StoreRefusal('Cannot journal post-replace phase before new target')
   self._verified_replacement()
  temp=self.directory/'journal.pending'
  self.fault('journal-before-write')
  if temp.exists():
   if bounded(temp,65536)!=raw:raise StoreRefusal('Foreign/ambiguous pending journal preserved')
  else:exclusive(temp,raw)
  self.fault('journal-after-flush')
  # Recheck exact authenticated previous bytes before cooperative atomic publication.
  if self.read_journal()!=prior:raise StoreRefusal('Journal changed before publication')
  os.replace(safe(temp,self.root),safe(self.journal,self.root))
  self.fault('journal-after-replace')
  if self.read_journal()!=raw:raise StoreRefusal('Journal readback')
  self.fault('journal-after-readback')
 def _phase(self,allowed):
  raw=self.read_journal()
  if raw is None or self._validate_journal(raw) not in allowed:raise StoreRefusal('Authenticated phase intent required')
 def _seal(self,kind,value):
  body=core.encode({'purpose':PURPOSE,'transaction':self.plan['transaction'],'root':str(self.root),'kind':kind,'value':value})
  return core.encode({'body':body.decode(),'tag':hmac.new(self.key,b'filesystem-record\0'+body,hashlib.sha256).hexdigest()})
 def _record(self,path,kind):
  raw=bounded(path,65536)
  try:
   record=json.loads(raw);body=json.loads(record['body']);value=body['value']
   if raw!=self._seal(kind,value):raise StoreRefusal('Record scope/HMAC')
   return value
  except Exception as exc:raise StoreRefusal('Authenticated file record refused') from exc
 def _intent(self,kind):
  p=self.directory/(kind+'-intent.json')
  if p.exists():
   return self._existing_intent(kind)
  # Unknown files are never adopted on their bytes alone.
  if list(self.directory.glob(kind+'-*.bin')):raise StoreRefusal('Foreign files without authenticated ownership intent')
  value=dict(nonce=secrets.token_hex(16),old=self.plan['old_file_sha256'],new=self.plan['new_file_sha256'])
  self.fault(kind+'-before-intent');exclusive(p,self._seal(kind+'-intent',value));self.fault(kind+'-after-intent');return value
 def _existing_intent(self,kind):
  value=self._record(self.directory/(kind+'-intent.json'),kind+'-intent')
  if not isinstance(value,dict) or set(value)!={'nonce','old','new'} or not isinstance(value['nonce'],str) or not re.fullmatch('[a-f0-9]{32}',value['nonce']) or value['old']!=self.plan['old_file_sha256'] or value['new']!=self.plan['new_file_sha256']:raise StoreRefusal('Intent schema')
  return value
 def _verified_backup(self):
  intent=self._existing_intent('backup');path=self._owned_path('backup',intent)
  if self._record(self.directory/'backup-receipt.json','backup-receipt')!={'nonce':intent['nonce'],'sha256':self.plan['old_file_sha256']} or bounded(path)!=self.old:raise StoreRefusal('Backup receipt/bytes binding')
 def _verified_replacement(self):
  self._verified_backup();intent=self._existing_intent('replacement')
  if list(self.directory.glob('replacement-*.bin')):raise StoreRefusal('Ambiguous leftover replacement staging preserved')
  if self._record(self.directory/'replacement-receipt.json','replacement-receipt')!={'nonce':intent['nonce'],'sha256':self.plan['new_file_sha256']}:raise StoreRefusal('Replacement receipt binding')
 def _owned_path(self,kind,intent):
  path=self.directory/(kind+'-'+intent['nonce']+'.bin')
  if any(p!=path for p in self.directory.glob(kind+'-*.bin')):raise StoreRefusal('Unexpected competing owned-file candidate')
  return safe(path,self.root)
 @locked
 def backup_verified(self,plan):
  if plan!=self.plan:raise StoreRefusal('Caller plan mismatch')
  self._phase(('backup','replace'))
  intent=self._intent('backup');path=self._owned_path('backup',intent)
  receipt=self.directory/'backup-receipt.json'
  if receipt.exists():
   if self._record(receipt,'backup-receipt')!={'nonce':intent['nonce'],'sha256':self.plan['old_file_sha256']}:raise StoreRefusal('Backup receipt binding')
   if bounded(path)!=self.old:raise StoreRefusal('Backup readback mismatch')
   return
  if bounded(self.target)!=self.old:raise StoreRefusal('Backup only from exact signed old target')
  self.fault('backup-before-write')
  if path.exists():
   if bounded(path)!=self.old:raise StoreRefusal('Partial/foreign backup preserved')
  else:exclusive(path,self.old,lambda:self.fault('backup-mid-write'))
  self.fault('backup-after-flush')
  if bounded(path)!=self.old:raise StoreRefusal('Backup verification')
  self.fault('backup-after-readback')
  exclusive(receipt,self._seal('backup-receipt',{'nonce':intent['nonce'],'sha256':self.plan['old_file_sha256']}))
  self.fault('backup-after-receipt')
 @locked
 def replace_owned(self,plan):
  if plan!=self.plan:raise StoreRefusal('Caller plan mismatch')
  self._phase(('replace',));self.backup_verified(plan)
  existing_intent=(self.directory/'replacement-intent.json').exists()
  current=bounded(self.target)
  if current==self.new and not existing_intent:raise StoreRefusal('New bytes alone do not establish transaction ownership')
  if current not in (self.old,self.new):raise StoreRefusal('Foreign target preserved')
  intent=self._intent('replacement');staged=self._owned_path('replacement',intent)
  receipt=self.directory/'replacement-receipt.json'
  if receipt.exists():
   self._verified_replacement()
   if self._record(receipt,'replacement-receipt')!={'nonce':intent['nonce'],'sha256':self.plan['new_file_sha256']} or current!=self.new:raise StoreRefusal('Replacement receipt/current mismatch')
   return
  if current==self.old:
   self.fault('replacement-before-write')
   if staged.exists():
    if bounded(staged)!=self.new:raise StoreRefusal('Partial/foreign staged bytes preserved')
   else:exclusive(staged,self.new,lambda:self.fault('replacement-mid-write'))
   self.fault('replacement-after-flush')
   before=self.target.stat();identity=(before.st_dev,before.st_ino)
   if bounded(self.target)!=self.old:raise StoreRefusal('Old target changed before replace')
   self._unchanged();self.fault('replacement-before-replace')
   again=self.target.stat()
   if (again.st_dev,again.st_ino)!=identity or bounded(self.target)!=self.old:raise StoreRefusal('Target identity/hash changed')
   os.replace(safe(staged,self.root),safe(self.target,self.root))
   self.fault('replacement-after-replace')
  elif staged.exists():raise StoreRefusal('Ambiguous new target plus staged file preserved')
  if bounded(self.target)!=self.new:raise StoreRefusal('Replacement readback')
  self.fault('replacement-after-readback')
  exclusive(receipt,self._seal('replacement-receipt',{'nonce':intent['nonce'],'sha256':self.plan['new_file_sha256']}))
  self.fault('replacement-after-receipt')
  self._unchanged()

def setup_fixture():
 root=Path(tempfile.gettempdir())/('prometheus-active-chain-store-'+secrets.token_hex(16));root.mkdir()
 plan=chain.setup(root) # Actual signed verifier and R5 DPAPI receipt, synthetic authority.
 exclusive(root/'STORE-FIXTURE.json',core.encode({'purpose':PURPOSE,'root':str(root),'transaction':plan['transaction']}))
 # Initial fixture state is R5. All other runtime entries are signed identical.
 (root/'runtime'/PAYLOAD).write_bytes(bounded(chain.OLD/PAYLOAD))
 return root,plan
