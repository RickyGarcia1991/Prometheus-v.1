import os
from pathlib import Path
import subprocess
import sys
import pytest
from prometheus_assistant import project_recovery
from prometheus_assistant.coding_workspace import CodingWorkspace


@pytest.mark.parametrize('conflict', [False, True])
@pytest.mark.parametrize('phase', ['first_write', 'after_checks'])
def test_real_process_death_then_fresh_workspace_recovery(tmp_path, conflict, phase):
    root = tmp_path / 'project'; root.mkdir()
    history = tmp_path / 'history'
    original = b'\xef\xbb\xbfold\r\n'
    for name in ('a.txt', 'b.txt'): (root / name).write_bytes(original)
    code = '''
import json, os, sys
from pathlib import Path
from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.project_editor import ProjectEditor
from prometheus_assistant.project_change_set import propose_group, apply_group
ws = CodingWorkspace(Path(sys.argv[1]), Path(sys.argv[2]), resources=Path(sys.argv[1]).parent/'resources')
if sys.argv[3] == 'after_checks':
    tests = ws.root/'tests'; tests.mkdir()
    (tests/'test_pair.py').write_text('import unittest\\nfrom pathlib import Path\\nclass Pair(unittest.TestCase):\\n    def test_new(self):\\n        for name in ("a.txt","b.txt"):\\n            self.assertEqual(Path(name).read_text(encoding="utf-8-sig").strip(),"new")\\n')
def generate(messages, schema):
    return dict(path=json.loads(messages[-1]['content'])['path'], reason='Owner request', benefit='Consistent files', replacements=[dict(old_text='old', new_text='new')])
editor=ProjectEditor(ws,generate)
candidate=propose_group(editor,'Change old to new',['a.txt','b.txt'])
real_apply=ws.apply_edit
def die(*args):
    real_apply(*args)
    os._exit(23)
if sys.argv[3] == 'first_write':
    ws.apply_edit=die
else:
    real_tests = ws.run_tests
    def checked(*args, **kwargs):
        assert real_tests(*args, **kwargs)['exit_code'] == 0
        os._exit(23)
    ws.run_tests = checked
apply_group(editor,candidate)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(project_recovery.__file__).parents[1]), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-B', '-c', code, str(root), str(history), phase],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode == 23, result.stderr
    assert b'new' in (root/'a.txt').read_bytes()
    assert (root/'b.txt').read_bytes() == (original if phase == 'first_write' else original.replace(b'old', b'new'))
    restarted = CodingWorkspace(root, history, resources=tmp_path / 'resources')
    pending = project_recovery.latest(restarted)
    assert pending['interrupted']
    if conflict:
        (root/'b.txt').write_bytes(b'newer external work')
        with pytest.raises(ValueError, match='conflict'):
            project_recovery.restore(restarted, pending['edit_id'])
        assert b'new' in (root/'a.txt').read_bytes()
        assert (root/'b.txt').read_bytes() == b'newer external work'
    else:
        assert project_recovery.restore(restarted,pending['edit_id'])['recovered']
        assert project_recovery.latest(restarted) is None
        for name in ('a.txt','b.txt'): assert (root/name).read_bytes() == original
