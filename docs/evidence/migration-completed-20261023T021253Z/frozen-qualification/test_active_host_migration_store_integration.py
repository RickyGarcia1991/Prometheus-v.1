"""Real file handles/DPAPI on generated roots; synthetic authority and locks.

These are storage integration tests, not signed-package or task acceptance.
"""
import csv,io,os,subprocess,uuid
from contextlib import contextmanager
import pytest
import active_host_migration_core as core
from active_host_migration_windows_io import PhysicalFiles
from active_host_migration_windows_store import ProductionMigrationStore,StoreRefusal,digest

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI/file handles')

class SyntheticLocks:
    def _check(self):pass
    @contextmanager
    def outer_lock(self):yield

@pytest.fixture
def setup(tmp_path):
    sid=next(csv.reader(io.StringIO(subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True,creationflags=subprocess.CREATE_NO_WINDOW))))[1]
    old,new=b'generated old payload',b'generated new payload'
    auth,receipt=b'generated authorization',b'generated original receipt'
    enrollment=uuid.uuid4().hex
    (tmp_path/'Enrollment'/enrollment).mkdir(parents=True)
    (tmp_path/'Enrollment'/enrollment/'journal.json').write_bytes(receipt)
    (tmp_path/'authorized-host.json').write_bytes(auth)
    (tmp_path/'Prometheus-Host-Agent.ps1').write_bytes(old)
    pins={}
    for name in ['Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1']:
        data=('generated inert '+name).encode();(tmp_path/name).write_bytes(data);pins[name]=digest(data)
    plan=dict(transaction=uuid.uuid4().hex,host='a'*64,profile=core.encode(dict(root=str(tmp_path),sid=sid)).decode(),auth_sha256=digest(auth),enrollment_sha256=digest(receipt),task_config_sha256='b'*64,task_identity=core.encode(dict(enrollment_id=enrollment)).decode(),original_enabled=True,original_running=False,old_manifest=core.R5,new_manifest=core.R7,old_file_sha256=digest(old),new_file_sha256=digest(new),payload='Prometheus-Host-Agent.ps1',wrapper_identity='absent',child_identity='absent')
    files=PhysicalFiles(tmp_path,sid)
    def open_store(initialize=False,fault=None,override=None,mode='forward'):
        return ProductionMigrationStore(override or plan,files,SyntheticLocks(),old,new,pins,lambda p:True,initialize=initialize,fault=fault,mode=mode)
    return open_store,plan,tmp_path,old,new

def through_backup(store,plan):
    for phase in ('prepared','disable','stop','backup'):
        store.write_journal(core.seal(plan,phase,store.key))
    store.backup_verified(plan)
    store.write_journal(core.seal(plan,'replace',store.key))

def test_native_forward_reopen_preserves_authority(setup):
    open_store,plan,root,old,new=setup
    auth=(root/'authorized-host.json').read_bytes()
    store=open_store(True);through_backup(store,plan);store.replace_owned(plan)
    for phase in ('restore','start','complete'):store.write_journal(core.seal(plan,phase,store.key))
    reopened=open_store();reopened.verify_backup();reopened.verify_replacement()
    assert reopened.phase(reopened.read_journal())=='complete'
    assert (root/plan['payload']).read_bytes()==new
    assert (root/'authorized-host.json').read_bytes()==auth

@pytest.mark.parametrize('point',['backup-after-flush','replacement-after-flush','replacement-after-replace'])
def test_native_interruption_reopens_without_losing_original(setup,point):
    open_store,plan,root,old,new=setup
    def fault(where):
        if where==point:raise RuntimeError('generated interruption')
    store=open_store(True,fault)
    with pytest.raises(RuntimeError):
        through_backup(store,plan);store.replace_owned(plan)
    resumed=open_store()
    if resumed.phase(resumed.read_journal())=='backup':
        resumed.backup_verified(plan);resumed.write_journal(core.seal(plan,'replace',resumed.key))
    resumed.replace_owned(plan);resumed.verify_replacement()
    assert (root/plan['payload']).read_bytes()==new

def test_missing_key_is_not_regenerated(setup):
    open_store,plan,root,old,new=setup
    store=open_store(True);retained=store.keyfile.with_suffix('.retained');store.keyfile.rename(retained)
    with pytest.raises(OSError):open_store()
    assert retained.exists() and not store.keyfile.exists()
    assert (root/plan['payload']).read_bytes()==old

def test_completed_replacement_receipt_with_old_target_refused(setup):
    open_store,plan,root,old,new=setup
    store=open_store(True);through_backup(store,plan)
    value=store.intent('replacement')
    store.files.create(store.directory/'replacement-receipt.json',store.seal_record('replacement-receipt',dict(nonce=value['nonce'],sha256=plan['new_file_sha256'])))
    with pytest.raises(StoreRefusal):open_store()
    assert (root/plan['payload']).read_bytes()==old

def test_foreign_target_and_changed_plan_refused(setup):
    open_store,plan,root,old,new=setup
    store=open_store(True);through_backup(store,plan)
    (root/plan['payload']).write_bytes(b'foreign')
    with pytest.raises(StoreRefusal):open_store()
    assert (root/plan['payload']).read_bytes()==b'foreign'
    (root/plan['payload']).write_bytes(old)
    altered=dict(plan,original_enabled=False)
    with pytest.raises((StoreRefusal,OSError)):open_store(override=altered)

@pytest.mark.parametrize('point',[None,'rollback-after-flush','rollback-after-replace'])
def test_native_rollback_replay_preserves_forward_journal(setup,point):
    open_store,plan,root,old,new=setup
    store=open_store(True);through_backup(store,plan);store.replace_owned(plan)
    for phase in ('restore','start','complete'):store.write_journal(core.seal(plan,phase,store.key))
    forward=store.journal.read_bytes()
    def fault(where):
        if where==point:raise RuntimeError('generated rollback interruption')
    rollback=open_store(mode='rollback',fault=fault)
    with pytest.raises(StoreRefusal):rollback.rollback_owned(plan)
    assert (root/plan['payload']).read_bytes()==new
    rollback.locks._runtime=True  # Synthetic lock gate; no production mutex acquired.
    if point:
        with pytest.raises(RuntimeError):rollback.rollback_owned(plan)
    else:rollback.rollback_owned(plan)
    resumed=open_store(mode='rollback');resumed.locks._runtime=True;resumed.rollback_owned(plan)
    assert (root/plan['payload']).read_bytes()==old
    assert resumed.journal.read_bytes()==forward
    resumed.verify_backup();assert resumed.rollback_state() is not None
    with pytest.raises(StoreRefusal):open_store()
