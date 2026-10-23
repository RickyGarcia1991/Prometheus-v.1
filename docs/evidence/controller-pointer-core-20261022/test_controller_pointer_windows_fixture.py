import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

PATH=Path(__file__).with_name('controller_pointer_windows_fixture.py')
spec=importlib.util.spec_from_file_location('windows_fixture',PATH)
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)


def run(root,nonce,fault='none',forward=False):
    return subprocess.run([sys.executable,str(PATH),str(root),nonce,fault]+(['--forward'] if forward else []),capture_output=True,text=True,timeout=20)

@pytest.mark.parametrize('fault',['before-intent','after-intent','before-cache-1.0.12','after-cache-1.0.12','before-cache-1.0.11','after-cache-1.0.11','before-recovery-floor','after-recovery-floor','after-primary-floor','before-pointer','after-pointer','before-receipt','after-receipt'])
def test_abrupt_process_exit_then_fresh_resume(tmp_path,fault):
    nonce=f.setup(tmp_path)
    killed=run(tmp_path,nonce,fault)
    assert killed.returncode==77,killed.stderr
    floor=f.Provider(tmp_path,nonce).read_floor()
    resumed=run(tmp_path,nonce)
    assert resumed.returncode==0,resumed.stderr
    result=json.loads(resumed.stdout)
    assert result['floor']=='1.0.11' and result['launched'] is False
    assert f.core.version(floor)<=f.core.version(result['floor'])
    assert json.loads((tmp_path/'pointer.json').read_bytes())['core']=={'v':55}
    again=run(tmp_path,nonce)
    assert again.returncode==0,again.stderr
    assert json.loads(again.stdout)==result

@pytest.mark.parametrize('damage',['intent','key-corrupt','key-missing','pointer','source','nonce','floor'])
def test_tampered_durable_state_refused(tmp_path,damage):
    nonce=f.setup(tmp_path)
    assert run(tmp_path,nonce,'after-intent').returncode==77
    if damage=='intent': (tmp_path/'intent.json').write_bytes(b'{}')
    if damage=='key-corrupt': (tmp_path/'key.dpapi').write_bytes(b'bad')
    if damage=='key-missing': (tmp_path/'key.dpapi').unlink()
    if damage=='pointer': (tmp_path/'pointer.json').write_bytes(b'{"controller_release":"other","core":1}')
    if damage=='source': (tmp_path/'candidate.payload').write_bytes(b'changed')
    if damage=='nonce': nonce='wrong'
    if damage=='floor': (tmp_path/'floor-recovery.json').write_bytes(b'{')
    previous=(tmp_path/'pointer.json').read_bytes()
    result=run(tmp_path,nonce)
    assert result.returncode!=0
    assert (tmp_path/'pointer.json').read_bytes()==previous


def test_cache_stage_has_no_floor_or_pointer_side_effect(tmp_path):
    nonce=f.setup(tmp_path);p=f.Provider(tmp_path,nonce)
    before=(p.read_floor(),p.read_pointer())
    with p.exclusive(): p.stage(p.plan.recovery)
    assert (p.read_floor(),p.read_pointer())==before
    assert p.read_intent(p.plan.transaction) is None


def test_native_lock_blocks_second_process(tmp_path):
    nonce=f.setup(tmp_path);p=f.Provider(tmp_path,nonce)
    with p.exclusive():
        other=run(tmp_path,nonce)
        assert other.returncode!=0 and p.read_intent(p.plan.transaction) is None
    assert run(tmp_path,nonce).returncode==0


def test_forward_recovery_retains_both_immutable_receipts(tmp_path):
    nonce=f.setup(tmp_path)
    assert run(tmp_path,nonce).returncode==0
    forward=run(tmp_path,nonce,forward=True)
    assert forward.returncode==0,forward.stderr
    assert json.loads(forward.stdout)['floor']=='1.0.12'
    assert len(list(tmp_path.glob('receipt-*.json')))==2
    assert json.loads(run(tmp_path,nonce).stdout)['floor']=='1.0.12'


def test_traversal_refused_before_setup_write(tmp_path):
    empty=tmp_path/'empty';empty.mkdir()
    traversal=tmp_path/'child'/'..'/'empty'
    with pytest.raises(ValueError): f.setup(traversal)
    with pytest.raises(ValueError): f.Provider(traversal,'no-nonce')
    assert not list(empty.iterdir())


def test_junction_refused_before_setup_write(tmp_path):
    target=tmp_path/'target';target.mkdir()
    junction=tmp_path/'junction'
    result=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    try:
        with pytest.raises(ValueError): f.setup(junction)
        with pytest.raises(ValueError): f.Provider(junction,'no-nonce')
        assert not list(target.iterdir())
    finally:
        os.rmdir(junction)


def test_temp_root_itself_refused():
    with pytest.raises(ValueError): f.isolated_root(f.tempfile.gettempdir())
