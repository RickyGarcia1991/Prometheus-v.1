"""Actual signed R5/R7 + Windows DPAPI + fresh-process crash recovery, synthetic state."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
import pytest
import active_migration_windows_store as s
pytestmark=pytest.mark.skipif(os.name!='nt',reason='native Windows only')
B=Path(__file__).resolve().parent
CHILD=r'''
import sys,os
from active_migration_windows_store import WindowsMigrationStore
import active_host_migration_core as core
point=sys.argv[2]
def fault(name):
 if name==point:os._exit(71)
f=WindowsMigrationStore(sys.argv[1],fault=fault)
raw=f.read_journal();phase=f._validate_journal(raw) if raw else None
for step in ('prepared','disable','stop','backup'):
 if phase is None or core.PHASES.index(phase)<=core.PHASES.index(step):f.write_journal(core.seal(f.plan,step,f.key));phase=step
if core.PHASES.index(phase)<=core.PHASES.index('backup'):f.backup_verified(f.plan)
if core.PHASES.index(phase)<=core.PHASES.index('replace'):
 f.write_journal(core.seal(f.plan,'replace',f.key));f.replace_owned(f.plan)
print('filesystem replacement qualified',flush=True)
'''
def execute(root,point='none'):
 return subprocess.run([sys._base_executable,'-B','-c',CHILD,str(root),point],cwd=B,capture_output=True,text=True,timeout=55,creationflags=subprocess.CREATE_NO_WINDOW)
def snapshot(root):
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
def immutable(root):
 return {str(p.relative_to(root)):s.digest(p.read_bytes()) for p in [root/'plan.json',root/'observations.json',root/'task.xml',root/'runtime/authorized-host.json',s.chain.receipt_path(root)]}
def phase(f,value):f.write_journal(s.core.seal(f.plan,value,f.key))
def to_backup(f):
 for value in ('prepared','disable','stop','backup'):phase(f,value)

@pytest.mark.parametrize('point',['journal-before-write','journal-after-flush','journal-after-replace','journal-after-readback','backup-before-intent','backup-after-intent','backup-before-write','backup-after-flush','backup-after-readback','backup-after-receipt','replacement-before-intent','replacement-after-intent','replacement-before-write','replacement-after-flush','replacement-before-replace','replacement-after-replace','replacement-after-readback','replacement-after-receipt'])
def test_fresh_process_interruption_and_resume(point):
 root,plan=s.setup_fixture();before=immutable(root)
 first=execute(root,point);assert first.returncode==71,(first.stdout,first.stderr)
 second=execute(root);assert second.returncode==0,(second.stdout,second.stderr)
 assert (root/'runtime'/s.PAYLOAD).read_bytes()==s.bounded(s.chain.NEW/s.PAYLOAD)
 assert immutable(root)==before
 f=s.WindowsMigrationStore(root)
 assert f.signed_evidence['native_verified'] and f.signed_evidence['unchanged_entries']==6
 assert f._validate_journal(f.read_journal())=='replace'
 assert f._record(f.directory/'replacement-receipt.json','replacement-receipt')['sha256']==plan['new_file_sha256']
 # No task restore/start/complete was performed or claimed.
 assert not f.production_activation_available

@pytest.mark.parametrize('damage',['journal','key','missing-key','auth','original-receipt','other-runtime','foreign-target','same-new-without-intent','foreign-backup','same-backup-without-intent','foreign-stage','same-stage-without-intent','partial-owned-backup','partial-owned-stage'])
def test_ambiguity_refuses_and_preserves(damage):
 root,plan=s.setup_fixture();f=s.WindowsMigrationStore(root);to_backup(f)
 if damage=='journal':f.journal.write_bytes(b'{"foreign":true}')
 elif damage=='key':f.keyfile.write_bytes(b'not DPAPI')
 elif damage=='missing-key':f.keyfile.unlink()
 elif damage=='auth':f.auth.write_bytes(b'{"changed":true}')
 elif damage=='original-receipt':f.original.write_bytes(b'foreign receipt')
 elif damage=='other-runtime':(f.runtime/'Prometheus-Arrival.ps1').write_bytes(b'foreign other runtime')
 elif damage=='foreign-target':f.target.write_bytes(b'foreign target')
 elif damage=='same-new-without-intent':phase(f,'replace');f.target.write_bytes(f.new)
 elif damage in ('foreign-backup','same-backup-without-intent'):(f.directory/('backup-'+'a'*32+'.bin')).write_bytes(f.old if damage.startswith('same') else b'foreign')
 elif damage=='partial-owned-backup':
  intent=f._intent('backup');f._owned_path('backup',intent).write_bytes(f.old[:20])
 else:
  f.backup_verified(plan);phase(f,'replace')
  if damage=='partial-owned-stage':
   intent=f._intent('replacement');f._owned_path('replacement',intent).write_bytes(f.new[:20])
  else:(f.directory/('replacement-'+'a'*32+'.bin')).write_bytes(f.new if damage.startswith('same') else b'foreign')
 before=snapshot(root)
 try:
  resumed=s.WindowsMigrationStore(root)
  if damage in ('foreign-stage','same-stage-without-intent','partial-owned-stage','same-new-without-intent'):resumed.replace_owned(plan)
  else:resumed.backup_verified(plan)
 except (ValueError,OSError):pass
 else:pytest.fail('Ambiguity unexpectedly accepted')
 assert snapshot(root)==before

def test_wrong_root_key_and_pointer_entropy_refused():
 root,plan=s.setup_fixture();f=s.WindowsMigrationStore(root)
 other,otherplan=s.setup_fixture();g=s.WindowsMigrationStore(other)
 g.keyfile.write_bytes(f.keyfile.read_bytes());before=snapshot(other)
 with pytest.raises(ValueError):s.WindowsMigrationStore(other)
 assert snapshot(other)==before
 with pytest.raises(OSError):s.chain.dpapi(f.keyfile.read_bytes(),False)

def test_phase_skips_and_no_backup_replace_refused_without_mutation():
 root,plan=s.setup_fixture();f=s.WindowsMigrationStore(root);before=snapshot(root)
 with pytest.raises(ValueError):phase(f,'replace')
 with pytest.raises(ValueError):f.replace_owned(plan)
 assert snapshot(root)==before
 to_backup(f)
 phase(f,'replace');before=snapshot(root)
 with pytest.raises(ValueError):phase(f,'restore')
 assert snapshot(root)==before

def test_forged_backup_receipt_and_pending_journal_refused():
 root,plan=s.setup_fixture();f=s.WindowsMigrationStore(root);phase(f,'prepared')
 (f.directory/'journal.pending').write_bytes(b'foreign pending');before=snapshot(root)
 with pytest.raises(ValueError):phase(f,'disable')
 assert snapshot(root)==before

def test_wrong_root_marker_and_production_path_refused():
 root,plan=s.setup_fixture();f=s.WindowsMigrationStore(root)
 (root/'STORE-FIXTURE.json').write_bytes(b'{}');before=snapshot(root)
 with pytest.raises(ValueError):s.WindowsMigrationStore(root)
 assert snapshot(root)==before
 with pytest.raises(ValueError):s.WindowsMigrationStore(B)

@pytest.mark.parametrize('point',['backup-mid-write','replacement-mid-write'])
def test_partial_write_process_death_preserves_ambiguity(point):
 root,plan=s.setup_fixture();original=immutable(root)
 assert execute(root,point).returncode==71
 before=snapshot(root);resumed=execute(root)
 assert resumed.returncode!=0 and snapshot(root)==before and immutable(root)==original

def test_empty_journal_refused():
 root,plan=s.setup_fixture();f=s.WindowsMigrationStore(root);f.journal.write_bytes(b'');before=snapshot(root)
 with pytest.raises(ValueError):s.WindowsMigrationStore(root)
 assert snapshot(root)==before

@pytest.mark.parametrize('value',['new','foreign'])
def test_initial_foreign_target_creates_no_key(value):
 root,plan=s.setup_fixture();(root/'runtime'/s.PAYLOAD).write_bytes(s.bounded(s.chain.NEW/s.PAYLOAD) if value=='new' else b'foreign')
 before=snapshot(root)
 with pytest.raises(ValueError):s.WindowsMigrationStore(root)
 assert snapshot(root)==before and not (root/'ActiveHostMigration'/plan['transaction']).exists()

@pytest.mark.parametrize('damage',['backup-receipt','replacement-receipt','backup-bytes','replacement-intent','owned-stage','foreign-stage'])
def test_authenticated_malformed_completed_state_refused(damage):
 root,plan=s.setup_fixture();assert execute(root).returncode==0
 f=s.WindowsMigrationStore(root)
 for value in ('restore','start','complete'):phase(f,value)
 if damage=='backup-bytes':f._owned_path('backup',f._existing_intent('backup')).write_bytes(b'partial')
 elif damage in ('owned-stage','foreign-stage'):
  nonce=f._existing_intent('replacement')['nonce'] if damage=='owned-stage' else 'a'*32
  (f.directory/('replacement-'+nonce+'.bin')).write_bytes(f.new)
 else:(f.directory/(damage+'.json')).write_bytes(f._seal(damage,{'nonce':'a'*32,'sha256':plan['new_file_sha256']}))
 before=snapshot(root)
 with pytest.raises(ValueError):s.WindowsMigrationStore(root)
 assert snapshot(root)==before
