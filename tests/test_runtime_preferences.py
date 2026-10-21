import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from prometheus_assistant.runtime_preferences import Preferences


def test_missing_snapshot_has_no_filesystem_side_effect(tmp_path):
    path = tmp_path / 'missing' / 'settings.sqlite3'
    store = Preferences(path)
    assert store.snapshot() == {'backend': 'automatic', 'revision': 0}
    assert store.child_environment({'X': '1'}) == {'X': '1'}
    assert not path.parent.exists()


def test_persistence_cas_and_aba(tmp_path):
    path = tmp_path / 'settings.sqlite3'
    store = Preferences(path)
    assert store.configure('cpu', 0)['revision'] == 1
    assert Preferences(path).snapshot()['backend'] == 'cpu'
    store.configure('automatic', 1)
    store.configure('cpu', 2)
    with pytest.raises(ValueError, match='review'):
        store.configure('automatic', 1)
    assert store.snapshot() == {'backend': 'cpu', 'revision': 3}
    # Even same-value changes consume a revision; a stale review never replays.
    assert store.configure('cpu', 3)['revision'] == 4


@pytest.mark.parametrize('backend,revision', [('gpu',0), (None,0), ('cpu',True), ('cpu',-1), ('cpu',1.0)])
def test_invalid_configuration_does_not_create_storage(tmp_path, backend, revision):
    path = tmp_path / 'settings.sqlite3'
    with pytest.raises(ValueError):
        Preferences(path).configure(backend, revision)
    assert not path.exists()


@pytest.mark.parametrize('payload', [b'', b'not a database'])
def test_corrupt_database_is_never_reset(tmp_path, payload):
    path = tmp_path / 'settings.sqlite3'
    path.write_bytes(payload)
    store = Preferences(path)
    for operation in (store.snapshot, lambda: store.configure('cpu',0)):
        with pytest.raises(ValueError):
            operation()
    assert path.read_bytes() == payload


def test_malformed_record_rejected(tmp_path):
    path = tmp_path / 'settings.sqlite3'
    store = Preferences(path)
    store.configure('cpu',0)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE runtime_preference SET backend='unexpected'")
    with pytest.raises(ValueError):
        store.snapshot()
    with pytest.raises(ValueError):
        store.configure('automatic',1)


def test_environment_preserved_or_child_only_overridden(tmp_path):
    store = Preferences(tmp_path / 'settings.sqlite3')
    original = {'OTHER':'unchanged','OLLAMA_LLM_LIBRARY':'cuda','ollama_vulkan':'true'}
    assert store.child_environment(original) == original
    store.configure('cpu',0)
    child = store.child_environment(original)
    assert child['OLLAMA_LLM_LIBRARY'] == 'cpu'
    assert child['OLLAMA_VULKAN'] == '0'
    assert 'ollama_vulkan' not in child
    assert all(child[key] == '-1' for key in (
        'GGML_VK_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES'))
    assert original == {'OTHER':'unchanged','OLLAMA_LLM_LIBRARY':'cuda','ollama_vulkan':'true'}
    store.configure('automatic',1)
    assert store.child_environment(original) == original


def test_default_requires_absolute_windows_local_profile(tmp_path, monkeypatch):
    if os.name != 'nt':
        with pytest.raises(ValueError):
            Preferences()
        return
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert Preferences().path == tmp_path / 'Prometheus' / 'runtime-preferences.sqlite3'
    monkeypatch.setenv('LOCALAPPDATA', 'relative')
    with pytest.raises(ValueError):
        Preferences()
    monkeypatch.delenv('LOCALAPPDATA')
    with pytest.raises(ValueError):
        Preferences()


def test_symlink_rejected(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires Windows privilege')
    with pytest.raises(ValueError, match='Linked'):
        Preferences(link / 'settings.sqlite3')


@pytest.mark.parametrize('existing', [False, True])
def test_independent_process_compare_and_set_has_one_winner(tmp_path, existing):
    path = tmp_path / 'settings.sqlite3'
    revision = 0
    if existing:
        revision = Preferences(path).configure('automatic',0)['revision']
    code = '''import json,sys
from prometheus_assistant.runtime_preferences import Preferences
sys.stdin.readline()
try:
    result=Preferences(sys.argv[1]).configure('cpu',int(sys.argv[2]))
    print(json.dumps({'won':True,'revision':result['revision']}))
except ValueError:
    print(json.dumps({'won':False}))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    processes=[]
    try:
        for _ in range(2):
            processes.append(subprocess.Popen([sys.executable,'-B','-c',code,str(path),str(revision)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        for process in processes:
            process.stdin.write(b'go\n')
            process.stdin.flush()
        results=[]
        for process in processes:
            output,error=process.communicate(timeout=10)
            assert process.returncode==0,error
            results.append(json.loads(output))
        assert sum(row['won'] for row in results)==1
        assert Preferences(path).snapshot()=={'backend':'cpu','revision':revision+1}
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
