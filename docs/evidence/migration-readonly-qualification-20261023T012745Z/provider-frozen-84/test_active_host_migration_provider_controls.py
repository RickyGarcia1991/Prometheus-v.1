"""Injected callback controls; no native provider construction or mutation."""
from types import SimpleNamespace
import json
import pytest
import active_host_migration_windows_provider as p
import active_host_migration_windows_images as images

@pytest.mark.parametrize('state',['Running','Queued','Unknown'])
def test_stopped_completion_requires_converged_scheduler_state(tmp_path,state):
    owner=object.__new__(p.ProductionProvider)
    owner.verify=lambda plan:None
    owner.assert_preservation=lambda:True
    owner.store=SimpleNamespace(directory=tmp_path,verify_replacement=lambda:None)
    owner.latest=dict(observation=dict(task=dict(enabled=True,state=state),workers=[]))
    with pytest.raises(ValueError):owner.assert_complete(dict(original_enabled=True,original_running=False))

def test_running_receipt_cannot_override_nonrunning_scheduler():
    owner=object.__new__(p.ProductionProvider)
    owner.latest=dict(observation=dict(task=dict(state='Ready')))
    with pytest.raises(ValueError):owner.assert_current_start({})

def test_image_close_exception_attempts_remaining_handles(monkeypatch):
    owner=object.__new__(images.ImagePins);owner.handles={'a':(1,{}),'b':(2,{})};owner.unresolved=[];seen=[]
    def close(handle):
        seen.append(handle)
        if handle==1:raise OSError('injected')
        return True
    monkeypatch.setattr(images.q,'Close',close)
    with pytest.raises(RuntimeError):owner.close()
    assert seen==[1,2] and len(owner.unresolved)==1

def test_image_pin_refusal_retains_raised_close_uncertainty(monkeypatch):
    owner=object.__new__(images.ImagePins);owner.handles={};owner.unresolved=[]
    owner.open=lambda *args:77
    owner.final=lambda *args:0
    def close(handle):raise OSError('injected close')
    monkeypatch.setattr(images.q,'Close',close)
    with pytest.raises(ValueError):owner.probe('C:\\generated-injected-only.exe')
    assert owner.unresolved[0]['handle']==77

@pytest.mark.parametrize('wait_result',[0xffffffff,17])
def test_lost_stop_unknown_wait_refuses_and_closes_probe(monkeypatch,wait_result):
    owner=object.__new__(p.ProductionProvider);owner.original_pair=None;owner.verify=lambda plan:None
    closed=[]
    monkeypatch.setattr(p.q,'Open',lambda *args:77)
    monkeypatch.setattr(p.q,'Wait',lambda *args:wait_result)
    monkeypatch.setattr(p.q,'Close',lambda h:closed.append(h) or True)
    plan=dict(original_running=True,wrapper_identity=json.dumps(dict(pid=101,creation_ticks=123)),child_identity='{}')
    with pytest.raises(ValueError,match='wait unknown'):owner.stop_captured_pair(plan)
    assert closed==[77]
