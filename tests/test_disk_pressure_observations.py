import copy
from concurrent.futures import ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from prometheus_assistant import disk_pressure_observations as disk


D, V, M, I = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def packet(step=0):
    # Every step is ten seconds: 100 operations, 0.4 seconds cumulative latency,
    # four seconds idle, hence 4 ms/transfer, 60% busy, 10 transfers/second.
    counts = {'latency': (50000000 + step * 4000000, 20 + step * 100),
              'idle': (100000000 + step * 40000000, 200000000 + step * 100000000),
              'queue': (3 + step, 0),
              'transfers': (20 + step * 100, 200000000 + step * 100000000)}
    counters = {}
    types = {'latency': 805438464, 'idle': 542573824, 'queue': 65536, 'transfers': 272696320}
    for name, (first, second) in counts.items():
        counters[name] = dict(status='raw', reason_code=None, instance_id=I, cstatus=0,
            timestamp_100ns=1000000000 + step * 100000000, first=first, second=second,
            multi_count=0, counter_type=types[name], time_base=None if name == 'queue' else 10000000,
            scale=0, default_scale=0)
    return dict(schema_version=1, status='raw', reason_code=None, source='windows_pdh_raw', scope='device',
        volumes=[dict(role='data', status='mapped', reason_code=None, volume_id=V, device_id=D, mapping_id=M)],
        devices=[dict(device_id=D, identity_basis='storage_device_number_ex_hardware_guid',
                      status='raw', counters=counters)])


def encode(value):
    return json.dumps(value, separators=(',', ':')).encode()


def unavailable(reason='metadata_unavailable'):
    return dict(schema_version=1, status='unavailable', reason_code=reason, source='windows_pdh_raw',
                scope='device', volumes=[], devices=[])


def omit(value, metric, reason='counter_unavailable'):
    value['devices'][0]['counters'][metric] = dict(status='unavailable', reason_code=reason)
    value['devices'][0]['status'] = value['status'] = 'partial'
    value['reason_code'] = 'observations_incomplete'
    return value


class Samples:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def __call__(self, roots):
        self.calls.append(copy.deepcopy(roots))
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, bytes) else encode(value)


def probe(values):
    now = [0.0]
    runner = Samples(values)
    instance = disk.DiskPressureProbe({'data': 'E:/data'}, clock=lambda: now[0],
                                     runner=runner, wall_clock=lambda: NOW)
    return instance, now, runner


def test_first_sample_only_queue_then_independent_formula_values():
    instance, now, runner = probe([packet(), packet(1)])
    first = instance.snapshot()
    assert first['status'] == 'warming_up' and not first['stale']
    assert first['scope'] == 'whole_device' and first['observed_at'] == NOW.isoformat()
    assert first['devices'][0]['queue_length'] == 3
    assert first['devices'][0]['interval_seconds'] is None
    assert first['devices'][0]['reasons'] == dict(latency_ms='warming_up', busy_percent='warming_up',
                                                  transfers_per_second='warming_up')
    now[0] = 10
    result = instance.snapshot()
    device = result['devices'][0]
    assert result['status'] == 'observed' and result['reason_code'] is None
    assert device['latency_ms'] == pytest.approx(4)
    assert device['busy_percent'] == pytest.approx(60)
    assert device['transfers_per_second'] == pytest.approx(10)
    assert device['queue_length'] == 4 and device['interval_seconds'] == 10
    assert device['reasons'] == {} and len(runner.calls) == 2


def test_cache_age_copy_isolation_and_no_early_or_forced_sampling():
    instance, now, runner = probe([packet(), packet(1)])
    first = instance.snapshot()
    first['devices'][0]['queue_length'] = 999
    first['volumes'][0]['role'] = 'forged'
    now[0] = 9.999
    cached = instance.snapshot()
    assert len(runner.calls) == 1 and cached['age_seconds'] == 9.999
    assert cached['devices'][0]['queue_length'] == 3 and cached['volumes'][0]['role'] == 'data'
    now[0] = 10
    assert instance.snapshot()['status'] == 'observed'
    assert len(runner.calls) == 2


def test_concurrent_readers_share_one_cold_helper_invocation():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def runner(roots):
        calls.append(roots)
        entered.set()
        assert release.wait(2)
        return encode(packet())
    instance = disk.DiskPressureProbe({'data': 'E:/data'}, clock=lambda: 0, runner=runner)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(instance.snapshot) for _ in range(8)]
        assert entered.wait(2)
        release.set()
        results = [future.result() for future in futures]
    assert len(calls) == 1 and all(value['status'] == 'warming_up' for value in results)
    results[0]['devices'].clear()
    assert results[1]['devices']


@pytest.mark.parametrize('error', [disk.DiskObservationError('helper_timeout'),
    disk.DiskObservationError('secret E:/user-name'), RuntimeError('secret output')])
def test_failure_clears_old_readings_and_backoff_then_warms_again(error):
    instance, now, runner = probe([packet(), packet(1), error, packet(8)])
    instance.snapshot()
    now[0] = 10
    assert instance.snapshot()['status'] == 'observed'
    now[0] = 20
    result = instance.snapshot()
    assert result['status'] == 'unavailable' and result['stale']
    assert result['devices'] == [] and result['observed_at'] is None
    assert 'secret' not in json.dumps(result)
    now[0] = 79.999
    assert instance.snapshot()['devices'] == [] and len(runner.calls) == 3
    now[0] = 80
    assert instance.snapshot()['status'] == 'warming_up' and len(runner.calls) == 4


def test_provider_unavailable_backoff_and_empty_device_list():
    instance, now, runner = probe([unavailable(), packet(6)])
    result = instance.snapshot()
    assert result['status'] == 'unavailable' and result['stale'] and result['devices'] == []
    assert result['reason_code'] == 'metadata_unavailable'
    now[0] = 59
    instance.snapshot()
    assert len(runner.calls) == 1
    now[0] = 60
    assert instance.snapshot()['status'] == 'warming_up'


def test_zero_completed_transfers_is_unknown_latency_not_zero_latency():
    second = packet(1)
    for key in ('latency', 'transfers'):
        second['devices'][0]['counters'][key]['first'] = packet()['devices'][0]['counters'][key]['first']
    second['devices'][0]['counters']['latency']['second'] = 20
    instance, now, _ = probe([packet(), second])
    instance.snapshot()
    now[0] = 10
    device = instance.snapshot()['devices'][0]
    assert device['transfers_per_second'] == 0 and device['latency_ms'] is None
    assert device['reasons']['latency_ms'] == 'no_transfers' and device['status'] == 'partial'


def test_latency_requires_matching_valid_transfer_evidence():
    for second, reason in ((omit(packet(1), 'transfers'), 'transfers_unavailable'), (packet(1), 'transfer_count_mismatch')):
        if reason == 'transfer_count_mismatch':
            second['devices'][0]['counters']['latency']['second'] = 121
        instance, now, _ = probe([packet(), second])
        instance.snapshot()
        now[0] = 10
        device = instance.snapshot()['devices'][0]
        assert device['latency_ms'] is None and device['reasons']['latency_ms'] == reason


@pytest.mark.parametrize('field', ['mapping_id', 'volume_id', 'device_id'])
def test_identity_changes_reset_interval_even_when_counter_numbers_increase(field):
    second = packet(1)
    second['volumes'][0][field] = 'e' * 64
    if field == 'device_id':
        second['devices'][0][field] = 'e' * 64
    instance, now, _ = probe([packet(), second])
    instance.snapshot()
    now[0] = 10
    device = instance.snapshot()['devices'][0]
    assert device['latency_ms'] is None and device['busy_percent'] is None
    assert device['queue_length'] == 4 and device['interval_seconds'] is None


@pytest.mark.parametrize('field,value', [('instance_id', 'e' * 64), ('time_base', 5000000),
    ('scale', 1), ('default_scale', 1), ('multi_count', 1)])
def test_counter_metadata_changes_reset_affected_metric(field, value):
    second = packet(1)
    if field == 'instance_id':
        for counter in second['devices'][0]['counters'].values():
            counter[field] = value
    else:
        second['devices'][0]['counters']['latency'][field] = value
    instance, now, _ = probe([packet(), second])
    instance.snapshot()
    now[0] = 10
    device = instance.snapshot()['devices'][0]
    assert device['latency_ms'] is None and device['reasons']['latency_ms'] == 'counter_changed'


@pytest.mark.parametrize('field,value', [('first', 0), ('second', 0), ('timestamp_100ns', 999999999)])
def test_counter_rollbacks_are_unknown_then_recover_from_new_baseline(field, value):
    second = packet(1)
    second['devices'][0]['counters']['idle'][field] = value
    third = copy.deepcopy(second)
    counter = third['devices'][0]['counters']['idle']
    counter['first'] += 40000000
    counter['second'] += 100000000
    counter['timestamp_100ns'] += 100000000
    instance, now, _ = probe([packet(), second, third])
    instance.snapshot()
    now[0] = 10
    assert instance.snapshot()['devices'][0]['reasons']['busy_percent'] == 'counter_reset'
    now[0] = 20
    assert instance.snapshot()['devices'][0]['busy_percent'] == pytest.approx(60)


def test_sleep_gap_resets_and_next_ordinary_interval_recovers():
    instance, now, _ = probe([packet(), packet(7), packet(8)])
    instance.snapshot()
    now[0] = 70
    result = instance.snapshot()['devices'][0]
    assert result['latency_ms'] is None and result['reasons']['latency_ms'] == 'sample_gap'
    now[0] = 80
    assert instance.snapshot()['status'] == 'observed'


def test_sixty_second_interval_allowed_and_native_timestamp_jump_rejected():
    instance, now, _ = probe([packet(), packet(6)])
    instance.snapshot()
    now[0] = 60
    assert instance.snapshot()['status'] == 'observed'
    second = packet(1)
    second['devices'][0]['counters']['idle']['timestamp_100ns'] += 1000000000
    instance, now, _ = probe([packet(), second])
    instance.snapshot()
    now[0] = 10
    assert instance.snapshot()['devices'][0]['reasons']['busy_percent'] == 'sample_time_changed'


def test_clock_rollback_drops_old_baseline():
    instance, now, _ = probe([packet(), packet(1), packet(2)])
    instance.snapshot()
    now[0] = 10
    instance.snapshot()
    now[0] = 1
    result = instance.snapshot()
    assert result['status'] == 'warming_up' and result['devices'][0]['latency_ms'] is None


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -1, True])
def test_invalid_clock_clears_cache_and_requires_fresh_baseline(invalid):
    instance, now, runner = probe([packet(), packet(1)])
    instance.snapshot()
    now[0] = invalid
    assert instance.snapshot() == disk._unavailable('clock_unavailable')
    now[0] = 1
    assert instance.snapshot()['status'] == 'warming_up' and len(runner.calls) == 2


def test_failure_backoff_starts_after_helper_finishes():
    now, calls = [0.0], []
    def runner(roots):
        calls.append(roots)
        now[0] += 2
        raise disk.DiskObservationError('helper_timeout')
    instance = disk.DiskPressureProbe({'data': 'E:/data'}, clock=lambda: now[0], runner=runner)
    instance.snapshot()
    now[0] = 61.999
    instance.snapshot()
    assert len(calls) == 1
    now[0] = 62
    instance.snapshot()
    assert len(calls) == 2


def test_idle_timebase_gap_not_allowed_to_report_plausible_but_stale_percentage():
    second = packet(1)
    second['devices'][0]['counters']['idle']['second'] += 1000000000
    instance, now, _ = probe([packet(), second])
    instance.snapshot()
    now[0] = 10
    device = instance.snapshot()['devices'][0]
    assert device['busy_percent'] is None and device['reasons']['busy_percent'] == 'counter_data_invalid'


@pytest.mark.parametrize('first,second', [(300000001, 300000000), (140000000, 200000000)])
def test_invalid_idle_fraction_or_zero_time_is_not_clamped_to_healthy(first, second):
    next_packet = packet(1)
    next_packet['devices'][0]['counters']['idle'].update(first=first, second=second)
    instance, now, _ = probe([packet(), next_packet])
    instance.snapshot()
    now[0] = 10
    device = instance.snapshot()['devices'][0]
    assert device['busy_percent'] is None and device['reasons']['busy_percent'] == 'counter_data_invalid'


def test_missing_sample_breaks_only_affected_metric_interval():
    instance, now, _ = probe([packet(), omit(packet(1), 'idle'), packet(2), packet(3)])
    instance.snapshot()
    now[0] = 10
    assert instance.snapshot()['devices'][0]['reasons']['busy_percent'] == 'counter_unavailable'
    now[0] = 20
    device = instance.snapshot()['devices'][0]
    assert device['busy_percent'] is None and device['reasons']['busy_percent'] == 'warming_up'
    assert device['latency_ms'] == pytest.approx(4)
    now[0] = 30
    assert instance.snapshot()['status'] == 'observed'


@pytest.mark.parametrize('timestamp', [1000000000, 999999999])
def test_repeated_or_rollback_queue_timestamp_is_not_fresh_instantaneous_data(timestamp):
    second = packet(1)
    second['devices'][0]['counters']['queue']['timestamp_100ns'] = timestamp
    instance, now, _ = probe([packet(), second, packet(2)])
    instance.snapshot()
    now[0] = 10
    result = instance.snapshot()['devices'][0]
    assert result['queue_length'] is None and result['reasons']['queue_length'] == 'counter_reset'
    assert result['latency_ms'] == pytest.approx(4)
    now[0] = 20
    assert instance.snapshot()['devices'][0]['queue_length'] == 5


def test_repeated_entire_packet_is_unavailable_and_old_queue_not_republished():
    instance, now, _ = probe([packet(), packet()])
    instance.snapshot()
    now[0] = 10
    result = instance.snapshot()
    assert result['status'] == 'unavailable' and result['stale']
    assert result['devices'][0]['queue_length'] is None
    assert all(reason == 'counter_reset' for reason in result['devices'][0]['reasons'].values())


@pytest.mark.parametrize('field', ['mapping_id', 'instance_id'])
def test_new_mapping_or_instance_can_have_its_first_queue_timestamp(field):
    second = packet()
    if field == 'mapping_id':
        second['volumes'][0][field] = 'e' * 64
    else:
        for counter in second['devices'][0]['counters'].values():
            counter[field] = 'e' * 64
    instance, now, _ = probe([packet(), second])
    instance.snapshot()
    now[0] = 10
    assert instance.snapshot()['devices'][0]['queue_length'] == 3


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(schema_version=True), lambda p: p.update(scope='job'),
    lambda p: p.update(source='model'), lambda p: p.update(secret='E:/secret'),
    lambda p: p.update(reason_code='E:/secret'), lambda p: p['volumes'][0].update(role='other'),
    lambda p: p['volumes'].append(copy.deepcopy(p['volumes'][0])),
    lambda p: p['devices'].append(copy.deepcopy(p['devices'][0])),
    lambda p: p['volumes'][0].update(device_id='e' * 64),
    lambda p: p['devices'][0].update(status='unavailable'),
    lambda p: p['devices'][0].update(identity_basis='model_claim'),
    lambda p: p['devices'][0]['counters']['idle'].update(instance_id='e' * 64),
    lambda p: p['devices'][0]['counters']['queue'].update(time_base=10000000),
    lambda p: p['devices'][0]['counters']['idle'].update(first=-1),
    lambda p: p['devices'][0]['counters']['idle'].update(first=True),
    lambda p: p['devices'][0]['counters']['idle'].update(first=2**64),
    lambda p: p['devices'][0]['counters']['idle'].update(first=1.5),
    lambda p: p['devices'][0]['counters']['idle'].update(cstatus=123),
    lambda p: p['devices'][0]['counters']['idle'].update(time_base=0),
    lambda p: p['devices'][0]['counters']['idle'].update(counter_type=123),
])
def test_malformed_evidence_cannot_escape_into_display(mutate):
    value = packet()
    mutate(value)
    instance, _, _ = probe([value])
    result = instance.snapshot()
    assert result == disk._unavailable('helper_output_invalid')
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('payload', [b'no json', b'\xff', b'[]', b'{}', b' ' * 32769,
    b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'[' * 2000 + b']' * 2000],
    ids=['text', 'encoding', 'array', 'empty', 'oversize', 'duplicate', 'nan', 'deep'])
def test_malformed_duplicate_deep_and_oversize_output_is_unavailable(payload):
    instance, _, _ = probe([payload])
    assert instance.snapshot() == disk._unavailable('helper_output_invalid')


def test_config_and_runner_inputs_are_isolated():
    roots = {'data': 'E:/data'}
    calls = []
    def runner(value):
        calls.append(dict(value))
        value['data'] = 'E:/forged'
        return encode(packet(len(calls) - 1))
    now = [0]
    instance = disk.DiskPressureProbe(roots, clock=lambda: now[0], runner=runner)
    roots['data'] = 'C:/changed'
    instance.snapshot()
    now[0] = 10
    instance.snapshot()
    assert calls == [{'data': 'E:\\data'}] * 2


def test_public_runner_fixed_command_and_scrubs_python_environment(monkeypatch):
    monkeypatch.setattr(disk.os, 'name', 'nt')
    monkeypatch.setenv('PYTHONPATH', 'E:/untrusted')
    monkeypatch.setenv('PYTHONINSPECT', '1')
    monkeypatch.setenv('PYTHONSTARTUP', 'E:/untrusted.py')
    calls = []
    def run(command, payload, **kwargs):
        calls.append((command, payload, kwargs))
        return b'{}'
    monkeypatch.setattr(disk, '_run_bounded', run)
    disk.run_disk_helper({'data': 'E:/data'})
    command, payload, kwargs = calls[0]
    expected = str(Path(getattr(sys, '_base_executable', None) or sys.executable).resolve())
    assert command == [expected, '-B', '-m', 'prometheus_assistant.windows_disk_observations']
    assert json.loads(payload)['roots'] == {'data': 'E:\\data'}
    assert kwargs['env']['PYTHONPATH'] == str(Path(disk.__file__).resolve().parent.parent)
    assert kwargs['env']['PYTHONNOUSERSITE'] == '1' and kwargs['env']['PYTHONSAFEPATH'] == '1'
    assert 'PYTHONINSPECT' not in kwargs['env'] and 'PYTHONSTARTUP' not in kwargs['env']


def transport(code, payload=b'{}'):
    return disk._run_bounded([disk._helper_executable(), '-B', '-c', code], payload,
                             cwd=str(Path(__file__).parent), env=os.environ.copy())


def test_real_child_bounded_pipe_success_and_no_drain_threads(monkeypatch):
    before = {thread.ident for thread in threading.enumerate()}
    assert transport('import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)', b'x' * 8192) == b'x' * 8192
    assert {thread.ident for thread in threading.enumerate()} == before


@pytest.mark.parametrize('stream,limit', [('stdout', 32768), ('stderr', 4096)])
def test_real_child_output_flood_stopped_at_limit(stream, limit):
    started = time.monotonic()
    with pytest.raises(disk.DiskObservationError, match='helper_output_limit'):
        transport(f'import sys; sys.stdin.buffer.read(); sys.{stream}.buffer.write(b"x" * {limit + 1}); sys.{stream}.flush()')
    assert time.monotonic() - started < 2.5


def test_real_child_timeout_kills_and_reaps_owned_process(monkeypatch):
    created = []
    original = subprocess.Popen
    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        created.append(child)
        return child
    monkeypatch.setattr(disk.subprocess, 'Popen', popen)
    monkeypatch.setattr(disk, 'HELPER_TIMEOUT_SECONDS', 0.15)
    started = time.monotonic()
    with pytest.raises(disk.DiskObservationError, match='helper_timeout'):
        transport('import time; time.sleep(30)', b'x' * 8192)
    assert time.monotonic() - started < 1
    assert created[0].poll() is not None
    assert all(stream.closed for stream in (created[0].stdin, created[0].stdout, created[0].stderr))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows venv redirector and native process completion.')
def test_timeout_owns_actual_interpreter_pid_and_native_process_has_exited(monkeypatch, tmp_path):
    marker = tmp_path / 'actual-helper-pid.txt'
    created = []
    original = subprocess.Popen
    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        created.append(child)
        return child
    monkeypatch.setattr(disk.subprocess, 'Popen', popen)
    monkeypatch.setattr(disk, 'HELPER_TIMEOUT_SECONDS', 0.6)
    command = [disk._helper_executable(), '-B', '-c',
        'import os,sys,time,pathlib; pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding="ascii"); time.sleep(30)',
        str(marker)]
    actual_pid = None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    try:
        with pytest.raises(disk.DiskObservationError, match='^helper_timeout$'):
            disk._run_bounded(command, b'{}', cwd=str(tmp_path), env=os.environ.copy())
        actual_pid = int(marker.read_text(encoding='ascii'))
        assert actual_pid == created[0].pid, 'The launcher PID must be the actual Python process.'
        handle = kernel.OpenProcess(0x100000, False, actual_pid)  # SYNCHRONIZE only
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 0) == 0
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # PID no longer exists
    finally:
        # Keep the generated fixture bounded even if this regression catches a
        # future launcher mismatch. Never target any PID except its own marker.
        if actual_pid is None and marker.exists():
            actual_pid = int(marker.read_text(encoding='ascii'))
        if actual_pid is not None:
            handle = kernel.OpenProcess(0x100001, False, actual_pid)
            if handle:
                try:
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        kernel.TerminateProcess(handle, 1)
                        assert kernel.WaitForSingleObject(handle, 1000) == 0
                finally:
                    kernel.CloseHandle(handle)


@pytest.mark.parametrize('code', ['import sys; sys.stdin.buffer.read(); sys.exit(3)',
    'import sys; sys.stdin.buffer.read(); sys.stderr.write("secret")'])
def test_nonzero_or_stderr_never_accepted_or_echoed(code):
    with pytest.raises(disk.DiskObservationError, match='^helper_failed$'):
        transport(code)


class FakePipe:
    def __init__(self):
        self.closed = False

    def fileno(self):
        return 100

    def close(self):
        self.closed = True


class CleanupChild:
    def __init__(self, failure):
        self.stdin, self.stdout, self.stderr = FakePipe(), FakePipe(), FakePipe()
        self.failure, self.exited = failure, False
        self.kill_calls, self.wait_calls = 0, []

    def poll(self):
        return 0 if self.exited else None

    def kill(self):
        self.kill_calls += 1
        if self.failure == 'kill':
            raise OSError('secret kill failure')

    def wait(self, timeout):
        self.wait_calls.append(timeout)
        if self.failure == 'wait':
            raise OSError('secret wait failure')
        if not self.exited:
            raise subprocess.TimeoutExpired('owned fixture', timeout)
        return 0


@pytest.mark.parametrize('failure', ['kill', 'wait', 'grace_timeout'])
def test_failed_cleanup_retains_owned_child_and_suppresses_launch_until_confirmed_reaped(monkeypatch, failure):
    child, launched = CleanupChild(failure), []
    def popen(*args, **kwargs):
        launched.append(1)
        return child
    monkeypatch.setattr(disk, '_quarantined_child', None)
    monkeypatch.setattr(disk.subprocess, 'Popen', popen)
    monkeypatch.setattr(disk.os, 'set_blocking', lambda *args: None)
    monkeypatch.setattr(disk, 'HELPER_TIMEOUT_SECONDS', 0)
    with pytest.raises(disk.DiskObservationError, match='^helper_cleanup_failed$'):
        disk._run_bounded(['owned fixture'], b'{}', cwd='.', env={})
    assert disk._quarantined_child is child and len(launched) == 1
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))
    for _ in range(3):
        with pytest.raises(disk.DiskObservationError, match='^helper_cleanup_pending$'):
            disk._run_bounded(['owned fixture'], b'{}', cwd='.', env={})
    assert len(launched) == 1 and disk._quarantined_child is child
    child.exited = True
    child.failure = 'wait'
    with pytest.raises(disk.DiskObservationError, match='^helper_cleanup_pending$'):
        disk._run_bounded(['owned fixture'], b'{}', cwd='.', env={})
    assert disk._quarantined_child is child  # poll==0 alone must not release it
    child.failure = None
    monkeypatch.setattr(disk, '_run_owned', lambda *args, **kwargs: b'new helper result')
    assert disk._run_bounded(['owned fixture'], b'{}', cwd='.', env={}) == b'new helper result'
    assert disk._quarantined_child is None and child.wait_calls[-1] == 0


def test_cleanup_pending_is_visible_unavailable_backoff_not_authority(monkeypatch):
    instance, now, runner = probe([disk.DiskObservationError('helper_cleanup_pending'), packet(6)])
    result = instance.snapshot()
    assert result == disk._unavailable('helper_cleanup_pending')
    now[0] = 59
    instance.snapshot()
    assert len(runner.calls) == 1
    now[0] = 60
    assert instance.snapshot()['status'] == 'warming_up'
