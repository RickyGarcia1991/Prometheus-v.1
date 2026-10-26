"""API-injected production policy tests; no production mutex calls."""
from dataclasses import replace
import pytest
import active_host_migration_windows_locks as p


class Fake:
    def __init__(self):self.opened=[];self.closed=[];self.released=[]
    def thread(self):return 1
    def session_id(self):return 1
    def open(self,name):self.opened.append(name);return len(self.opened)
    def wait(self,handle,timeout):return 0
    def release(self,handle):self.released.append(handle);return True
    def close(self,handle):self.closed.append(handle);return True


def context():return p.ProductionSession('S-1-5-21-1-2-3-1002',1,'S-1-5-21-1-2-3-1002',1,(1,),(1,),True)


def test_literal_roles_share_ordered_owner_engine_without_native_calls():
    c=context();api=Fake();locks=p.ProductionMigrationLocks(c,session_probe=lambda:c,api=api)
    with locks.outer_lock():
        with locks.quiescent():
            with pytest.raises(p.engine.LockRefusal):locks.assert_restart_allowed()
        assert locks.assert_restart_allowed()
    assert api.opened==[p.production_mutex_name(x) for x in p.engine.OUTER+p.engine.RUNTIME]
    assert api.closed==list(range(30,27,-1))+list(range(27,0,-1))


@pytest.mark.parametrize('change',[dict(complete=False),dict(task_sid='S-1-5-99'),dict(task_session=2),dict(eligible_session_ids=(1,2)),dict(eligible_session_ids=(True,)),dict(worker_session_ids=(2,)),dict(session_id=0),dict(session_id=True)])
def test_unsupported_context_refuses_before_acquiring(change):
    c=replace(context(),**change);api=Fake()
    with pytest.raises(p.engine.LockRefusal):p.ProductionMigrationLocks(c,session_probe=lambda:c,api=api)
    assert not api.opened


def test_fresh_session_drift_refuses_and_releases_partial_outer_set():
    c=context();api=Fake();calls=0
    def probe():
        nonlocal calls
        calls+=1
        return c if calls<6 else replace(c,eligible_session_ids=(1,2))
    locks=p.ProductionMigrationLocks(c,session_probe=probe,api=api)
    with pytest.raises(p.engine.LockRefusal):
        with locks.outer_lock():pass
    assert api.opened and len(api.closed)==len(api.opened)
