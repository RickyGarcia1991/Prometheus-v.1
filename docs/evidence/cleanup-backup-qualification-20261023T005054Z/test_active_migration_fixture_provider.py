import json,os,subprocess,sys
from pathlib import Path
import pytest
import active_migration_fixture_provider as p
from active_migration_windows_locks import LockRefusal
pytestmark=pytest.mark.skipif(os.name!='nt',reason='native Windows fixture')
B=Path(__file__).resolve().parent
@pytest.fixture(autouse=True)
def retain_fixture_root_in_xml(request,monkeypatch):
 original=p.setup_fixture
 def setup(*args,**kwargs):
  root,plan=original(*args,**kwargs);request.node.user_properties.append(('generated_root',str(root)));return root,plan
 monkeypatch.setattr(p,'setup_fixture',setup)

CHILD='''import os,sys
from active_migration_fixture_provider import FixtureProvider
def fault(point):
 if point==sys.argv[2]:os._exit(73)
f=FixtureProvider(sys.argv[1],fault=fault)
try:print(f.run(),flush=True)
finally:f.close()
'''
def execute(root,fault='none'):
 result=subprocess.run([sys._base_executable,'-B','-c',CHILD,str(root),fault],cwd=B,capture_output=True,text=True,timeout=240,creationflags=subprocess.CREATE_NO_WINDOW)
 p.save(root/('run-'+p.secrets.token_hex(8)+'.json'),dict(fault=fault,exit=result.returncode,stdout=result.stdout,stderr=result.stderr))
 return result
def originals(root):
 return {str(x):p.store.digest(x.read_bytes()) for x in (root/'runtime/authorized-host.json',p.store.chain.receipt_path(root),root/'task.xml',root/'plan.json')}

@pytest.mark.parametrize('enabled,running',[(True,True),(True,False),(False,False)])
def test_native_composition_baselines(enabled,running):
 root,plan=p.setup_fixture(enabled,running);before=originals(root)
 try:
  result=execute(root);assert result.returncode==0,(result.stdout,result.stderr,str(root))
  f=p.FixtureProvider(root)
  try:
   assert f.phase()=='complete'
   with f.outer_lock():f.assert_complete(plan)
   with f.outer_lock():
    with f.store.locks.quiescent():
     with pytest.raises(LockRefusal):f.restore_enabled(plan)
     with pytest.raises(LockRefusal):f.start_once(plan)
   assert f.task()['enabled']==enabled and bool(f.task()['processes'])==running
   assert not (f.store.directory/'task-start-receipt.json').exists() if not running else (f.store.directory/'task-start-receipt.json').exists()
   assert originals(root)==before
   assert not f.production_activation_available
  finally:f.close()
 finally:assert p.cleanup(root)['passed']

@pytest.mark.parametrize('fault',['journal-disable','after-disable','after-stop','after-backup','after-replace','after-restore','after-start-call','after-start-receipt','journal-complete'])
def test_native_phase_interruption_resume(fault):
 root,plan=p.setup_fixture();before=originals(root)
 try:
  first=execute(root,fault);assert first.returncode==73,(first.stdout,first.stderr,str(root))
  second=execute(root);assert second.returncode==0,(second.stdout,second.stderr,str(root))
  f=p.FixtureProvider(root)
  try:
   with f.outer_lock():f.assert_complete(plan)
   assert f.phase()=='complete' and len(f.task()['processes'])==1
   assert len(list(root.glob('driver-*.json')))==2
   assert originals(root)==before
  finally:f.close()
 finally:assert p.cleanup(root)['passed']

def test_disabled_running_refused():
 with pytest.raises(ValueError):p.setup_fixture(False,True)

def test_close_failure_attempts_all_and_retains_uncertainty(monkeypatch):
 f=object.__new__(p.FixtureProvider);f.handles={(1,'a'):(11,{'pid':1}),(2,'b'):(22,{'pid':2})};f.unresolved=[];calls=[]
 def close(handle):calls.append(handle);return handle!=11
 with monkeypatch.context() as patch:
  patch.setattr(p.q,'Close',close)
  with pytest.raises(RuntimeError):f.close()
 assert calls==[11,22] and len(f.unresolved)==1 and not f.handles
 with pytest.raises(ValueError):f.task()

def test_api_injected_task_and_native_command_negatives(monkeypatch):
 root,plan=p.setup_fixture();f=None
 try:
  f=p.FixtureProvider(root);observed=f.task();real=p.native
  for field,value in [('xml',observed['xml']+' '),('owner','S-1-0-0'),('arguments',observed['arguments']+' --foreign')]:
   bad=dict(observed);bad[field]=value;calls=[]
   def injected(root,mode):
    calls.append(mode)
    assert mode=='Observe','Mutation reached after mismatched observation'
    return bad
   with monkeypatch.context() as patch:
    patch.setattr(p,'native',injected)
    with pytest.raises(ValueError):f.disable_owned(plan)
   assert calls==['Observe']
  row=p.load(next(root.glob('record-application-*.json')))
  for change in [dict(role='controller'),dict(token='0'*32),dict(parent=0)]:
   with pytest.raises(ValueError):p.capture(dict(row,**change),root,f.spec['token'],f.spec['owner'])
  info=p.q.ps_json("Get-CimInstance Win32_Process -Filter 'ProcessId="+str(row['pid'])+"' | Select-Object CommandLine,ParentProcessId | ConvertTo-Json -Compress")
  with monkeypatch.context() as patch:
   patch.setattr(p.q,'ps_json',lambda code:dict(info,CommandLine=info['CommandLine']+' --foreign'))
   with pytest.raises(ValueError):p.capture(row,root,f.spec['token'],f.spec['owner'])
  h=p.capture(row,root,f.spec['token'],f.spec['owner'])
  try:assert p.q.Wait(h,0)==258
  finally:p.q.Close(h)
 finally:
  if f:f.close()
  assert p.cleanup(root)['passed']

@pytest.mark.parametrize('damage',['incomplete','stale-generation'])
def test_pending_start_ambiguity_refuses_without_duplicate(damage):
 root,plan=p.setup_fixture();before=originals(root)
 try:
  first=execute(root,'before-start-call');assert first.returncode==73,(first.stdout,first.stderr)
  if damage=='stale-generation':
   request=p.load(root/'launch-request.json');request['generation']='0'*32
   (root/'launch-request.json').write_bytes(p.core.encode(request));p.native(root,'Start');p.wait_pair(root)
  drivers=len(list(root.glob('driver-*.json')))
  second=execute(root);assert second.returncode!=0
  assert len(list(root.glob('driver-*.json')))==drivers and originals(root)==before
 finally:assert p.cleanup(root)['passed']

def test_completed_start_missing_pair_and_malformed_receipt_refuse():
 root,plan=p.setup_fixture();f=None
 try:
  assert execute(root,'after-start-receipt').returncode==73
  f=p.FixtureProvider(root);receipt=f.store.directory/'task-start-receipt.json';original=receipt.read_bytes()
  value=f.store._record(receipt,'task-start-receipt')
  for wrong in [dict(value,generation='0'*32),dict(value,extra=True),{k:v for k,v in value.items() if k!='generation'}]:
   receipt.write_bytes(f.store._seal('task-start-receipt',wrong))
   with pytest.raises(ValueError):
    with f.outer_lock():f.assert_complete(plan)
  receipt.write_bytes(original)
  pair=p.wait_pair(root);wrapper=f.held(pair[0],True);child=f.held(pair[1]);p.q.require(p.q.Terminate(wrapper,0));assert p.q.Wait(child,10000)==0
  # Pending core start phase already persisted; completion receipt now exists but pair is gone.
  drivers=len(list(root.glob('driver-*.json')))
  result=execute(root);assert result.returncode!=0 and len(list(root.glob('driver-*.json')))==drivers
 finally:
  if f:f.close()
  assert p.cleanup(root)['passed']

def test_native_record_readiness_timeout_preserves_provisional_cleanup(monkeypatch,request):
 root,plan=p.store.setup_fixture();request.node.user_properties.append(('generated_root',str(root)));handle=None
 forced=p.SUPERVISOR.replace('if($image -ieq $expected){break}',"if($image -ieq $expected -and $role -eq 'immediate-supervisor'){break}")
 forced=forced.replace('if($image -ine $expected){throw',"if($image -ine $expected -or $role -ne 'immediate-supervisor'){throw")
 try:
  with monkeypatch.context() as patch:
   patch.setattr(p,'SUPERVISOR',forced)
   with pytest.raises(TimeoutError):p._setup_fixture(root,plan,True,True)
  assert not list(root.glob('record-application-*.json'))
  provisional=list(root.glob('launch-process-application-*.json'));assert len(provisional)==1
  row=p.load(provisional[0]);spec=p.load(root/'task-spec.json');handle=p.capture(row,root,spec['token'],spec['owner'])
  assert p.q.Wait(handle,0)==258
 finally:
  result=p.cleanup(root)
  try:
   assert result['passed']
   if handle:assert p.q.Wait(handle,0)==0
  finally:
   if handle:p.q.require(p.q.Close(handle))
