import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from prometheus_assistant.project_check_metrics import capture


class Counters:
    def peak_working_set(self, handle):
        assert handle == 42
        return 3 * 1024**3
    def cpu_ticks(self, handle):
        return 25_000_000
    def io_bytes(self, handle):
        return 7 * 1024**2


def test_exact_units_and_direct_process_scope():
    result = capture(SimpleNamespace(_handle=42), api=Counters())
    assert result['ram_gib'] == 3
    assert result['cpu_seconds'] == 2.5
    assert result['io_mib'] == 7
    assert result['scope'] == 'direct_process'
    assert result['descendants_measured'] is False
    assert 'non-disk' in result['io_scope']
    assert 'disk_io_mib' not in result
    json.dumps(result, allow_nan=False)


def test_failure_in_one_metric_preserves_others():
    class Partial(Counters):
        def peak_working_set(self, handle):
            raise OSError('denied')
    result = capture(SimpleNamespace(_handle=42), api=Partial())
    assert result['ram_gib'] is None
    assert result['cpu_seconds'] == 2.5
    assert result['sources']['ram_gib'] == 'unavailable'


@pytest.mark.parametrize('value', [-1, True, float('nan'), None, '10'])
def test_invalid_counters_remain_unknown(value):
    class Invalid(Counters):
        def cpu_ticks(self, handle):
            return value
    assert capture(SimpleNamespace(_handle=42), api=Invalid())['cpu_seconds'] is None


@pytest.mark.parametrize('process', [None, SimpleNamespace(), SimpleNamespace(_handle=0)])
def test_unavailable_handle_is_nonfatal(process):
    result = capture(process, api=Counters())
    assert all(result[key] is None for key in ('ram_gib', 'cpu_seconds', 'io_mib'))


def test_non_windows_is_explicitly_unknown(monkeypatch):
    from prometheus_assistant import project_check_metrics as module
    monkeypatch.setattr(module, '_IS_WINDOWS', False)
    assert capture(SimpleNamespace(_handle=42))['ram_gib'] is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process handle acceptance')
def test_actual_process_counters_after_exit():
    process = subprocess.Popen([sys.executable, '-B', '-c',
        'data=bytearray(8*1024*1024); print(sum(range(200000)))'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    output, error = process.communicate(timeout=15)
    assert process.returncode == 0, error
    result = capture(process)
    assert result['ram_gib'] is not None and result['ram_gib'] > 0
    assert result['cpu_seconds'] is not None and result['cpu_seconds'] >= 0
    assert result['io_mib'] is not None and result['io_mib'] >= 0
    assert result['sources']['ram_gib'] == 'Windows GetProcessMemoryInfo'
    assert output
