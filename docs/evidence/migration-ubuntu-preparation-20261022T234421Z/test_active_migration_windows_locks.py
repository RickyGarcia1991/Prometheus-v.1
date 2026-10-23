"""Native generated-mutex tests; never production names or tasks."""
import json, os, subprocess, sys, threading, uuid
from pathlib import Path
import pytest
from active_migration_windows_locks import Native,SessionContext,WindowsMigrationLocks,OUTER,RUNTIME,LockRefusal,LockBusy,RevalidationRequired
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native mutex tests')
B=Path(__file__).resolve().parent
CHILD=r'''
import sys,json,threading,os
from active_migration_windows_locks import Native,PREFIX
api=Native();name=PREFIX+sys.argv[1]+'-'+sys.argv[2]
h=api.open(name);status=api.wait(h,0)
print(json.dumps({'status':status}),flush=True)
if status not in (0,128):api.close(h);sys.exit(3)
event=threading.Event();command=[]
def read():
 command.append(sys.stdin.readline().strip());event.set()
threading.Thread(target=read,daemon=True).start()
event.wait(20)
if command and command[0]=='abandon':os._exit(0)
api.release(h);api.close(h)
'''
def context():
 s=Native().session_id();return SessionContext(s,s,(s,))
def owner(token=None,**kw):return WindowsMigrationLocks(token or uuid.uuid4().hex,context(),**kw)
def holder(token,role):
 p=subprocess.Popen([sys._base_executable,'-B','-c',CHILD,token,role],cwd=B,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
 # Readiness line is bounded by a thread; kill only our direct subprocess on timeout.
 lines=[];event=threading.Event()
 def read():lines.append(p.stdout.readline());event.set()
 threading.Thread(target=read,daemon=True).start()
 try:
  if not event.wait(10):raise AssertionError('holder readiness timeout')
  assert json.loads(lines[0])['status']==0
  return p
 except BaseException:
  if p.poll() is None:p.kill();p.wait(timeout=10)
  raise
def finish(p,command='release'):
 if p.poll() is None:
  try:p.communicate(command+'\n',timeout=10)
  except subprocess.TimeoutExpired:p.kill();p.wait();raise
 assert p.returncode==0

def test_order_release_before_restart_and_thread_affinity():
 locks=owner()
 with pytest.raises(LockRefusal):locks.assert_restart_allowed()
 with pytest.raises(LockRefusal):
  with locks.quiescent():pass
 with locks.outer_lock():
  assert locks.assert_restart_allowed()
  with pytest.raises(LockRefusal):
   with locks.outer_lock():pass
  with locks.quiescent():
   with pytest.raises(LockRefusal):locks.assert_restart_allowed()
   errors=[]
   def cross_thread():
    try:locks.assert_restart_allowed()
    except LockRefusal:errors.append(True)
   t=threading.Thread(target=cross_thread);t.start();t.join();assert errors==[True]
  assert locks.assert_restart_allowed()
 assert [r for e,r in locks.events if e=='acquire']==list(OUTER+RUNTIME)
 assert [r for e,r in locks.events if e=='release']==list(reversed(RUNTIME))+list(reversed(OUTER))
 assert not locks._held

@pytest.mark.parametrize('role',OUTER+RUNTIME)
def test_each_native_contention_partial_release(role):
 locks=owner();p=holder(locks.namespace,role)
 try:
  with pytest.raises(LockBusy):
   with locks.outer_lock():
    if role in RUNTIME:
     with locks.quiescent():pass
  acquired=[r for e,r in locks.events if e=='acquire']
  released=[r for e,r in locks.events if e=='release']
  assert released==list(reversed(acquired))
  assert not locks._held
  # Another process can immediately acquire a previously acquired/released role.
  first=next((r for r in acquired if r!=role),None)
  if first:
   probe=holder(locks.namespace,first);finish(probe)
 finally:finish(p)

@pytest.mark.parametrize('role',['ReconnectEnrollment','Arrival'])
@pytest.mark.parametrize('accept',[False,True])
def test_native_abandonment_requires_revalidation(role,accept):
 locks=owner();p=holder(locks.namespace,role)
 # Keep object alive so ownership abandonment is observable after child exit.
 observation=locks.api.open(locks.name(role));finish(p,'abandon');seen=[]
 def validate(names):seen.append(names);return accept
 try:
  if accept:
   with locks.outer_lock(revalidate=validate):
    if role in RUNTIME:
     with locks.quiescent(revalidate=validate):assert seen==[(role,)]
    assert locks.assert_restart_allowed()
  else:
   with pytest.raises(RevalidationRequired):
    with locks.outer_lock(revalidate=validate):
     if role in RUNTIME:
      with locks.quiescent(revalidate=validate):pass
  assert seen==[(role,)] and not locks._held
 finally:locks.api.close(observation)

@pytest.mark.parametrize('change',['caller','task','worker','production','zero','boolean'])
def test_unsupported_session_refused_before_mutex_creation(change):
 c=context();s=c.caller_session
 bad={'caller':SessionContext(s+1,s,(s,)),'task':SessionContext(s,s+1,(s,)),
      'worker':SessionContext(s,s,(s,s+1)),'production':SessionContext(s,s,(s,),False),
      'zero':SessionContext(0,0,()),'boolean':SessionContext(True,s,())}[change]
 with pytest.raises(LockRefusal):WindowsMigrationLocks(uuid.uuid4().hex,bad)

def test_changed_session_observation_refuses_and_releases():
 c=context();current=[c];locks=WindowsMigrationLocks(uuid.uuid4().hex,c,session_probe=lambda:current[0])
 with pytest.raises(LockRefusal):
  with locks.outer_lock():
   current[0]=SessionContext(c.caller_session,c.task_session,(c.caller_session+1,))
   with locks.quiescent():pass
 assert not locks._held

def test_partial_native_open_failure_releases(monkeypatch):
 locks=owner();original=locks.api.open
 def broken(name):
  if name==locks.name('Eject-C'):raise OSError('injected open failure')
  return original(name)
 monkeypatch.setattr(locks.api,'open',broken)
 with pytest.raises(OSError):
  with locks.outer_lock():pass
 assert not locks._held
 p=holder(locks.namespace,'ReconnectEnrollment');finish(p)

def test_production_names_and_arbitrary_namespace_refused():
 api=Native()
 for name in ['Local\\PrometheusReconnectEnrollment','Local\\PrometheusUsbLifecycle','Global\\PrometheusHostAgent']:
  with pytest.raises(LockRefusal):api.open(name)
 with pytest.raises(LockRefusal):WindowsMigrationLocks('production',context())
 assert WindowsMigrationLocks.production_activation_available is False

@pytest.mark.parametrize('failure',['release','close'])
def test_cleanup_api_failure_attempts_every_handle_and_poisons(monkeypatch,failure):
 locks=owner();original_release=locks.api.release;original_close=locks.api.close
 released=[];closed=[];fail_once=[True]
 def release(h):
  released.append(h);ok=original_release(h)
  if failure=='release' and fail_once[0]:fail_once[0]=False;return False
  return ok
 def close(h):
  closed.append(h);ok=original_close(h)
  if failure=='close' and fail_once[0]:fail_once[0]=False;return False
  return ok
 monkeypatch.setattr(locks.api,'release',release);monkeypatch.setattr(locks.api,'close',close)
 with pytest.raises(LockRefusal):
  with locks.outer_lock():pass
 assert len(released)==len(OUTER) and closed==released
 assert locks.poisoned and len(locks.unresolved)==1
 with pytest.raises(LockRefusal):locks.assert_restart_allowed()
 with pytest.raises(LockRefusal):
  with locks.outer_lock():pass

@pytest.mark.parametrize('failure',['exception','invalid','timeout-close'])
def test_wait_failure_closes_handle_and_refuses_reuse(monkeypatch,failure):
 locks=owner();closed=[];original_close=locks.api.close
 def wait(h,millis):
  if failure=='exception':raise OSError('injected wait exception')
  return 0x102 if failure=='timeout-close' else 0xffffffff
 def close(h):
  closed.append(h);ok=original_close(h)
  return False if failure=='timeout-close' else ok
 monkeypatch.setattr(locks.api,'wait',wait);monkeypatch.setattr(locks.api,'close',close)
 with pytest.raises((OSError,LockRefusal)):
  with locks.outer_lock():pass
 assert len(closed)==1 and locks.poisoned
 with pytest.raises(LockRefusal):locks.assert_restart_allowed()
