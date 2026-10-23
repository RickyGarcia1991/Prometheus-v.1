"""Isolated policy/bootstrap tests; never invoke production execution."""
import ctypes,hashlib,json,os
from pathlib import Path
import pytest
import execute_active_host_migration as entry
from active_host_migration_windows_io import PhysicalFiles,PhysicalRefusal
from test_active_host_migration_store_integration import setup

def test_exact_contract_rejects_missing_or_extra_dependency():
    value=dict(schema=1,plan_report_sha256='a'*64,transaction='b'*32,source_pins={name:'c'*64 for name in entry.DEPENDENCIES},operations=['initialize','resume','rollback'])
    entry.validate_contract(value,'initialize')
    value['source_pins']['unexpected.py']='d'*64
    with pytest.raises(ValueError):entry.validate_contract(value,'initialize')

@pytest.mark.skipif(os.name!='nt',reason='Windows handles')
def test_bootstrap_retains_exact_source_and_refuses_changed_pin(tmp_path):
    source=tmp_path/'source.py';source.write_bytes(b'exact')
    held=entry.HeldSources()
    try:
        assert held.read(source,hashlib.sha256(b'exact').hexdigest())==b'exact'
        with pytest.raises(OSError):source.write_bytes(b'changed')
        with pytest.raises(ValueError):held.read(source,'0'*64)
    finally:held.close()
    assert held.handles==[] and source.read_bytes()==b'exact'

@pytest.mark.parametrize('owner',['S-1-5-18','S-1-5-32-544'])
def test_administrative_owner_opt_in_is_explicit(owner):
    obj=PhysicalFiles.__new__(PhysicalFiles);obj.sid='current';acl=ctypes.create_string_buffer(b'\x02\x00\x08\x00\x00\x00\x00\x00')
    def security(handle,kind,info,owner_ptr,group,dacl_ptr,sacl,descriptor):
        ctypes.cast(owner_ptr,ctypes.POINTER(ctypes.c_void_p))[0]=1
        ctypes.cast(dacl_ptr,ctypes.POINTER(ctypes.c_void_p))[0]=ctypes.addressof(acl)
        return 0
    obj.security=security;obj._sid=lambda value:owner
    with pytest.raises(PhysicalRefusal):obj._acl(1)
    obj._acl(1,administrative_owner=True)
    obj._sid=lambda value:'foreign'
    with pytest.raises(PhysicalRefusal):obj._acl(1,administrative_owner=True)

def test_immutable_authorization_rejects_every_mutating_entry_before_io(tmp_path):
    obj=PhysicalFiles.__new__(PhysicalFiles);obj.root=tmp_path;obj.administrative_authorization_owner=True
    auth=tmp_path/'authorized-host.json';auth.write_bytes(b'original');other=tmp_path/'other'
    for action in [lambda:obj.replace(auth,other,b''),lambda:obj.replace(other,auth,b'original'),lambda:obj.publish_new(auth,other,b'original'),lambda:obj.publish_new(other,auth,b''),lambda:obj.mkdir(auth),lambda:obj.handle(auth,create=True).__enter__()]:
        with pytest.raises(PhysicalRefusal,match='immutable'):action()
    assert auth.read_bytes()==b'original' and not other.exists()

@pytest.mark.skipif(os.name!='nt',reason='Windows handles')
def test_only_exact_authorization_read_selects_admin_owner_policy(tmp_path,monkeypatch):
    import csv,io,subprocess
    sid=next(csv.reader(io.StringIO(subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True,creationflags=subprocess.CREATE_NO_WINDOW))))[1]
    files=PhysicalFiles(tmp_path,sid)
    for name in ('authorized-host.json','Prometheus-Host-Agent.ps1'):files.create(tmp_path/name,b'original')
    files.administrative_authorization_owner=True;selected=[];original=files._acl
    def observe(handle,**kwargs):
        selected.append(kwargs.get('administrative_owner',False));return original(handle,**kwargs)
    monkeypatch.setattr(files,'_acl',observe)
    files.read(tmp_path/'authorized-host.json');files.read(tmp_path/'Prometheus-Host-Agent.ps1');files.check_directory(tmp_path)
    assert selected==[True,False,False]

def test_durable_attempt_receipt_is_create_only(tmp_path):
    path=tmp_path/'attempt.json';entry.durable_new(path,{'passed':False})
    with pytest.raises(FileExistsError):entry.durable_new(path,{'passed':True})
    assert json.loads(path.read_bytes())=={'passed':False}

@pytest.mark.parametrize('changed',[False,True])
def test_initial_authority_rejects_stale_plan_before_constructor_write(monkeypatch,tmp_path,changed):
    import active_host_migration_windows as policy
    import active_host_migration_windows_provider as providers
    import active_host_migration_windows_native as native
    import inspect_active_host_migration as inspection
    plan={'transaction':'a'*32,'original_running':False};events=[]
    class FakeProvider:
        def __init__(self,value,*args,**kwargs):
            self.images=None;self.latest={}
            self.authority(value)
            events.append('constructor-write')
        def authority(self,value):return True
        def run(self):return {'status':'complete'}
        def close(self):events.append('closed')
    monkeypatch.setattr(providers,'ProductionProvider',FakeProvider)
    monkeypatch.setattr(policy,'validate_production_plan',lambda value:({'root':str(tmp_path)},{}))
    monkeypatch.setattr(policy,'build_plan',lambda *args:dict(plan,changed=True) if changed else plan)
    monkeypatch.setattr(inspection,'sessions_snapshot',lambda:{})
    result=entry.run_bound(plan,'initialize','a'*64)
    assert result['passed'] is not changed
    assert ('constructor-write' in events) is not changed

def test_transaction_appearing_at_outer_lock_is_not_adopted(setup,monkeypatch):
    from contextlib import contextmanager
    from test_active_host_migration_store_integration import SyntheticLocks
    from active_host_migration_windows_store import StoreRefusal
    open_store,plan,root,old,new=setup
    directory=root/'ActiveHostMigration'/plan['transaction']
    @contextmanager
    def concurrent_appearance(self):
        directory.mkdir(parents=True)
        (directory/'foreign-evidence').write_bytes(b'preserve')
        yield
    monkeypatch.setattr(SyntheticLocks,'outer_lock',concurrent_appearance)
    with pytest.raises(StoreRefusal,match='cannot adopt'):open_store(True)
    assert [item.name for item in directory.iterdir()]==['foreign-evidence']
    assert (directory/'foreign-evidence').read_bytes()==b'preserve'
    assert (root/plan['payload']).read_bytes()==old
