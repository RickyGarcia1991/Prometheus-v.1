import contextlib,copy,importlib.util,json
from pathlib import Path
import pytest
P=Path(__file__).with_name('active_host_migration_core.py')
spec=importlib.util.spec_from_file_location('migration',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
KEY=b'k'*32
@pytest.fixture
def plan():
 return dict(transaction='a'*32,host='b'*64,profile='fixed-profile',auth_sha256='c'*64,enrollment_sha256='9'*64,task_config_sha256='d'*64,task_identity='unchanged-existing-task',original_enabled=True,original_running=True,old_manifest=m.R5,new_manifest=m.R7,old_file_sha256='e'*64,new_file_sha256='f'*64,payload='Prometheus-Host-Agent.ps1',wrapper_identity='pid+creation+path+hash-wrapper',child_identity='pid+creation+path+hash-child')
class Provider:
 def __init__(self,plan,fault=None):
  self.plan=copy.deepcopy(plan);self.journal=None;self.enabled=plan['original_enabled'];self.running=plan['original_running'];self.file='old';self.backup=False;self.starts=0;self.fault=fault;self.events=[];self.locked=False;self.quiet=False
 def hit(self,label):
  self.events.append(label)
  if label==self.fault:self.fault=None;raise RuntimeError(label)
 @contextlib.contextmanager
 def outer_lock(self):
  self.locked=True
  try:yield
  finally:self.locked=False
 @contextlib.contextmanager
 def quiescent(self,plan):
  assert not self.enabled and not self.running
  self.quiet=True
  try:yield
  finally:self.quiet=False
 def verify(self,p):
  assert self.locked
  if p!=self.plan:raise ValueError('identity changed')
 def verify_original_enrollment(self,p):
  if p['enrollment_sha256']!=self.plan['enrollment_sha256']:raise ValueError('Enrollment receipt changed')
 def assert_original(self,p):assert self.enabled==p['original_enabled'] and self.running==p['original_running'] and self.file=='old'
 def read_journal(self):return self.journal
 def write_journal(self,raw):
  phase=json.loads(json.loads(raw)['body'])['phase'];self.hit('before-journal-'+phase);self.journal=raw;self.hit('after-journal-'+phase)
 def disable_owned(self,p):self.hit('before-disable');self.enabled=False;self.hit('after-disable')
 def stop_captured_pair(self,p):assert not self.enabled;self.hit('before-stop');self.running=False;self.hit('after-stop')
 def backup_verified(self,p):assert self.quiet;self.hit('before-backup');self.backup=True;self.hit('after-backup')
 def replace_owned(self,p):assert self.quiet and self.backup;self.hit('before-replace');self.file='new';self.hit('after-replace')
 def restore_enabled(self,p):assert not self.quiet;self.hit('before-restore');self.enabled=p['original_enabled'];self.hit('after-restore')
 def start_once(self,p):
  assert self.enabled and not self.quiet
  self.hit('before-start')
  if not self.running:self.running=True;self.starts+=1
  self.hit('after-start')
 def assert_complete(self,p):assert self.file=='new' and self.backup and self.enabled==p['original_enabled'] and self.running==p['original_running']

@pytest.mark.parametrize('fault',[prefix+step for step in ('disable','stop','backup','replace','restore','start') for prefix in ('before-','after-')]+[prefix+step for step in m.PHASES for prefix in ('before-journal-','after-journal-')])
def test_every_interruption_replays_safely(plan,fault):
 p=Provider(plan,fault)
 with pytest.raises(RuntimeError):m.migrate(p,plan,KEY)
 result=m.migrate(p,plan,KEY)
 assert result['status']=='complete' and p.starts==1
 before=list(p.events);m.migrate(p,plan,KEY)
 assert p.events==before and p.starts==1
 assert p.plan['auth_sha256']==plan['auth_sha256'] and p.plan['task_identity']==plan['task_identity']

@pytest.mark.parametrize('damage',['journal','key','auth','task','package'])
def test_changed_authority_refuses(plan,damage):
 p=Provider(plan,'after-journal-prepared')
 with pytest.raises(RuntimeError):m.migrate(p,plan,KEY)
 key=KEY
 if damage=='journal':p.journal=p.journal.replace(b'prepared',b'complete')
 if damage=='key':key=b'z'*32
 if damage=='auth':plan['auth_sha256']='1'*64
 if damage=='task':plan['task_identity']='foreign'
 if damage=='package':plan['new_manifest']='0'*64
 with pytest.raises((ValueError,AssertionError)):m.migrate(p,plan,key)
 assert p.enabled and p.running and p.file=='old' and not p.backup

@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('fault',['after-disable','after-stop','after-backup','after-replace','after-restore','after-journal-start','before-journal-complete'])
def test_stopped_baseline_restored_without_start(plan,enabled,fault):
 plan['original_enabled']=enabled;plan['original_running']=False
 p=Provider(plan,fault)
 with pytest.raises(RuntimeError):m.migrate(p,plan,KEY)
 m.migrate(p,plan,KEY)
 assert p.enabled==enabled and not p.running and p.starts==0
 assert 'before-start' not in p.events

@pytest.mark.parametrize('field',['auth_sha256','task_config_sha256','old_file_sha256','wrapper_identity','child_identity'])
def test_changed_observed_identity_refuses_before_mutation(plan,field):
 p=Provider(plan,'after-journal-prepared')
 with pytest.raises(RuntimeError):m.migrate(p,plan,KEY)
 p.plan[field]='changed live observation'
 before=list(p.events)
 with pytest.raises(ValueError):m.migrate(p,plan,KEY)
 assert p.events==before and p.file=='old' and p.running

@pytest.mark.parametrize('damage',['none','receipt','pending','key','runtime'])
def test_completed_migration_chain_keeps_old_receipt_immutable(plan,damage):
 p=Provider(plan);m.migrate(p,plan,KEY);key=KEY
 if damage=='receipt':p.plan['enrollment_sha256']='0'*64
 if damage=='pending':p.journal=m.seal(plan,'replace',key)
 if damage=='key':key=b'x'*32
 if damage=='runtime':p.file='foreign'
 before=list(p.events)
 if damage=='none':
  result=m.verify_completed_chain(p,plan,key)
  assert result['enrollment_sha256']==plan['enrollment_sha256']
 else:
  with pytest.raises((ValueError,AssertionError)):m.verify_completed_chain(p,plan,key)
 assert p.events==before
