"""Real generated DPAPI store; injected original authority, no installed writes."""
import pytest
from test_active_host_migration_store_integration import setup
from active_host_migration_windows_store import ProductionMigrationStore,StoreRefusal
import execute_active_host_migration as entry

def reopen_initial(store,plan,guard):
    return ProductionMigrationStore(plan,store.files,store.locks,store.old,store.new,store.other_pins,store.authority,resume_initialization=True,initial_guard=guard)

def test_key_only_recovery_preserves_exact_key_and_reentrant_authority(setup):
    open_store,plan,root,old,new=setup;store=open_store(True);key=store.keyfile.read_bytes();calls=[]
    resumed=reopen_initial(store,plan,lambda existing:calls.append(existing))
    resumed.initialization_only()
    assert calls==[True] and resumed.keyfile.read_bytes()==key and resumed.key==store.key
    assert [p.name for p in resumed.directory.iterdir()]==['key.dpapi']

@pytest.mark.parametrize('extra',['journal.json','journal.pending','backup-intent.json','independent-baseline.json','foreign'])
def test_extra_evidence_refuses_initialization_recovery(setup,extra):
    open_store,plan,root,old,new=setup;store=open_store(True);(store.directory/extra).write_bytes(b'preserve');calls=[]
    with pytest.raises(StoreRefusal,match='key-only'):reopen_initial(store,plan,lambda existing:calls.append(existing))
    assert calls==[] and (store.directory/extra).read_bytes()==b'preserve'

def test_wrong_key_scope_refuses_before_guard(setup):
    open_store,plan,root,old,new=setup;store=open_store(True);changed=dict(plan,host='f'*64);calls=[]
    with pytest.raises(Exception):reopen_initial(store,changed,lambda existing:calls.append(existing))
    assert calls==[]

def test_initial_guard_only_precreate_not_reentrant_read_journal(setup):
    open_store,plan,root,old,new=setup
    # Construct an uninitialized store with the real fixture IO/authority parameters
    # obtained from a separately initialized transaction, keeping its evidence.
    existing=open_store(True);fresh=dict(plan,transaction='e'*32);calls=[]
    store=ProductionMigrationStore(fresh,existing.files,existing.locks,old,new,existing.other_pins,existing.authority,initialize=True,initial_guard=lambda owned:calls.append(owned))
    assert store.read_journal() is None and calls==[False]

@pytest.mark.parametrize('evidence,changed,accepted',[(['a'*32],False,True),(['a'*32,'foreign'],False,False),([],False,False),(['a'*32],True,False)])
def test_recovery_initial_guard_excludes_only_own_authenticated_entry(monkeypatch,tmp_path,evidence,changed,accepted):
    import active_host_migration_windows as policy
    import active_host_migration_windows_provider as providers
    import inspect_active_host_migration as inspection
    plan={'transaction':'a'*32,'original_running':False};(tmp_path/'ActiveHostMigration'/plan['transaction']).mkdir(parents=True);events=[]
    class FakeProvider:
        def __init__(self,value,*args,**kwargs):
            assert kwargs['resume_initialization'] and not kwargs['initialize']
            self.images=None;self.latest={'observation':{'migration_evidence':evidence}}
            kwargs['initial_guard'](self,True);events.append('baseline-before-task')
        def authority(self,value):return True
        def run(self):events.append('task-action');return {'status':'complete'}
        def close(self):pass
    monkeypatch.setattr(providers,'ProductionProvider',FakeProvider)
    monkeypatch.setattr(policy,'validate_production_plan',lambda value:({'root':str(tmp_path)},{}))
    def build(envelope,*args):
        assert envelope['observation']['migration_evidence']==[]
        return dict(plan,changed=True) if changed else plan
    monkeypatch.setattr(policy,'build_plan',build);monkeypatch.setattr(inspection,'sessions_snapshot',lambda:{})
    result=entry.run_bound(plan,'resume-initialization','a'*64)
    assert result['passed']==accepted
    assert events==(['baseline-before-task','task-action'] if accepted else [])
