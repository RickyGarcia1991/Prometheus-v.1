"""Read existing Windows Job Object counters before its owner closes the handle.

These are job-associated process metrics, not guaranteed complete process-tree
coverage: a child created before assignment may escape the existing runner job.
This module never creates, assigns, changes, terminates or closes a job/process.
"""
from __future__ import annotations

import ctypes
import os

_IS_WINDOWS = os.name == 'nt'


class _BasicAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        'user_time', 'kernel_time', 'period_user_time', 'period_kernel_time')] + [
        (name, ctypes.c_uint32) for name in (
            'page_faults', 'total_processes', 'active_processes', 'terminated_processes')]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'read_operations', 'write_operations', 'other_operations',
        'read_bytes', 'write_bytes', 'other_bytes')]


class _AccountingAndIo(ctypes.Structure):
    _fields_ = [('basic', _BasicAccounting), ('io', _IoCounters)]


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
        ('flags', ctypes.c_uint32), ('min_working_set', ctypes.c_size_t),
        ('max_working_set', ctypes.c_size_t), ('active_processes', ctypes.c_uint32),
        ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32),
        ('scheduling', ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('reserved_io', _IoCounters),
        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class _WindowsApi:
    def __init__(self):
        self.query = ctypes.WinDLL('kernel32', use_last_error=True).QueryInformationJobObject
        self.query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                              ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        self.query.restype = ctypes.c_int

    def _read(self, handle, kind, structure):
        result = structure()
        returned = ctypes.c_uint32()
        if not self.query(handle, kind, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned)):
            raise OSError('Job accounting unavailable')
        if returned.value != ctypes.sizeof(result):
            raise OSError('Incomplete job accounting structure')
        return result

    def accounting(self, handle):
        result = self._read(handle, 8, _AccountingAndIo)
        return dict(cpu_ticks=result.basic.user_time + result.basic.kernel_time,
                    io_bytes=result.io.read_bytes + result.io.write_bytes + result.io.other_bytes,
                    total_processes=result.basic.total_processes,
                    active_processes=result.basic.active_processes)

    def peak_commit(self, handle):
        # Extended-limit IoInfo is reserved; never use it for I/O accounting.
        return self._read(handle, 9, _ExtendedLimits).peak_job_memory


def _counter(value):
    return type(value) is int and 0 <= value <= 2**64 - 1


def capture_job(handle, *, api=None):
    """Return separate job counters without changing existing direct RAM metrics.

    api test seam: accounting(handle) returns cpu_ticks/io_bytes/total_processes/
    active_processes; peak_commit(handle) returns bytes. Missing/failed queries
    stay None independently. snapshot_complete describes successful accounting
    with zero active members, not proof that every descendant joined this job.
    """
    result = dict(schema_version=1, scope='job_associated_processes',
        peak_commit_gib=None, cpu_seconds=None, io_mib=None,
        total_processes=None, active_processes=None, snapshot_complete=None,
        memory_scope='peak committed memory; not resident working set',
        io_scope='OS read/write/other transfer volume; includes non-disk I/O',
        coverage='Only job-associated processes; descendants created before assignment may be outside the job.',
        sources={name: 'unavailable' for name in (
            'peak_commit_gib', 'cpu_seconds', 'io_mib', 'total_processes', 'active_processes')})
    if not _IS_WINDOWS and api is None:
        return result
    try:
        if isinstance(handle, ctypes.c_void_p):
            handle = handle.value
        if not _counter(handle) or handle == 0:
            return result
        reader = api if api is not None else _WindowsApi()
    except Exception:
        return result
    source = 'injected test counters' if api is not None else 'Windows QueryInformationJobObject'
    try:
        raw = reader.accounting(handle)
        valid = isinstance(raw, dict)
        if valid:
            for key, original, divisor in (
                ('cpu_seconds', 'cpu_ticks', 10_000_000), ('io_mib', 'io_bytes', 1024**2),
                ('total_processes', 'total_processes', 1), ('active_processes', 'active_processes', 1)):
                value = raw.get(original)
                if _counter(value):
                    result[key] = value if divisor == 1 else value / divisor
                    result['sources'][key] = source
                else:
                    valid = False
            total, active = result['total_processes'], result['active_processes']
            if total is not None and active is not None and active > total:
                result['total_processes'] = result['active_processes'] = None
                result['sources']['total_processes'] = result['sources']['active_processes'] = 'unavailable'
                valid = False
            if valid:
                result['snapshot_complete'] = active == 0
    except Exception:
        pass
    try:
        peak = reader.peak_commit(handle)
        if _counter(peak):
            result['peak_commit_gib'] = peak / 1024**3
            result['sources']['peak_commit_gib'] = source
    except Exception:
        pass
    return result
