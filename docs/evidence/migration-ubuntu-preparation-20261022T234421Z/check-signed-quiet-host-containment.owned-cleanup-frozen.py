"""Signed primitive qualification; only generated, identity-bound fixture processes."""
import ctypes
from ctypes import wintypes as W
import datetime, hashlib, json, os, subprocess, tempfile, time, uuid
from pathlib import Path
B=Path(__file__).resolve().parent
PS=Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
PS7=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
PKG=B/'reconnect-enrollment-candidate-r5'; EXE=PKG/'Prometheus-QuietHost-v3.exe'
PIN='b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e'
K=ctypes.WinDLL('kernel32',use_last_error=True); A=ctypes.WinDLL('advapi32',use_last_error=True)
def api(lib,name,args,result):
 f=getattr(lib,name);f.argtypes=args;f.restype=result;return f
Open=api(K,'OpenProcess',[W.DWORD,W.BOOL,W.DWORD],W.HANDLE)
Wait=api(K,'WaitForSingleObject',[W.HANDLE,W.DWORD],W.DWORD)
Terminate=api(K,'TerminateProcess',[W.HANDLE,W.UINT],W.BOOL)
Close=api(K,'CloseHandle',[W.HANDLE],W.BOOL)
Times=api(K,'GetProcessTimes',[W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4,W.BOOL)
Image=api(K,'QueryFullProcessImageNameW',[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)],W.BOOL)
OpenToken=api(A,'OpenProcessToken',[W.HANDLE,W.DWORD,ctypes.POINTER(W.HANDLE)],W.BOOL)
TokenInfo=api(A,'GetTokenInformation',[W.HANDLE,ctypes.c_int,W.LPVOID,W.DWORD,ctypes.POINTER(W.DWORD)],W.BOOL)
SidString=api(A,'ConvertSidToStringSidW',[W.LPVOID,ctypes.POINTER(W.LPWSTR)],W.BOOL)
Free=api(K,'LocalFree',[W.HLOCAL],W.HLOCAL)
def require(ok):
 if not ok:raise ctypes.WinError(ctypes.get_last_error())
def owner_sid(h):
 t=W.HANDLE();require(OpenToken(h,8,ctypes.byref(t)))
 try:
  n=W.DWORD();TokenInfo(t,1,None,0,ctypes.byref(n));b=ctypes.create_string_buffer(n.value)
  require(TokenInfo(t,1,b,n,ctypes.byref(n)));sid=ctypes.cast(b,ctypes.POINTER(ctypes.c_void_p))[0]
  s=W.LPWSTR();require(SidString(sid,ctypes.byref(s)))
  try:return s.value
  finally:Free(ctypes.cast(s,W.HLOCAL))
 finally:Close(t)
def identity(h):
 ts=[W.FILETIME() for _ in range(4)];require(Times(h,*(ctypes.byref(t) for t in ts)))
 n=W.DWORD(32768);b=ctypes.create_unicode_buffer(n.value);require(Image(h,0,b,ctypes.byref(n)))
 return dict(created=str((ts[0].dwHighDateTime<<32)|ts[0].dwLowDateTime),image=b.value,owner=owner_sid(h))
def ps_json(code):
 p=subprocess.run([str(PS7),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=12,creationflags=subprocess.CREATE_NO_WINDOW)
 if p.returncode:raise RuntimeError(p.stderr[:1000])
 return json.loads(p.stdout)
ROLES=('immediate-supervisor','application','controller','communication')
def acquire(row,token,owner,root):
 pid=row['pid']
 if type(pid) is not int or pid<=0 or row['token']!=token or row['owner']!=owner or row['role'] not in ROLES:raise ValueError('Record scope mismatch')
 h=Open(0x00100000|0x1000|1,False,pid);require(h)
 try:
  actual=identity(h)
  if actual['created']!=row['created'] or actual['owner']!=row['owner'] or os.path.normcase(actual['image'])!=os.path.normcase(row['image']):raise ValueError('Native creation/image/owner mismatch: '+repr((actual,row)))
  if os.path.normcase(actual['image'])!=os.path.normcase(str(PS)):raise ValueError('Unexpected image')
  cmd=ps_json(f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine | ConvertTo-Json -Compress")
  script=root/('Prometheus/Prometheus-USB-Reconnect-Supervisor.ps1' if row['role']==ROLES[0] else 'independent.ps1')
  if not isinstance(cmd,str) or str(script).lower() not in cmd.lower() or token not in cmd:raise ValueError('Unique command path/token mismatch')
  if row['role']!=ROLES[0] and row['role'] not in cmd:raise ValueError('Command role mismatch')
  if Wait(h,0)!=258 or identity(h)!=actual:raise ValueError('Identity changed/exited')
  return h
 except BaseException:Close(h);raise
class CaptureFailure(Exception):pass
def scenario(mode,owner):
 token=uuid.uuid4().hex;root=Path(tempfile.mkdtemp(prefix='prometheus-signed-'+token+'-'))
 supervisor=root/'Prometheus/Prometheus-USB-Reconnect-Supervisor.ps1';supervisor.parent.mkdir()
 driver=root/'driver.ps1'
 driver.write_text('''param([string]$Assembly,[string]$Root)
$ErrorActionPreference='Stop'
$a=[Reflection.Assembly]::LoadFile($Assembly)
$m=$a.GetType('PrometheusQuietHostV3').GetMethod('Run',[Reflection.BindingFlags]'Static,NonPublic')
$code=$m.Invoke($null,[object[]]@([string[]]@('reconnect'),$Root))
exit [int]$code
''')
 (root/'independent.ps1').write_text('param([string]$Role,[string]$Token)\nStart-Sleep -Seconds 45\n')
 supervisor.write_text('''$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$token='TOKEN'
function Record($role,$id){
 $p=Get-Process -Id $id
 $row=[pscustomobject]@{role=$role;pid=$id;created=$p.StartTime.ToUniversalTime().ToFileTimeUtc().ToString();image=$p.MainModule.FileName;owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;token=$token}
 $target=Join-Path $root ($role+'.json')
 $row | ConvertTo-Json | Set-Content -LiteralPath ($target+'.tmp')
 Move-Item -LiteralPath ($target+'.tmp') -Destination $target
}
Record 'immediate-supervisor' $PID
foreach($role in @('application','controller','communication')){
 $arguments=@('-NoProfile','-NonInteractive','-File',('"'+(Join-Path $root 'independent.ps1')+'"'),'-Role',$role,'-Token',$token)
 $p=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -WindowStyle Hidden -ArgumentList $arguments -PassThru
 Record $role $p.Id
}
READY
Start-Sleep -Seconds 45
'''.replace('TOKEN',token).replace('READY',"Set-Content -LiteralPath (Join-Path $root 'ready') -Value 'ready'" if mode!='readiness-timeout' else '# readiness deliberately withheld'))
 args=[str(PS7),'-NoProfile','-NonInteractive','-File',str(driver),str(EXE),str(root)]
 handles={};rows={};cleanup=[];checks=[];parent=None;error=None
 def capture(role):
  f=root/(role+'.json')
  if role not in handles and f.exists():
   row=json.loads(f.read_text(encoding='utf-8-sig'));h=acquire(row,token,owner,root)
   handles[role]=h;rows[role]=row
   return True
  return False
 try:
  parent=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
  deadline=time.monotonic()+(6 if mode=='readiness-timeout' else 20)
  while not (root/'ready').exists():
   for role in ROLES:
    if capture(role) and mode=='capture-failure':raise CaptureFailure('Injected capture failure after first retained handle')
   if parent.poll() is not None:raise AssertionError('Fixture wrapper exited early')
   if time.monotonic()>deadline:raise TimeoutError('Fixture readiness timeout')
   time.sleep(.05)
  for role in ROLES:
   if capture(role) and mode=='capture-failure':raise CaptureFailure('Injected capture failure after first retained handle')
  assert len(handles)==4 and all(Wait(h,0)==258 for h in handles.values())
  for field,value in [('created','0'),('image',str(root/'wrong.exe')),('owner','S-1-0-0'),('token','wrong')]:
   bad=dict(rows['application']);bad[field]=value
   try:
    unexpected=acquire(bad,token,owner,root)
   except ValueError:pass
   else:
    Close(unexpected);raise AssertionError('Tampered '+field+' accepted')
   assert Wait(handles['application'],0)==258
  checks.append('creation image owner token mismatches refused without terminating fixture')
  parent.kill();parent.wait(timeout=10)
  assert Wait(handles[ROLES[0]],10000)==0
  assert all(Wait(handles[r],0)==258 for r in ROLES[1:])
  checks+=['signed owner termination kills immediate supervisor','application/controller/communication stand-ins survive']
 except (CaptureFailure,TimeoutError) as exc:
  if (mode=='capture-failure' and isinstance(exc,CaptureFailure)) or (mode=='readiness-timeout' and isinstance(exc,TimeoutError)):checks.append(str(exc))
  else:error=repr(exc)
 except BaseException as exc:error=repr(exc)
 finally:
  # Each capture and cleanup is independent. Retained handles pin process identity.
  for role in ROLES:
   try:capture(role)
   except BaseException as exc:cleanup.append(dict(role=role,capture_error=repr(exc),self_exit_seconds=45))
  try:
   if parent and parent.poll() is None:parent.kill();parent.wait(timeout=10)
  except BaseException as exc:cleanup.append(dict(role='owned-driver',error=repr(exc)))
  # Catch any record published during supervisor shutdown; no PID-only killing.
  for role in ROLES[1:]:
   try:capture(role)
   except BaseException as exc:cleanup.append(dict(role=role,late_capture_error=repr(exc),self_exit_seconds=45))
  for role,h in handles.items():
   try:
    if Wait(h,10000 if role==ROLES[0] else 0)==258:
     if not Terminate(h,0) and Wait(h,10000)!=0:require(False)
    if Wait(h,10000)!=0:raise TimeoutError('Verified fixture failed to exit')
    cleanup.append(dict(role=role,exited=True))
   except BaseException as exc:cleanup.append(dict(role=role,error=repr(exc)))
   finally:Close(h)
 result=dict(mode=mode,fixture_root=str(root),checks=checks,error=error,verified_records=list(rows.values()),cleanup=cleanup,passed=error is None and len(rows)==4 and len(cleanup)==4 and all(x.get('exited') for x in cleanup))
 (root/'qualification.json').write_text(json.dumps(result,indent=2));return result
def run():
 verified=json.loads(subprocess.check_output([str(PS7),'-NoProfile','-NonInteractive','-File',str(B/'qualify-reconnect-enrollment-v2.ps1'),'-Operation','Verify','-PackageKind','ReconnectEnrollment','-Candidate',str(PKG),'-ExpectedManifestSha256',PIN],text=True,timeout=30))
 assert verified['signature_verified'] and verified['files']==7
 initial=hashlib.sha256(EXE.read_bytes()).hexdigest();h=Open(0x1000,False,os.getpid());require(h)
 try:owner=owner_sid(h)
 finally:Close(h)
 results=[scenario(mode,owner) for mode in ('normal','capture-failure','readiness-timeout')]
 assert hashlib.sha256(EXE.read_bytes()).hexdigest()==initial
 return dict(passed=all(x['passed'] for x in results),scenarios=results,bundle_signature_verified=True,bundle_manifest=PIN,quiet_host_sha256=initial,installed_processes_touched=False,scope='Signed internal Run primitive; generated fixtures only, not production migration acceptance')
if __name__=='__main__':
 result=run();result['utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();result['helper_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 target=B/('signed-quiet-host-owned-cleanup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
 target.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
