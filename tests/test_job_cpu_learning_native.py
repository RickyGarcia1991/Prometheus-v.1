"""Real Windows accounting through the separate workload-history seam."""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from prometheus_assistant.project_resources import ProjectResources


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object accounting')
def test_native_child_cpu_survives_history_restart_without_changing_ram(tmp_path):
    from prometheus_assistant.coding_workspace import _WindowsJob
    from prometheus_assistant.project_job_metrics import capture_job

    parent = """import subprocess,sys
sys.stdin.readline()
p=subprocess.run([sys.executable,'-B','-c','sum(i*i for i in range(300000))'],
    creationflags=subprocess.CREATE_NO_WINDOW,timeout=5)
raise SystemExit(p.returncode)
"""
    child = subprocess.Popen([sys.executable, '-B', '-c', parent], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    job = None
    try:
        job = _WindowsJob(child)
        _, error = child.communicate(b'go\n', timeout=8)
        assert child.returncode == 0, error
        child._handle.Close()
        measured = capture_job(job.handle)
        deadline = time.monotonic() + 1
        while measured['active_processes'] and time.monotonic() < deadline:
            time.sleep(.01)
            measured = capture_job(job.handle)
        assert measured['snapshot_complete'] is True
        assert measured['total_processes'] >= 2
        assert measured['cpu_seconds'] > 0
    finally:
        if job is not None:
            job.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

    probe = SimpleNamespace(snapshot=lambda **_: dict(available_ram_gib=8,
        free_disk_gib=20, cpu_percent=10, battery_percent=100, plugged_in=True))
    candidate = {'project': str(tmp_path), 'content': '',
        'checks': [{'preset': 'python-unittest', 'target': 'tests'}]}
    # RAM deliberately unavailable: complete native job CPU must be independently useful.
    result = {'status': 'passed', 'checks': [{**candidate['checks'][0], 'passed': True,
        'exit_code': 0, 'cancelled': False, 'timed_out': False,
        'resource_measurements': {'scope': 'direct_process', 'job_metrics': measured}}]}
    engine = ProjectResources(tmp_path/'history', probe=probe)
    before = engine.review(candidate)
    observed = engine.observe(candidate, result)
    assert observed['recorded'] is False
    assert observed['job_cpu_observation']['recorded'] is True
    after = ProjectResources(tmp_path/'history', probe=probe).review(candidate)
    assert after['estimated_cost'] == before['estimated_cost']
    assert after['decision'] == before['decision']
    assert after['job_cpu_prediction']['samples'] == 1
    assert after['job_cpu_prediction']['cpu_seconds'] == pytest.approx(measured['cpu_seconds'] * 1.5)
    assert after['job_cpu_prediction']['used_for_admission'] is False
