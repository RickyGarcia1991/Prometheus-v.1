"""Real callback/store, injected task observations; never calls a production task."""
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
import active_host_migration_core as core
from active_host_migration_windows_provider import ProductionProvider
from test_active_host_migration_store_integration import setup,through_backup

def provider(setup):
    open_store,plan,root,old,new=setup
    store=open_store(True);through_backup(store,plan);store.replace_owned(plan)
    for phase in ('restore','start'):store.write_journal(core.seal(plan,phase,store.key))
    p=object.__new__(ProductionProvider)
    p.plan=plan;p.store=store;p.files=store.files;p.binding={};p.fault=lambda point:None
    p.verify=lambda plan:None;p.authority=lambda plan:True
    p.assert_preservation=lambda:True  # Injected; independent OS evidence is tested separately.
    p.locks=SimpleNamespace(assert_restart_allowed=lambda:None)
    p.latest={'observation':{'workers':[],'task':{'state':'Ready'}}}
    calls=[]
    def start(*args):
        calls.append(args)
        p.latest['observation']={'workers':[{},{}],'task':{'state':'Running'}}
    p.native=SimpleNamespace(call=start)
    p.current_pair=lambda:{'wrapper':{'creation_ticks':p.start_intent()['not_before_ticks']+1},'supervisor':{'creation_ticks':p.start_intent()['not_before_ticks']+2}}
    return p,plan,calls

def test_lost_before_start_refuses_automatic_repeat(setup):
    p,plan,calls=provider(setup)
    def crash(point):
        if point=='before-start-call':raise RuntimeError('injected interruption')
    p.fault=crash
    with pytest.raises(RuntimeError):p.start_once(plan)
    assert not calls
    p.fault=lambda point:None
    with pytest.raises(ValueError,match='ambiguous'):p.start_once(plan)
    assert not calls and not (p.store.directory/'start-receipt.json').exists()

def test_lost_after_start_recovers_without_second_call(setup):
    p,plan,calls=provider(setup)
    def crash(point):
        if point=='after-start-call':raise RuntimeError('injected interruption')
    p.fault=crash
    with pytest.raises(RuntimeError):p.start_once(plan)
    assert len(calls)==1
    p.fault=lambda point:None;p.start_once(plan);p.start_once(plan)
    assert len(calls)==1 and (p.store.directory/'start-receipt.json').exists()

def test_runtime_lock_refusal_precedes_start_intent(setup):
    p,plan,calls=provider(setup)
    def refuse():raise RuntimeError('runtime locks held')
    p.locks.assert_restart_allowed=refuse
    with pytest.raises(RuntimeError):p.start_once(plan)
    assert not calls and not (p.store.directory/'start-intent.json').exists()

def test_old_pair_cannot_complete_new_start(setup):
    p,plan,calls=provider(setup)
    p.current_pair=lambda:{'wrapper':{'creation_ticks':1},'supervisor':{'creation_ticks':2}}
    with pytest.raises(ValueError,match='predates'):p.start_once(plan)
    assert len(calls)==1 and not (p.store.directory/'start-receipt.json').exists()

def test_completed_receipt_cannot_authorize_changed_pair(setup):
    p,plan,calls=provider(setup);p.start_once(plan)
    p.current_pair=lambda:{'wrapper':{'creation_ticks':p.start_intent()['not_before_ticks']+100},'supervisor':{'creation_ticks':p.start_intent()['not_before_ticks']+200}}
    with pytest.raises(ValueError,match='mismatch'):p.start_once(plan)
    assert len(calls)==1

def test_orphan_receipt_preserved_without_new_intent(setup):
    p,plan,calls=provider(setup)
    path=p.store.directory/'start-receipt.json';path.write_bytes(b'generated orphan')
    with pytest.raises(ValueError,match='Orphan'):p.start_once(plan)
    assert not calls and path.read_bytes()==b'generated orphan'
    assert not (p.store.directory/'start-intent.json').exists()

@pytest.mark.parametrize('fault_point',[None,'rollback-after-flush','rollback-after-replace'])
def test_stopped_rollback_task_lifecycle_and_replay(setup,fault_point):
    p,plan,calls=provider(setup)
    open_store,_,root,old,new=setup
    forward=p.store.journal.read_bytes()
    def fault(point):
        if point==fault_point:raise RuntimeError('generated file rollback interruption')
    p.mode='rollback';p.store=open_store(mode='rollback',fault=fault);p.locks=p.store.locks
    p.latest['observation']['task']['enabled']=True
    @contextmanager
    def quiet():
        p.locks._runtime=True
        try:yield
        finally:p.locks._runtime=False
    p.locks.quiescent=quiet
    p.locks.assert_restart_allowed=lambda:None
    def native(operation,*args):
        calls.append(operation)
        task=p.latest['observation']['task']
        if operation=='Disable':task.update(enabled=False,state='Disabled')
        elif operation=='Restore':task.update(enabled=True,state='Ready')
        else:raise AssertionError('Stopped baseline must not start')
    p.native=SimpleNamespace(call=native)
    if fault_point:
        with pytest.raises(RuntimeError,match='generated file'):p.rollback()
        p.store.fault=lambda point:None
    assert p.rollback()['status']=='rolled-back'
    completed_calls=list(calls)
    assert p.rollback()['status']=='rolled-back' and calls==completed_calls
    assert (root/plan['payload']).read_bytes()==old
    assert p.store.journal.read_bytes()==forward
    assert calls==['Disable','Restore']
