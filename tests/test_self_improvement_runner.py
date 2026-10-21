"""Trusted runner contract tests; never run live application/models/devices."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('self_improvement_test_runner', ROOT / 'tools/self-improvement-test-runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def payload(tmp_path):
    baseline = tmp_path / 'baseline'
    baseline.mkdir()
    names = {'tests/conftest.py': '# fixture\n', 'tests/test_unit.py': 'def test_ok(): assert True\n',
        'tools/maps-studio/test_studio_backend.py': 'def test_ok(): assert True\n',
        'tools/maps-studio/test_studio_ui.cjs': 'require("assert").ok(true);\n',
        'tests/ui/test-app.cjs': 'require("assert").ok(true);\n',
        'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths=["tests"]\n',
        'src/prometheus_assistant/example.py': 'value = 1\n'}
    for name, text in names.items():
        path = baseline / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    rows = [{'path': name, 'sha256': runner.digest((baseline / name).read_bytes())} for name in names]
    candidate = tmp_path / 'candidate'
    shutil.copytree(baseline, candidate)
    (baseline / 'SHA256-MANIFEST.json').write_text(json.dumps(rows), encoding='utf-8')
    return baseline, candidate


def test_only_source_change_is_allowed_and_complete_oracles_are_selected(payload):
    baseline, candidate = payload
    (candidate / 'src/prometheus_assistant/example.py').write_text('value = 2\n')
    manifest, before, after, ui = runner.payloads(baseline, candidate)
    assert before != after
    assert ui == ['tests/ui/test-app.cjs']
    assert len(manifest) == 64


@pytest.mark.parametrize('mutation', ['test', 'config', 'tool', 'extra', 'missing', 'baseline'])
def test_missing_or_mutated_oracles_fail_before_execution(payload, mutation):
    baseline, candidate = payload
    if mutation in {'test', 'config', 'tool'}:
        name = {'test': 'tests/test_unit.py', 'config': 'pyproject.toml', 'tool': 'tools/maps-studio/test_studio_ui.cjs'}[mutation]
        (candidate / name).write_text('altered')
    elif mutation == 'extra':
        (candidate / 'extra.py').write_text('extra')
    elif mutation == 'missing':
        (candidate / 'tests/test_unit.py').unlink()
    else:
        (baseline / 'tests/test_unit.py').write_text('altered')
    with pytest.raises(ValueError):
        runner.payloads(baseline, candidate)


def test_offline_wheels_extract_and_real_pytest_import_uses_pinned_runtime(tmp_path):
    packages = tmp_path / 'python'
    bundle = ROOT / 'tools/self-improvement-tests'
    runner.extract_runtime(bundle, packages)
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);import pytest;print(pytest.__version__);print(pytest.__file__)', str(packages)],
        env=env, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    assert '9.1.1' in result.stdout and str(packages) in result.stdout


def test_tampered_runtime_pin_rejected_before_import(tmp_path):
    bundle = tmp_path / 'bundle'
    shutil.copytree(ROOT / 'tools/self-improvement-tests', bundle)
    (bundle / 'python-bootstrap.py').write_text('print("tampered")')
    with pytest.raises(ValueError, match='pin mismatch'):
        runner.extract_runtime(bundle, tmp_path / 'packages')


def test_multiprocessing_child_bootstrap_does_not_reenter_pytest(tmp_path):
    packages = tmp_path / 'python'
    bundle = ROOT / 'tools/self-improvement-tests'
    runner.extract_runtime(bundle, packages)
    source = ('import sys,runpy;script=sys.argv[1];sys.argv=[script,*sys.argv[2:],"missing-must-not-collect"];'
              'runpy.run_path(script,run_name="__mp_main__");print("child bootstrap only")')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source,
        str(bundle / 'python-bootstrap.py'), str(bundle), str(packages), str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'child bootstrap only'


def test_workspace_uses_short_os_host_path_not_deep_report_location(tmp_path, monkeypatch):
    monkeypatch.setenv('TEMP', str(tmp_path / ('deep-' * 35)))
    root = runner.host_temp_root(tmp_path / 'portable')
    assert root.name == 'PrometheusChecks'
    assert len(str(root)) <= 130
    if os.name == 'nt':
        assert not root.is_relative_to(tmp_path)


def test_python_counts_do_not_count_skips_as_executed(tmp_path):
    xml = tmp_path / 'results.xml'
    xml.write_text('<testsuites><testsuite><testcase/><testcase><skipped message="not available"/></testcase></testsuite></testsuites>')
    assert runner.python_counts(xml) == {'tests_run': 1, 'skipped': 1, 'skip_reasons': ['not available']}


def test_precancelled_command_does_not_start(tmp_path):
    cancel = tmp_path / 'cancel'
    cancel.touch()
    result = runner.run_command([sys.executable, '-B', '-c', 'raise Exception("must not run")'],
        tmp_path, os.environ.copy(), tmp_path / 'out.log', cancel, time.monotonic() + 10)
    assert result['executed'] is False and result['state'] == 'cancelled'


@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
def test_running_command_is_stopped_with_failure_receipt(tmp_path, stop):
    cancel = tmp_path / 'cancel'
    timer = threading.Timer(.4, cancel.touch) if stop == 'cancel' else None
    if timer:
        timer.start()
    try:
        result = runner.run_command([sys.executable, '-B', '-c', 'import time;time.sleep(30)'],
            tmp_path, os.environ.copy(), tmp_path / 'out.log', cancel,
            time.monotonic() + (10 if stop == 'cancel' else .4))
    finally:
        if timer:
            timer.join()
    assert result['executed'] and result['exit_code'] != 0
    assert result['state'] == ('cancelled' if stop == 'cancel' else 'timed_out')
    assert result['seconds'] < 10


def test_windows_job_kills_owned_descendant_after_parent_exits(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows Job Object behavior')
    marker = tmp_path / 'escaped.txt'
    child = 'import time,pathlib;time.sleep(2);pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")'
    parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-B","-c",' + repr(child) + ']);print("done")'
    result = runner.run_command([sys.executable, '-B', '-c', parent], tmp_path, os.environ.copy(),
        tmp_path / 'out.log', None, time.monotonic() + 10)
    assert result['exit_code'] == 0
    time.sleep(2.2)
    assert not marker.exists(), 'Owned child process must not survive the suite'


def test_guard_blocks_storage_drive_root_and_live_inference(tmp_path):
    bundle = ROOT / 'tools/self-improvement-tests'
    storage = tmp_path / 'portable'
    storage.mkdir()
    secret = storage / 'private.txt'
    secret.write_text('fixture data')
    env = os.environ.copy()
    env.update(PROMETHEUS_TEST_STORAGE_ROOT=str(storage), PROMETHEUS_TEST_SCRATCH=str(tmp_path),
               PROMETHEUS_TEST_GUARD_LOG=str(tmp_path / 'guard.jsonl'))
    source = ('import sys;sys.path.insert(0,sys.argv[1]);import sitecustomize;'
              'open(sys.argv[2]).read()')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source, str(bundle), str(secret)],
        env=env, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode != 0 and 'live portable storage access' in result.stderr
    assert (tmp_path / 'guard.jsonl').is_file()
