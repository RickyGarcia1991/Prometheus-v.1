"""Studio lifecycle boundaries use only temporary host state and child processes."""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / 'tools/maps-studio/studio.py'
MARKERS = ('eject-mode.json', 'active-chat.shutdown')


def launch(tmp_path, *, environment=None):
    ready = tmp_path / 'ready.txt'
    env = {**os.environ, 'LOCALAPPDATA': str(tmp_path / 'host')}
    if environment is not None:
        env = environment
    child = subprocess.Popen(
        [sys.executable, '-I', '-S', '-B', str(STUDIO), '--no-browser', '--ready-file', str(ready)],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return child, ready


def stop(child):
    if child.poll() is None:
        child.terminate()
    child.communicate(timeout=5)


def wait_ready(child, ready):
    deadline = time.monotonic() + 8
    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(.025)
    assert ready.exists(), 'Generated Studio did not become ready'
    with urlopen(ready.read_text().strip() + '/api/bootstrap', timeout=2) as response:
        assert response.status == 200


@pytest.mark.parametrize('marker', MARKERS)
def test_startup_refuses_active_lifecycle_marker(tmp_path, marker):
    host = tmp_path / 'host/Prometheus'
    host.mkdir(parents=True)
    (host / marker).write_text('{}')
    child, ready = launch(tmp_path)
    try:
        _, errors = child.communicate(timeout=3)
        assert child.returncode != 0
        assert b'Studio startup blocked' in errors
        assert not ready.exists()
        assert (host / marker).read_text() == '{}'
    finally:
        stop(child)


@pytest.mark.parametrize('marker', MARKERS)
def test_running_studio_stops_for_lifecycle_marker(tmp_path, marker):
    host = tmp_path / 'host/Prometheus'
    host.mkdir(parents=True)
    child, ready = launch(tmp_path)
    try:
        wait_ready(child, ready)
        (host / marker).write_text('{}')
        _, errors = child.communicate(timeout=3)
        assert child.returncode == 0, errors.decode()
        assert (host / marker).read_text() == '{}'
    finally:
        stop(child)


def test_unrelated_paths_and_marker_names_do_not_stop_studio(tmp_path):
    host = tmp_path / 'host/Prometheus'
    host.mkdir(parents=True)
    (host / 'last-eject.json').write_text('{}')
    (tmp_path / 'host/eject-mode.json').write_text('{}')
    (tmp_path / 'eject-mode.json').write_text('{}')
    child, ready = launch(tmp_path)
    try:
        wait_ready(child, ready)
        time.sleep(.65)
        assert child.poll() is None
        with urlopen(ready.read_text().strip() + '/api/bootstrap', timeout=2) as response:
            assert response.status == 200
    finally:
        stop(child)


def test_no_localappdata_does_not_treat_working_directory_as_host(tmp_path):
    (tmp_path / 'Prometheus').mkdir()
    (tmp_path / 'Prometheus/eject-mode.json').write_text('{}')
    env = {key: value for key, value in os.environ.items() if key.upper() != 'LOCALAPPDATA'}
    child, ready = launch(tmp_path, environment=env)
    try:
        wait_ready(child, ready)
        time.sleep(.65)
        assert child.poll() is None
    finally:
        stop(child)


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('pwsh'), reason='Windows PowerShell 7 launcher')
@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('flags', [[], ['-CheckOnly']])
def test_launcher_refuses_marker_before_runtime_or_process_start(tmp_path, marker, flags):
    host = tmp_path / 'host/Prometheus'
    host.mkdir(parents=True)
    (host / marker).write_text('{}')
    launcher = tmp_path / 'Start-Studio.ps1'
    launcher.write_bytes((ROOT / 'tools/maps-studio/Start-Studio.ps1').read_bytes())
    ps = Path(shutil.which('pwsh'))
    env = {**os.environ, 'LOCALAPPDATA': str(tmp_path / 'host'), 'PSModulePath': str(ps.parent / 'Modules')}
    result = subprocess.run([str(ps), '-NoProfile', '-File', str(launcher), *flags],
                            env=env, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert b'Studio startup blocked' in result.stderr
    assert not (tmp_path / 'startup-error.txt').exists()
    assert not list(tmp_path.glob('.ready-*'))
    assert (host / marker).read_text() == '{}'
