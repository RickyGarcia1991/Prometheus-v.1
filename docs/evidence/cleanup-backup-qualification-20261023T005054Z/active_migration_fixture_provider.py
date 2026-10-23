"""Generated Scheduler/driver/supervisor composition only; no production entry."""
from contextlib import contextmanager
import ctypes,hashlib,importlib.util,json,os,re,secrets,subprocess,time
from pathlib import Path
import active_host_migration_core as core
import active_migration_windows_store as store

B=Path(__file__).resolve().parent
HELPER=B/'check-signed-quiet-host-containment.owned-cleanup-frozen.py'
if store.digest(HELPER.read_bytes())!='d6cca2a4b4cb7b12e0f100ca0c36abd0ab0160fed3216e3273e0cc860dc8bbde':raise ValueError('Reviewed process primitive changed')
spec=importlib.util.spec_from_file_location('qualified_containment',HELPER);q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
BRIDGE=B/'active-migration-fixture-task.ps1'
SESSION=q.api(q.K,'ProcessIdToSessionId',[q.W.DWORD,ctypes.POINTER(q.W.DWORD)],q.W.BOOL)

def current_owner():
 h=q.Open(0x1000,False,os.getpid());q.require(h)
 try:return q.owner_sid(h)
 finally:q.Close(h)
def native(root,mode):
 store.root_check(root)
 p=subprocess.run([str(q.PS7),'-NoProfile','-NonInteractive','-File',str(BRIDGE),'-Root',str(root),'-Mode',mode],capture_output=True,text=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
 if p.returncode:raise ValueError(p.stderr.strip())
 return json.loads(p.stdout)
def save(path,value):store.exclusive(path,core.encode(value))
def load(path):return json.loads(store.bounded(path))
def session(pid):
 value=q.W.DWORD();q.require(SESSION(pid,ctypes.byref(value)));return value.value
def identity_matches(actual,row):
 return actual['created']==row['created'] and actual['owner']==row['owner'] and os.path.normcase(actual['image'])==os.path.normcase(row['image'])
def argv(command):
 shell=ctypes.WinDLL('shell32',use_last_error=True)
 parse=q.api(shell,'CommandLineToArgvW',[q.W.LPCWSTR,ctypes.POINTER(ctypes.c_int)],ctypes.POINTER(q.W.LPWSTR));count=ctypes.c_int()
 result=parse(command,ctypes.byref(count));q.require(result)
 try:return [result[i] for i in range(count.value)]
 finally:q.Free(ctypes.cast(result,q.W.HLOCAL))
def capture(row,root,token,owner,wrapper=False):
 h=q.Open(0x00100000|0x1000|1,False,row['pid']);q.require(h)
 try:
  actual=q.identity(h)
  if not identity_matches(actual,row) or actual['owner']!=owner or session(row['pid'])!=session(os.getpid()):raise ValueError('Native process identity/session mismatch')
  info=q.ps_json("Get-CimInstance Win32_Process -Filter 'ProcessId="+str(row['pid'])+"' | Select-Object CommandLine,ParentProcessId | ConvertTo-Json -Compress")
  if wrapper:
   if info['CommandLine']!=row['command'] or os.path.normcase(actual['image'])!=os.path.normcase(str(q.PS7)):raise ValueError('Driver command/image mismatch')
  else:
   if row['role'] not in q.ROLES:raise ValueError('Unknown generated process role')
   script=root/('Prometheus/Prometheus-USB-Reconnect-Supervisor.ps1' if row['role']=='immediate-supervisor' else 'independent.ps1')
   expected=([str(q.PS),'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)] if row['role']=='immediate-supervisor' else [str(q.PS),'-NoProfile','-NonInteractive','-File',str(script),'-Role',row['role'],'-Token',token])
   actual_args=argv(info['CommandLine']);actual_args[0]=os.path.normcase(actual_args[0]);expected[0]=os.path.normcase(expected[0])
   if actual['image'].lower()!=str(q.PS).lower() or actual_args!=expected or row['token']!=token or info['ParentProcessId']!=row['parent']:raise ValueError('Generated process exact command/parent mismatch')
  if q.Wait(h,0)!=258:raise ValueError('Process exited during capture')
  return h
 except BaseException:q.Close(h);raise

DRIVER=r'''param([string]$Root,[string]$Token)
$ErrorActionPreference='Stop'
$request=Get-Content -LiteralPath (Join-Path $Root 'launch-request.json') -Raw|ConvertFrom-Json
if($request.token -cne $Token -or $request.generation -cnotmatch '^[a-f0-9]{32}$'){throw 'Launch request'}
$record=@{generation=$request.generation;token=$Token;pid=$PID}
$bytes=[Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Compress))
$stream=[IO.File]::Open((Join-Path $Root ('driver-'+$PID+'.json')),'CreateNew','Write','None')
try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
$a=[Reflection.Assembly]::LoadFile('SIGNED_EXE')
$m=$a.GetType('PrometheusQuietHostV3').GetMethod('Run',[Reflection.BindingFlags]'Static,NonPublic')
exit [int]$m.Invoke($null,[object[]]@([string[]]@('reconnect'),$Root))
'''
SUPERVISOR=r'''$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$token='TOKEN'
$parentId=[int](Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)).ParentProcessId
$launch=Get-Content -LiteralPath (Join-Path $root ('driver-'+$parentId+'.json')) -Raw|ConvertFrom-Json
if($launch.token -cne $token){throw 'Driver generation mismatch'}
function Record($role,$pidValue,$parentValue){
 $expected=Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
 $p=Get-Process -Id $pidValue
 $created=$p.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
 $provisional=@{role=$role;pid=$pidValue;parent=$parentValue;created=$created;image=$expected;owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;token=$token;generation=$launch.generation;provisional=$true}
 $launchBytes=[Text.Encoding]::UTF8.GetBytes(($provisional|ConvertTo-Json -Compress))
 $launchStream=[IO.File]::Open((Join-Path $root ('launch-process-'+$role+'-'+$pidValue+'.json')),'CreateNew','Write','None')
 try{$launchStream.Write($launchBytes,0,$launchBytes.Length);$launchStream.Flush($true)}finally{$launchStream.Dispose()}
 $deadline=[DateTime]::UtcNow.AddSeconds(8)
 do {
  $p=Get-Process -Id $pidValue
  if($p.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() -cne $created){throw 'Started process creation changed before record'}
  $image=$null
  try{$image=$p.MainModule.FileName}catch{}
  if($image -ieq $expected){break}
  Start-Sleep -Milliseconds 50
 } while([DateTime]::UtcNow -lt $deadline)
 if($image -ine $expected){throw 'Expected process image not ready; no identity record published'}
 $row=@{role=$role;pid=$pidValue;parent=$parentValue;created=$created;image=$image;owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;token=$token;generation=$launch.generation}
 $bytes=[Text.Encoding]::UTF8.GetBytes(($row|ConvertTo-Json -Compress))
 $stream=[IO.File]::Open((Join-Path $root ('record-'+$role+'-'+$pidValue+'.json')),'CreateNew','Write','None')
 try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
Record 'immediate-supervisor' $PID $parentId
foreach($role in @('application','controller','communication')){
 $args=@('-NoProfile','-NonInteractive','-File',('"'+(Join-Path $root 'independent.ps1')+'"'),'-Role',$role,'-Token',$token)
 $p=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $args -WindowStyle Hidden -PassThru
 Record $role $p.Id $PID
}
Set-Content -LiteralPath (Join-Path $root ('ready-'+$PID)) -Value 'ready'
Start-Sleep -Seconds 600
'''

def setup_fixture(enabled=True,running=True):
 if running and not enabled:raise ValueError('Disabled-running unsupported')
 root,plan=store.setup_fixture()
 try:return _setup_fixture(root,plan,enabled,running)
 except BaseException:
  if (root/'task-ownership.json').exists():cleanup(root)
  raise
def _setup_fixture(root,plan,enabled,running):
 token=root.name.rsplit('-',1)[1];owner=current_owner()
 (root/'Prometheus').mkdir()
 store.exclusive(root/'signed-quiet-host.exe',store.bounded(q.EXE))
 if store.bounded(root/'signed-quiet-host.exe')!=store.bounded(root/'runtime/Prometheus-QuietHost-v3.exe'):raise ValueError('Driver assembly differs from verified runtime copy')
 store.exclusive(root/'driver.ps1',DRIVER.replace('SIGNED_EXE',str(root/'signed-quiet-host.exe').replace("'","''")).encode())
 store.exclusive(root/'Prometheus/Prometheus-USB-Reconnect-Supervisor.ps1',SUPERVISOR.replace('TOKEN',token).encode())
 store.exclusive(root/'independent.ps1',b'param([string]$Role,[string]$Token)\nStart-Sleep -Seconds 600\n')
 save(root/'task-spec.json',dict(token=token,enrollment=load(root/'observations.json')['enrollment_id'],owner=owner))
 save(root/'launch-request.json',dict(token=token,generation=secrets.token_hex(16)))
 observed=native(root,'Create');store.exclusive(root/'task-baseline.xml',observed['xml'].encode())
 # Setup only: bind actual normalized generated task XML before store key creation.
 (root/'task.xml').write_bytes(observed['xml'].encode())
 assets={name:store.digest(store.bounded(root/name)) for name in ('signed-quiet-host.exe','driver.ps1','Prometheus/Prometheus-USB-Reconnect-Supervisor.ps1','independent.ps1','task-spec.json','task-baseline.xml')}
 identity=dict(name=observed['name'],arguments=observed['arguments'],image=observed['image'],owner=owner,assets=assets,bridge=store.digest(BRIDGE.read_bytes()))
 plan.update(task_config_sha256=store.digest((root/'task.xml').read_bytes()),task_identity=core.encode(identity).decode(),original_enabled=enabled,original_running=running,wrapper_identity='absent',child_identity='absent')
 observation=load(root/'observations.json');observation['task_identity']=plan['task_identity'];(root/'observations.json').write_bytes(core.encode(observation))
 if not enabled:native(root,'Disable')
 if running:native(root,'Start')
 pair=wait_pair(root) if running else None
 if pair:
  plan['wrapper_identity']=core.encode(pair[0]).decode();plan['child_identity']=core.encode(pair[1]).decode()
 (root/'plan.json').write_bytes(core.encode(plan))
 return root,plan

def wait_pair(root):
 deadline=time.monotonic()+18
 while time.monotonic()<deadline:
  task=native(root,'Observe');rows=task['processes']
  if len(rows)>1:raise ValueError('Duplicate generated task driver')
  if rows:
   wrapper=rows[0]
   matches=[load(p) for p in root.glob('record-immediate-supervisor-*.json') if load(p)['parent']==wrapper['pid']]
   if len(matches)>1:raise ValueError('Ambiguous supervisor')
   if matches and (root/('ready-'+str(matches[0]['pid']))).exists():return wrapper,matches[0]
  time.sleep(.15)
 raise TimeoutError('Generated task pair did not converge')

class FixtureProvider:
 production_activation_available=False
 def __init__(self,root,*,fault=None):
  self.root=store.root_check(root);self.store=store.WindowsMigrationStore(root);self.plan=self.store.plan;self.key=self.store.key
  self.fault=fault or (lambda point:None);self.spec=load(self.root/'task-spec.json');self.identity=json.loads(self.plan['task_identity']);self.handles={};self.events=[];self.unresolved=[]
  self.verify(self.plan)
 def task(self,mode='Observe'):
  if self.unresolved:raise ValueError('Native handle close uncertainty; provider cannot be reused')
  for name,pin in self.identity['assets'].items():
   if store.digest(store.bounded(store.safe(self.root/name,self.root)))!=pin:raise ValueError('Generated asset changed')
  if store.digest(BRIDGE.read_bytes())!=self.identity['bridge']:raise ValueError('Task bridge changed')
  if self.identity['assets']['signed-quiet-host.exe']!=self.store.other_pins['Prometheus-QuietHost-v3.exe']:raise ValueError('Driver assembly is not signed closure entry')
  result=native(self.root,mode)
  if mode!='Remove' and (result['xml'].encode()!=store.bounded(self.root/'task.xml') or any(result[k]!=self.identity[k] for k in ('name','arguments','image','owner'))):raise ValueError('Actual task configuration changed')
  return result
 def held(self,row,wrapper=False):
  key=(row['pid'],row['created'])
  if key not in self.handles:self.handles[key]=(capture(row,self.root,self.spec['token'],self.spec['owner'],wrapper),row)
  h=self.handles[key][0]
  if not identity_matches(q.identity(h),row):raise ValueError('Retained handle identity changed')
  return h
 def phase(self):
  raw=self.store.read_journal();return self.store._validate_journal(raw) if raw is not None else None
 def verify(self,plan):
  if plan!=self.plan:raise ValueError('Plan mismatch')
  phase=self.phase();task=self.task()
  if phase in ('stop','backup','replace') and task['enabled']:raise ValueError('Task must remain disabled')
  if phase in (None,'prepared','start','complete') and task['enabled']!=plan['original_enabled']:raise ValueError('Task enabled state mismatch')
  return True
 def verify_original_enrollment(self,plan):
  self.store._unchanged()
  if not self.store.signed_evidence['native_verified']:raise ValueError('Signed trust missing')
 @contextmanager
 def outer_lock(self):
  with self.store._operation():yield
 def read_journal(self):return self.store.read_journal()
 def write_journal(self,raw):
  self.store.write_journal(raw);self.fault('journal-'+self.store._validate_journal(raw))
 def assert_original(self,plan):
  self.verify(plan);task=self.task()
  if plan['original_running']:
   pair=wait_pair(self.root)
   if core.encode(pair[0]).decode()!=plan['wrapper_identity'] or core.encode(pair[1]).decode()!=plan['child_identity']:raise ValueError('Original pair changed')
   for i,row in enumerate(pair):self.held(row,i==0)
  elif task['processes'] or task['state']=='Running':raise ValueError('Stopped baseline changed')
 def disable_owned(self,plan):
  self.verify(plan);self.task('Disable');self.fault('after-disable')
 def stop_captured_pair(self,plan):
  self.verify(plan);task=self.task()
  if task['processes']:
   pair=wait_pair(self.root)
   if core.encode(pair[0]).decode()!=plan['wrapper_identity'] or core.encode(pair[1]).decode()!=plan['child_identity']:raise ValueError('Only original captured pair may stop')
   wrapper=self.held(pair[0],True);child=self.held(pair[1])
   q.require(q.Terminate(wrapper,0))
   if q.Wait(wrapper,10000)!=0 or q.Wait(child,10000)!=0:raise ValueError('Owned driver/contained supervisor failed to stop')
  self.original_child_gone(10000)
  deadline=time.monotonic()+15
  while self.task()['state']=='Running':
   if time.monotonic()>deadline:raise TimeoutError('Scheduler stop convergence')
   time.sleep(.1)
  self.fault('after-stop')
 def original_child_gone(self,timeout):
  if not self.plan['original_running']:return
  row=json.loads(self.plan['child_identity']);probe=q.Open(0x1000|0x100000,False,row['pid'])
  if not probe:
   if ctypes.get_last_error()==87:return
   q.require(False)
  try:
   if q.Wait(probe,0)==0:return
   if not identity_matches(q.identity(probe),row):raise ValueError('Original supervisor identity ambiguous')
   if q.Wait(probe,timeout)!=0:raise ValueError('Original supervisor teardown incomplete')
  finally:q.Close(probe)
 @contextmanager
 def quiescent(self,plan):
  self.verify(plan)
  if self.task()['processes']:raise ValueError('Owned task still active')
  self.original_child_gone(0)
  with self.store.locks.quiescent():yield
 def backup_verified(self,plan):self.store.backup_verified(plan);self.fault('after-backup')
 def replace_owned(self,plan):self.store.replace_owned(plan);self.fault('after-replace')
 def restore_enabled(self,plan):
  self.store.locks.assert_restart_allowed();self.verify(plan)
  self.task('Enable' if plan['original_enabled'] else 'Disable');self.events.append('runtime-released-before-restore');self.fault('after-restore')
 def start_once(self,plan):
  self.store.locks.assert_restart_allowed();self.verify(plan)
  intent=self.store.directory/'task-start-intent.json'
  receipt=self.store.directory/'task-start-receipt.json';task=self.task();fresh=not intent.exists()
  if receipt.exists() and not task['processes']:raise ValueError('Completed start pair absent; no retry permitted')
  if fresh:
   if task['processes'] or task['state']=='Running':raise ValueError('Unowned task pair before start intent')
   store.exclusive(intent,self.store._seal('task-start-intent',dict(generation=secrets.token_hex(16),task=plan['task_identity'])))
  start=self.start_intent()
  if not task['processes'] and task['state']!='Running':
   if not fresh:raise ValueError('Pending start with absent pair is ambiguous; no retry')
   request=store.safe(self.root/'launch-request.json',self.root);pending=self.root/'launch-request.pending'
   encoded=core.encode(dict(token=self.spec['token'],generation=start['generation']));store.exclusive(pending,encoded);os.replace(pending,request)
   if store.bounded(request)!=encoded:raise ValueError('Launch request readback')
   self.fault('before-start-call')
   self.store.locks.assert_restart_allowed();self.task('Start');self.events.append('runtime-released-before-start');self.fault('after-start-call')
  pair=wait_pair(self.root)
  value=self.start_value(pair,start)
  for i,row in enumerate(pair):self.held(row,i==0)
  if receipt.exists():
   if self.store._record(receipt,'task-start-receipt')!=value:raise ValueError('Started pair changed')
  else:store.exclusive(receipt,self.store._seal('task-start-receipt',value))
  self.fault('after-start-receipt')
 def start_intent(self):
  start=self.store._record(self.store.directory/'task-start-intent.json','task-start-intent')
  if not isinstance(start,dict) or set(start)!={'generation','task'} or start['task']!=self.plan['task_identity'] or not isinstance(start['generation'],str) or not re.fullmatch('[a-f0-9]{32}',start['generation']):raise ValueError('Start intent invalid')
  return start
 def start_value(self,pair,start):
  launch=load(self.root/('driver-'+str(pair[0]['pid'])+'.json'))
  if launch!={'generation':start['generation'],'token':self.spec['token'],'pid':pair[0]['pid']} or pair[1]['generation']!=start['generation']:raise ValueError('Start does not own this generation')
  return dict(generation=start['generation'],wrapper=pair[0],child=pair[1])
 def assert_complete(self,plan):
  self.verify(plan);self.store._verified_replacement();task=self.task()
  if plan['original_running']:
   pair=wait_pair(self.root);record=self.store._record(self.store.directory/'task-start-receipt.json','task-start-receipt')
   if record!=self.start_value(pair,self.start_intent()):raise ValueError('Complete pair/generation/schema mismatch')
   for i,row in enumerate(pair):self.held(row,i==0)
  elif task['processes'] or task['state']=='Running' or (self.store.directory/'task-start-intent.json').exists() or (self.store.directory/'task-start-receipt.json').exists():raise ValueError('Stopped completion required without start evidence')
  self.assert_independents()
 def assert_independents(self):
  for path in self.root.glob('record-*.json'):
   row=load(path)
   if row['role']!='immediate-supervisor':
    if q.Wait(self.held(row),0)!=258:raise ValueError('Independent descendant collateral')
 def close(self):
  errors=[]
  for key,(h,row) in list(self.handles.items()):
   try:
    if not q.Close(h):raise RuntimeError('Native CloseHandle failed')
   except BaseException as exc:
    record=dict(handle=h,identity=row,error=repr(exc));self.unresolved.append(record);errors.append(record)
   finally:del self.handles[key]
  if errors:raise RuntimeError('Independent handle close uncertainty: '+repr(errors))
 def run(self):return core.migrate(self,self.plan,self.key)

def cleanup(root):
 """Independent best-effort cleanup of identity-verified generated roles only."""
 errors=[];handles=[];removed=False;spec=load(root/'task-spec.json')
 try:
  task=native(root,'CleanupDisable')
  for row in task['processes']:
   try:handles.append((capture(row,root,spec['token'],spec['owner'],True),row))
   except BaseException as exc:errors.append(repr(exc))
 except BaseException as exc:errors.append(repr(exc))
 seen=set()
 for path in [*root.glob('record-*.json'),*root.glob('launch-process-*.json')]:
  try:
   row=load(path)
   if path.name.startswith('launch-process-'):
    if not isinstance(row,dict) or set(row)!={'role','pid','parent','created','image','owner','token','generation','provisional'} or row['provisional'] is not True or row['role'] not in q.ROLES or os.path.normcase(row['image'])!=os.path.normcase(str(q.PS)) or not re.fullmatch('[a-f0-9]{32}',row['generation']):raise ValueError('Provisional launch schema/scope')
   key=(row['pid'],row['created'])
   if key in seen:continue
   probe=q.Open(0x1000|0x100000,False,row['pid'])
   if not probe:
    if ctypes.get_last_error()==87:continue
    q.require(False)
   try:
    if q.Wait(probe,0)==0:continue
    actual=q.identity(probe)
    if actual['created']!=row['created']:continue # Original PID no longer names that process.
    if not identity_matches(actual,row):raise ValueError('Owned cleanup identity ambiguity')
   finally:
    if not q.Close(probe):errors.append('Native cleanup probe CloseHandle failed for observed PID '+str(row['pid']))
   handles.append((capture(row,root,spec['token'],spec['owner']),row));seen.add(key)
  except BaseException as exc:errors.append(str(path.name)+': '+repr(exc))
 for h,row in handles:
  try:
   if q.Wait(h,0)==258 and not q.Terminate(h,0) and q.Wait(h,10000)!=0:q.require(False)
   if q.Wait(h,10000)!=0:raise TimeoutError('Generated cleanup timeout')
  except BaseException as exc:errors.append(repr(exc))
  finally:
   if not q.Close(h):errors.append('Native cleanup CloseHandle failed for verified PID '+str(row['pid']))
 try:removed=native(root,'Remove')['removed']
 except BaseException as exc:errors.append(repr(exc))
 result=dict(task_removed=removed,errors=errors,passed=removed and not errors,scope='generated task and native-verified processes only')
 save(root/('cleanup-'+secrets.token_hex(8)+'.json'),result);return result
