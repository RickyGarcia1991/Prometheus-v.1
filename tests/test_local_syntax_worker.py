import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from prometheus_assistant import local_syntax_worker as local
from prometheus_assistant.remote_job_protocol import normalize_request, validate_receipt


def request(files=None):
    target = local.local_target()
    return dict(schema_version=1, job_id='a'*32, device_id=target['device_id'],
        worker_digest=target['worker_digest'], operation='python-syntax-check',
        timeout_seconds=5, files=files or [dict(path='main.py', content='answer = 42\n')])


class Probe:
    def __init__(self, **changed):
        self.value = dict(available_ram_gib=8, free_disk_gib=10, cpu_percent=20,
            battery_percent=100, plugged_in=True,
            observed_at=datetime.fromtimestamp(100, timezone.utc).isoformat())
        self.value.update(changed)

    def snapshot(self, *, force=False):
        assert force is True
        return dict(self.value)


def test_target_is_stable_and_bound_to_actual_compiler(monkeypatch):
    before = local.local_target()
    assert local.local_target() == before
    assert before['account_id'] == 'local'
    assert re.fullmatch(r'local-[0-9a-f]{32}', before['device_id'])
    assert re.fullmatch(r'[0-9a-f]{64}', before['worker_digest'])
    assert re.fullmatch(r'[0-9a-f]{64}', before['configuration_digest'])
    monkeypatch.setattr(local, '_WINDOWS_CHILD_CODE', local._WINDOWS_CHILD_CODE + '\n# changed\n')
    if sys.platform == 'win32':
        changed = local.local_target()
        assert changed['worker_digest'] != before['worker_digest']
        assert changed['configuration_digest'] != before['configuration_digest']
        assert changed['device_id'] == before['device_id']


def test_trusted_admission_keeps_original_sample_time_and_detached_values():
    probe = Probe()
    got = local.local_admission(request(), probe=probe, clock=lambda: 101)
    assert got['authenticated'] is True and got['decision'] == 'ready'
    assert got['observed_at'] == 100 and got['expires_at'] == 130
    probe.value['available_ram_gib'] = 0
    assert got['observations']['available_ram_gib'] == 8


@pytest.mark.parametrize('changed', [
    {'available_ram_gib': 2.249}, {'available_ram_gib': None},
    {'available_ram_gib': float('nan')}, {'available_ram_gib': True},
    {'free_disk_gib': .499}, {'free_disk_gib': float('inf')},
    {'cpu_percent': 70.01}, {'cpu_percent': -1}, {'cpu_percent': None},
    {'plugged_in': False}, {'plugged_in': 1},
    {'observed_at': '1970-01-01T00:01:00+00:00'},
    {'observed_at': '1970-01-01T00:01:42+00:00'},
    {'observed_at': '1970-01-01T00:01:40'}, {'observed_at': 'invalid'}])
def test_untrusted_unknown_stale_or_pressured_resources_defer(changed):
    got = local.local_admission(probe=Probe(**changed), clock=lambda: 101)
    assert got['decision'] == 'defer' and got['reasons']


def test_admission_reserve_boundary_and_expiry():
    assert local.local_admission(probe=Probe(available_ram_gib=2.25,
        free_disk_gib=.5, cpu_percent=70), clock=lambda: 129.99)['decision'] == 'ready'
    assert local.local_admission(probe=Probe(), clock=lambda: 130)['decision'] == 'defer'


def test_unavailable_probe_and_unsupported_platform_defer(monkeypatch):
    probe = SimpleNamespace(snapshot=lambda **_: (_ for _ in ()).throw(OSError('private path')))
    got = local.local_admission(probe=probe, clock=lambda: 101)
    assert got['decision'] == 'defer' and 'private path' not in str(got)
    monkeypatch.setattr(local.sys, 'platform', 'unsupported')
    assert local.local_admission(probe=Probe(), clock=lambda: 101)['decision'] == 'defer'
    with pytest.raises(RuntimeError, match='supported'):
        local.execute_local_syntax(request())


def test_changed_target_never_runs(monkeypatch):
    raw = request()
    raw['device_id'] = 'another-computer'
    monkeypatch.setattr(local.subprocess, 'run', lambda *a, **k: pytest.fail('Started a changed request'))
    with pytest.raises(ValueError, match='local worker'):
        local.execute_local_syntax(raw)
    with pytest.raises(ValueError, match='local worker'):
        local.local_admission(raw, probe=Probe(), clock=lambda: 101)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows limits qualification.')
def test_native_compile_valid_invalid_without_executing_or_importing(tmp_path):
    marker = tmp_path / 'must-not-exist'
    raw = request([dict(path='main.py', content=f'open({str(marker)!r}, "w").write("bad")'),
        dict(path='broken.py', content='if broken\n'),
        dict(path='loop.py', content='while True: pass'),
        dict(path='import.py', content='import nonexistent_submitted_module')])
    got = local.execute_local_syntax(raw)
    assert not marker.exists()
    assert got['state'] == 'completed'
    assert [row['passed'] for row in got['checks']] == [True, False, True, True]
    assert validate_receipt(raw, got)['status'] == 'failed'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows limits qualification.')
@pytest.mark.parametrize('call', ['kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())',
    'kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))'])
def test_native_limits_failure_exits_before_reading_source(monkeypatch, call):
    code = local._WINDOWS_CHILD_CODE.replace(call, 'False')
    assert code != local._WINDOWS_CHILD_CODE
    monkeypatch.setattr(local, '_WINDOWS_CHILD_CODE', code)
    raw = request()
    got = local.execute_local_syntax(raw)
    assert got['state'] == 'failed'
    assert validate_receipt(raw, got)['status'] == 'failed'


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows limits qualification.')
def test_native_child_enforces_memory_limit(monkeypatch):
    code = local._WINDOWS_CHILD_CODE.replace('payload = sys.stdin.buffer.read(3100001)',
        'allocation = bytearray(384 * 1024 * 1024)\npayload = sys.stdin.buffer.read(3100001)')
    assert code != local._WINDOWS_CHILD_CODE
    monkeypatch.setattr(local, '_WINDOWS_CHILD_CODE', code)
    got = local.execute_local_syntax(request())
    assert got['state'] == 'failed'


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows limits qualification.')
def test_native_cpu_limit_terminates_busy_compiler_before_wall_deadline(monkeypatch):
    code = local._WINDOWS_CHILD_CODE.replace('payload = sys.stdin.buffer.read(3100001)',
        'while True: pass\npayload = sys.stdin.buffer.read(3100001)')
    monkeypatch.setattr(local, '_WINDOWS_CHILD_CODE', code)
    original, exits = subprocess.run, []
    def child(*args, **kwargs):
        result = original(*args, **kwargs)
        exits.append(result.returncode)
        return result
    monkeypatch.setattr(local.subprocess, 'run', child)
    raw = request()
    raw['timeout_seconds'] = 20
    got = local.execute_local_syntax(raw)
    # A wall timeout raises instead of producing a return code. A native job
    # CPU termination therefore has an independently observable child exit.
    assert exits and exits[0] != 0 and got['state'] == 'failed'


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows compiler qualification.')
def test_native_large_utf8_source_and_long_portable_path():
    raw = request([dict(path='a'*237+'.py', content='value = "' + '\u00e9'*120000 + '"')])
    got = local.execute_local_syntax(raw)
    assert validate_receipt(raw, got)['status'] == 'passed'
    assert len(json.dumps(got).encode()) < 8192


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows limits qualification.')
def test_native_wall_deadline_kills_and_waits_for_sole_child(monkeypatch, tmp_path):
    import ctypes
    from pathlib import Path
    pid_file = tmp_path / 'compiler.pid'
    launched = []
    real_popen = subprocess.Popen
    def observe_launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append((process.pid, Path(kwargs['cwd'])))
        return process
    monkeypatch.setattr(local.subprocess, 'Popen', observe_launch)
    code = local._WINDOWS_CHILD_CODE.replace('payload = sys.stdin.buffer.read(3100001)',
        'import os,time\nfrom pathlib import Path\n'
        f'Path({str(pid_file)!r}).write_text(str(os.getpid()))\n'
        'time.sleep(10)\npayload = sys.stdin.buffer.read(3100001)')
    monkeypatch.setattr(local, '_WINDOWS_CHILD_CODE', code)
    raw = request()
    raw['timeout_seconds'] = 1
    started = time.monotonic()
    got = local.execute_local_syntax(raw)
    assert time.monotonic() - started < 5
    assert got['state'] == 'failed'
    assert len(launched) == 1
    actual_pid = int(pid_file.read_text())
    # A venv redirector has a different PID and can return before the actual
    # compiler exits. The timeout must own and wait for the compiler itself.
    assert actual_pid == launched[0][0]
    assert not launched[0][1].exists()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, actual_pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows subprocess contract.')
@pytest.mark.parametrize('output', [b'x'*8193, b'{}', b'invalid'], ids=['oversized', 'empty', 'malformed'])
def test_excessive_or_invalid_output_never_accepted(monkeypatch, output):
    def child(*args, **kwargs):
        kwargs['stdout'].write(output)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local.subprocess, 'run', child)
    with pytest.raises(ValueError):
        local.execute_local_syntax(request())


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows subprocess contract.')
def test_subprocess_is_fixed_isolated_no_shell_and_timeout_bound(monkeypatch):
    def child(argv, **kwargs):
        from pathlib import Path
        expected = str(Path(getattr(sys, '_base_executable', None) or sys.executable).resolve())
        assert argv == [expected, '-I', '-S', '-B', '-c', local._WINDOWS_CHILD_CODE]
        assert kwargs.get('shell', False) is False
        assert kwargs['timeout'] == 5 and kwargs['close_fds'] is True
        assert 'PYTHONPATH' not in kwargs['env']
        req = json.loads(kwargs['input'])
        assert req == normalize_request(raw)
        raise subprocess.TimeoutExpired(argv, 5)
    raw = request()
    monkeypatch.setattr(local.subprocess, 'run', child)
    assert local.execute_local_syntax(raw)['state'] == 'failed'
