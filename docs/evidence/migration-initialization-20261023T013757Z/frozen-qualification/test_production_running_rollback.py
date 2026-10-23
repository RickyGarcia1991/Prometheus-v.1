"""Actual rollback callbacks+DPAPI store, injected Scheduler/process observations."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from test_active_host_migration_store_integration import setup,through_backup,core
from active_host_migration_windows_provider import ProductionProvider

@pytest.mark.parametrize('failure',[None,'before-native-start','after-native-start'])
def test_running_rollback_restart_recovery(setup,failure):
    open_store,original,root,old,new=setup;plan=dict(original,original_running=True)
    forward=open_store(True,override=plan);through_backup(forward,plan);forward.replace_owned(plan)
    for phase in ('restore','start'):forward.write_journal(core.seal(plan,phase,forward.key))
    journal=forward.journal.read_bytes();store=open_store(override=plan,mode='rollback')
    owner=object.__new__(ProductionProvider);owner.plan=plan;owner.store=store;owner.files=store.files;owner.locks=store.locks;owner.mode='rollback';owner.binding={}
    owner.authority=lambda plan:True;owner.assert_preservation=lambda:True
    owner.latest=dict(observation=dict(task=dict(state='Ready',enabled=True),workers=[]));calls=[];failed=False
    @contextmanager
    def quiet():
        owner.locks._runtime=True
        try:yield
        finally:owner.locks._runtime=False
    owner.locks.quiescent=quiet
    owner.locks.assert_restart_allowed=lambda:None
    def native(operation,*args):
        nonlocal failed
        if operation=='Start' and failure=='before-native-start' and not failed:
            failed=True;raise RuntimeError('before call')
        calls.append(operation);task=owner.latest['observation']['task']
        if operation=='Disable':task.update(enabled=False,state='Disabled')
        elif operation=='Restore':task.update(enabled=True,state='Ready')
        elif operation=='Start':
            task['state']='Running';owner.latest['observation']['workers']=[{},{}]
            if failure=='after-native-start' and not failed:failed=True;raise RuntimeError('after call')
    owner.native=SimpleNamespace(call=native)
    def pair():
        intent=store.record('rollback-start-intent.json','rollback-start-intent')
        return dict(wrapper=dict(creation_ticks=intent['not_before_ticks']+1),supervisor=dict(creation_ticks=intent['not_before_ticks']+2))
    owner.current_pair=pair
    if failure:
        with pytest.raises(RuntimeError):owner.rollback()
    if failure=='before-native-start':
        with pytest.raises(ValueError,match='Ambiguous'):owner.rollback()
        assert calls.count('Start')==0
    else:
        assert owner.rollback()['status']=='rolled-back'
        assert owner.rollback()['status']=='rolled-back'
        assert calls.count('Start')==1
    assert store.journal.read_bytes()==journal and (root/plan['payload']).read_bytes()==old


def test_stopped_rollback_orphan_receipt_refuses_completion(setup):
    open_store,plan,root,old,new=setup
    store=open_store(True);through_backup(store,plan);store.replace_owned(plan)
    rollback=open_store(mode='rollback');rollback.locks._runtime=True;rollback.rollback_owned(plan)
    rollback.files.create(rollback.directory/'rollback-start-receipt.json',b'orphan')
    owner=object.__new__(ProductionProvider);owner.store=rollback;owner.files=rollback.files;owner.plan=plan
    owner.authority=lambda plan:True;owner.assert_preservation=lambda:True
    owner.latest=dict(observation=dict(task=dict(enabled=True,state='Ready'),workers=[]))
    with pytest.raises(ValueError):owner.assert_rollback_complete()
