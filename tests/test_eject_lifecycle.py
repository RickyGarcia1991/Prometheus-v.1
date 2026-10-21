"""Real local process boundaries; no live USB or service mutation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle policy')
def test_lifecycle_policy_and_ownership():
    env = os.environ.copy()
    env['PSModulePath'] = str(PS.parent / 'Modules')
    result = subprocess.run([str(PS), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(ROOT / 'tests/test_lifecycle_policy.ps1')],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['failed'] == 0 and report['passed'] >= 21


@pytest.mark.parametrize('marker', ['active-chat.shutdown', 'eject-mode.json'])
def test_running_studio_exits_on_shared_shutdown(tmp_path, marker):
    host = tmp_path / 'Prometheus'
    host.mkdir()
    ready = tmp_path / 'ready.txt'
    env = os.environ.copy()
    env['LOCALAPPDATA'] = str(tmp_path)
    process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(ROOT / 'tools/maps-studio/studio.py'),
                                '--no-browser', '--ready-file', str(ready)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert ready.exists(), 'Studio did not start'
        (host / marker).write_text('{}')
        output, errors = process.communicate(timeout=5)
        assert process.returncode == 0, errors.decode()
        assert output.startswith(b'http://127.0.0.1:')
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(5)


def test_studio_refuses_start_during_eject(tmp_path):
    host = tmp_path / 'Prometheus'
    host.mkdir()
    (host / 'eject-mode.json').write_text('{}')
    ready = tmp_path / 'ready.txt'
    env = {**os.environ, 'LOCALAPPDATA': str(tmp_path)}
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'tools/maps-studio/studio.py'),
                              '--no-browser', '--ready-file', str(ready)],
                             env=env, capture_output=True, timeout=8)
    assert result.returncode != 0 and b'SSD eject is active' in result.stderr
    assert not ready.exists()
