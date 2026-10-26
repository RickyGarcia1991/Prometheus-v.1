"""Real authenticated store plus query-only injected independent process APIs."""
from types import SimpleNamespace
import pytest
from test_active_host_migration_store_integration import setup
from active_host_migration_windows_preservation import IndependentPreservation

def owner(setup):
    open_store,plan,root,old,new=setup;store=open_store(True)
    state=dict(alive=True,command='generated command',ports=[dict(port=54555,pid=101)])
    opened=[];closed=[]
    api=SimpleNamespace(Open=lambda rights,inherit,pid:opened.append(rights) or pid,require=lambda ok:None,identity=lambda h:dict(created='123',image='C:\\generated.exe',owner=store.files.sid),Wait=lambda h,t:258 if state['alive'] else 0,Close=lambda h:closed.append(h) or True)
    native=SimpleNamespace(call=lambda operation:dict(pids=[101],ports=list(state['ports'])))
    snapshot=lambda pid:dict(pid=pid,parent_pid=10,session_id=1,command_line=state['command'])
    preservation=IndependentPreservation(store,native,initialize=True,api=api,snapshot=snapshot)
    return preservation,store,state,opened,closed

def test_query_only_baseline_and_survival(setup):
    # pytest supplies setup; no production process API is invoked.
    preservation,store,state,opened,closed=owner(setup)
    assert all(rights&1==0 for rights in opened)
    assert preservation.assert_alive()
    preservation.close();assert closed==[101]

@pytest.mark.parametrize('change',['exit','command','port'])
def test_independent_drift_refuses_completion(setup,change):
    preservation,store,state,opened,closed=owner(setup)
    try:
        if change=='exit':state['alive']=False
        elif change=='command':state['command']='changed'
        else:state['ports']=[]
        with pytest.raises(ValueError):preservation.assert_alive()
    finally:preservation.close()
