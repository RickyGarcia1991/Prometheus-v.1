import ctypes
import json
import os
import subprocess
import sys
import time

import pytest

from prometheus_assistant import project_job_metrics as metrics


class Api:
    def accounting(self, handle):
        assert handle == 42
        return dict(cpu_ticks=25_000_000, io_bytes=3*1024**2,
                    total_processes=2, active_processes=0)

    def peak_commit(self, handle):
        return 1024**3


def test_separate_units_scope_and_complete_accounting():
    result = metrics.capture_job(42, api=Api())
    assert result['peak_commit_gib'] == 1
    assert result['cpu_seconds'] == 2.5
    assert result['io_mib'] == 3
    assert result['snapshot_complete'] is True
    assert result['scope'] == 'job_associated_processes'
    assert 'ram_gib' not in result
    assert 'not resident' in result['memory_scope']
    assert 'non-disk' in result['io_scope']
    assert 'before assignment' in result['coverage']
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('failing', ['accounting', 'peak_commit'])
def test_partial_failure_retains_independent_measurements(failing):
    api = Api()
    def fail(_):
        raise OSError('unavailable')
    setattr(api, failing, fail)
    result = metrics.capture_job(42, api=api)
    if failing == 'accounting':
        assert result['cpu_seconds'] is None
        assert result['snapshot_complete'] is None
        assert result['peak_commit_gib'] == 1
    else:
        assert result['peak_commit_gib'] is None
        assert result['cpu_seconds'] == 2.5
        assert result['snapshot_complete'] is True


@pytest.mark.parametrize('active,total,complete', [(1,2,False), (3,2,None)])
def test_active_or_inconsistent_process_counts_never_complete(active,total,complete):
    class Counts(Api):
        def accounting(self, handle):
            return dict(super().accounting(handle), active_processes=active, total_processes=total)
    assert metrics.capture_job(42, api=Counts())['snapshot_complete'] is complete


@pytest.mark.parametrize('handle', [None, 0, -1, True, '42', ctypes.c_void_p()])
def test_missing_or_closed_handles_remain_unknown(handle):
    result = metrics.capture_job(handle, api=Api())
    assert result['peak_commit_gib'] is None
    assert result['snapshot_complete'] is None


def test_invalid_metric_does_not_erase_valid_fields():
    class Invalid(Api):
        def accounting(self, handle):
            return dict(super().accounting(handle), cpu_ticks=float('nan'))
    result = metrics.capture_job(42, api=Invalid())
    assert result['cpu_seconds'] is None
    assert result['snapshot_complete'] is None
    assert result['io_mib'] == 3


def test_native_query_classes_and_reserved_io_are_correct():
    queried = []
    api = object.__new__(metrics._WindowsApi)
    def query(handle, kind, pointer, size, returned):
        queried.append(kind)
        returned._obj.value = size
        data = pointer._obj
        if kind == 8:
            data.basic.user_time = 10_000_000
            data.basic.kernel_time = 20_000_000
            data.basic.total_processes = 2
            data.io.read_bytes = 1*1024**2
            data.io.write_bytes = 2*1024**2
            data.io.other_bytes = 3*1024**2
        elif kind == 9:
            data.peak_job_memory = 1024**3
            data.reserved_io.read_bytes = 999999999
        else:
            pytest.fail('Unexpected job information class')
        return 1
    api.query = query
    result = metrics.capture_job(42, api=api)
    assert queried == [8,9]
    assert result['cpu_seconds'] == 3
    assert result['io_mib'] == 6
    assert result['peak_commit_gib'] == 1


def test_short_native_structure_is_unavailable():
    api = object.__new__(metrics._WindowsApi)
    api.query = lambda *_: 1
    assert metrics.capture_job(42, api=api)['cpu_seconds'] is None


def test_non_windows_without_api_is_explicitly_unknown(monkeypatch):
    monkeypatch.setattr(metrics, '_IS_WINDOWS', False)
    assert metrics.capture_job(42)['snapshot_complete'] is None


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows job accounting')
@pytest.mark.parametrize('leave_child_active', [False, True])
def test_native_parent_and_child_job_snapshot(tmp_path, leave_child_active):
    from prometheus_assistant.coding_workspace import _WindowsJob
    marker = tmp_path / 'child-ready'
    child = (
        'import pathlib,time; data=bytearray(8*1024*1024); '
        f'pathlib.Path({str(marker)!r}).write_bytes(b"x"*65536); '
        'sum(range(100000)); '
        + ('time.sleep(20)' if leave_child_active else 'pass'))
    parent = '''import subprocess,sys,time,pathlib
sys.stdin.readline()
child=subprocess.Popen([sys.executable,'-B','-c',sys.argv[1]],stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
deadline=time.monotonic()+5
while not pathlib.Path(sys.argv[2]).exists():
    if time.monotonic()>deadline: raise RuntimeError('child did not start')
    time.sleep(.01)
if sys.argv[3]=='wait':
    assert child.wait(timeout=5)==0
print('fixture complete')
'''
    process = subprocess.Popen([sys.executable, '-B', '-c', parent, child,
        str(marker), 'active' if leave_child_active else 'wait'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    job = None
    try:
        # Pipe barrier prevents this fixture's child from preceding assignment.
        job = _WindowsJob(process)
        output, error = process.communicate(input=b'go\n', timeout=8)
        assert process.returncode == 0, error
        assert b'fixture complete' in output
        if not leave_child_active:
            # Windows may retain ActiveProcesses until the final process handle
            # closes, even after wait. This fixture no longer needs that handle.
            process._handle.Close()
        result = metrics.capture_job(job.handle)
        if not leave_child_active:
            # Accounting can settle after wait/handle release. Poll only in this
            # bounded fixture; production capture remains one read, no waits.
            deadline = time.monotonic() + 1
            while result['active_processes'] and time.monotonic() < deadline:
                time.sleep(.01)
                result = metrics.capture_job(job.handle)
        assert result['total_processes'] >= 2
        assert result['peak_commit_gib'] > 0
        assert result['cpu_seconds'] >= 0
        assert result['io_mib'] >= 0
        assert result['snapshot_complete'] is (not leave_child_active), result
        assert (result['active_processes'] > 0) is leave_child_active
    finally:
        if job is not None:
            job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
