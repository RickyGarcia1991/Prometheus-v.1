import os
from pathlib import Path
import subprocess
import threading

import pytest

from prometheus_assistant.coding_workspace import CodingWorkspace, MAX_OUTPUT_BYTES


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'hello.py').write_bytes(b'\xef\xbb\xbfprint("hello")\r\n')
    return CodingWorkspace(root, tmp_path / 'history', resources=tmp_path / 'resources')


def test_read_preview_apply_undo_preserves_bytes_and_persists_history(project):
    original = (project.root / 'hello.py').read_bytes()
    opened = project.read_file('hello.py')
    proposed = 'print("world")\n'
    preview = project.preview_edit('hello.py', proposed, opened['sha256'])
    assert '-print("hello")' in preview['diff']
    assert '+print("world")' in preview['diff']
    assert (project.root / 'hello.py').read_bytes() == original
    result = project.apply_edit('hello.py', proposed, opened['sha256'])
    assert result['changed']
    assert (project.root / 'hello.py').read_bytes() == b'\xef\xbb\xbfprint("world")\r\n'
    reopened = CodingWorkspace(project.root, project.history.parent, resources=project.resources)
    restored = reopened.undo_edit(result['edit_id'])
    assert restored['undone']
    assert (project.root / 'hello.py').read_bytes() == original
    with pytest.raises(ValueError, match='cannot be undone'):
        reopened.undo_edit(result['edit_id'])


def test_changed_file_conflicts_preserve_newer_work(project):
    opened = project.read_file('hello.py')
    (project.root / 'hello.py').write_text('newer work\n', encoding='utf-8')
    for method in (project.preview_edit, project.apply_edit):
        with pytest.raises(ValueError, match='changed since'):
            method('hello.py', 'stale edit', opened['sha256'])
    opened = project.read_file('hello.py')
    result = project.apply_edit('hello.py', 'approved edit\n', opened['sha256'])
    (project.root / 'hello.py').write_text('later work\n', encoding='utf-8')
    with pytest.raises(ValueError, match='newer work'):
        project.undo_edit(result['edit_id'])
    assert (project.root / 'hello.py').read_text() == 'later work\n'


def test_backup_checksum_failure_does_not_replace_file(project):
    opened = project.read_file('hello.py')
    result = project.apply_edit('hello.py', 'changed\n', opened['sha256'])
    (project.history / (result['edit_id'] + '.before')).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum'):
        project.undo_edit(result['edit_id'])
    assert 'changed' in project.read_file('hello.py')['content']


@pytest.mark.parametrize('path', ['../outside.py', '/outside.py', r'C:\outside.py',
                                  r'..\outside.py', 'hello.py:stream', '.env',
                                  '.env.local', '.env ', '.git./config', '.git/config', '.aws/credentials'])
def test_invalid_paths_are_rejected(project, path):
    with pytest.raises((ValueError, OSError)):
        project.read_file(path)


def test_list_skips_private_metadata_and_large_binary_files(project):
    (project.root / '.git').mkdir()
    (project.root / '.git' / 'config').write_text('private')
    (project.root / '.env').write_text('TOKEN=private')
    (project.root / 'huge.py').write_text('x' * 256_001)
    (project.root / 'image.png').write_bytes(b'image')
    assert [row['path'] for row in project.list_files()] == ['hello.py']


def test_noop_and_protected_projects(project, tmp_path):
    opened = project.read_file('hello.py')
    assert not project.apply_edit('hello.py', opened['content'], opened['sha256'])['changed']
    assert not project.history.exists()
    with pytest.raises(ValueError, match='signed application'):
        CodingWorkspace(project.root, tmp_path / 'history', protected_roots=[project.root])
    with pytest.raises(ValueError, match='absolute'):
        CodingWorkspace('relative', tmp_path / 'history')


def test_symlink_escape_rejected_when_platform_permits(project, tmp_path):
    outside = tmp_path / 'outside.py'
    outside.write_text('outside')
    link = project.root / 'link.py'
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('Creating symlinks requires privileges on this Windows host.')
    with pytest.raises(ValueError, match='Linked'):
        project.read_file('link.py')


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction behavior')
def test_real_junction_is_rejected(project, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.py').write_text('private')
    junction = project.root / 'linked'
    # Arguments are environment values, not interpolated shell code.
    env = os.environ.copy()
    env['PROMETHEUS_TEST_LINK'] = str(junction)
    env['PROMETHEUS_TEST_TARGET'] = str(outside)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             'New-Item -ItemType Junction -Path $env:PROMETHEUS_TEST_LINK -Target $env:PROMETHEUS_TEST_TARGET | Out-Null'],
                            env=env, capture_output=True, timeout=10)
    if result.returncode:
        pytest.skip('Host cannot create a test junction.')
    try:
        with pytest.raises(ValueError, match='Linked'):
            project.read_file('linked/secret.py')
        assert not any(row['path'].startswith('linked/') for row in project.list_files())
    finally:
        os.rmdir(junction)  # Remove the link itself; never recurse into its target.
    assert (outside / 'secret.py').read_text() == 'private'


def test_real_unittest_and_syntax_check(project):
    tests = project.root / 'tests'
    tests.mkdir()
    (tests / 'test_example.py').write_text(
        'import unittest\nclass Example(unittest.TestCase):\n    def test_sum(self):\n        self.assertEqual(24 * 17, 408)\n',
        encoding='utf-8')
    result = project.run_tests('python-unittest', 'tests', timeout=10)
    assert result['exit_code'] == 0, result
    assert 'OK' in result['output']
    assert not result['cancelled'] and not result['timed_out']
    assert project.run_tests('python-compile', 'hello.py', timeout=10)['exit_code'] == 0
    (project.root / 'broken.py').write_text('if invalid syntax\n')
    result = project.run_tests('python-compile', 'broken.py', timeout=10)
    assert result['exit_code'] != 0
    assert 'SyntaxError' in result['output']


def test_real_cancellation_and_output_bounds(project):
    tests = project.root / 'tests'
    tests.mkdir()
    (tests / 'test_wait.py').write_text('import time\nprint("x" * 100000, flush=True)\ntime.sleep(30)\n', encoding='utf-8')
    cancel = threading.Event()
    timer = threading.Timer(.8, cancel.set)
    timer.start()
    try:
        result = project.run_tests('python-unittest', 'tests', timeout=10, cancel_event=cancel)
    finally:
        timer.cancel()
    assert result['cancelled']
    assert result['duration_seconds'] < 8
    assert result['truncated']
    assert len(result['output'].encode()) <= MAX_OUTPUT_BYTES


def test_real_timeout_and_precancel(project):
    tests = project.root / 'tests'
    tests.mkdir()
    (tests / 'test_wait.py').write_text('import time\ntime.sleep(30)\n', encoding='utf-8')
    result = project.run_tests('python-unittest', 'tests', timeout=.4)
    assert result['timed_out']
    assert result['duration_seconds'] < 8
    cancel = threading.Event()
    cancel.set()
    result = project.run_tests('python-unittest', 'tests', cancel_event=cancel)
    assert result['cancelled'] and result['exit_code'] is None


def test_explicit_presets_only(project):
    with pytest.raises(ValueError, match='listed test preset'):
        project.run_tests('shell', 'echo unsafe')
    with pytest.raises(ValueError, match='inside'):
        project.run_tests('python-pytest', '../outside')
    with pytest.raises(ValueError, match='timeout'):
        project.run_tests('python-compile', 'hello.py', timeout=1000)
    assert len(project.test_presets()) == 4


def test_real_installed_node_when_available(project):
    from prometheus_assistant.hardware import resource_root
    actual = CodingWorkspace(project.root, project.history.parent, resources=resource_root())
    if not next(row['available'] for row in actual.test_presets() if row['id'] == 'node-test'):
        pytest.skip('Node runtime not installed on this host.')
    (project.root / 'example.test.cjs').write_text(
        "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
        "test('arithmetic', () => assert.equal(24 * 17, 408));\n", encoding='utf-8')
    result = actual.run_tests('node-test', 'example.test.cjs', timeout=15)
    assert result['exit_code'] == 0, result
    assert 'pass 1' in result['output'], result


@pytest.mark.skipif(os.name != 'nt', reason='Windows process job cleanup')
def test_successful_test_process_cleans_up_spawned_children(project):
    import ctypes
    from ctypes import wintypes
    tests = project.root / 'tests'
    tests.mkdir()
    (tests / 'test_child.py').write_text(
        "import subprocess, sys, unittest\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('CHILD_PID=' + str(child.pid), flush=True)\n"
        "class Example(unittest.TestCase):\n    def test_pass(self):\n        self.assertTrue(True)\n", encoding='utf-8')
    result = project.run_tests('python-unittest', 'tests', timeout=10)
    assert result['exit_code'] == 0, result
    child_pid = int(result['output'].split('CHILD_PID=')[1].split()[0])
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, child_pid)
    if handle:
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            assert code.value != 259, 'Test child remained running after successful completion.'
        finally:
            kernel.CloseHandle(handle)
