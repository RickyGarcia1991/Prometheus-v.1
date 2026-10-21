"""Read-only measurements of a trusted Windows Popen process, never its children.

Call while Popen still owns its process handle, including immediately after wait.
No PID reopening, polling, process control, or new permissions are performed.
"""
from __future__ import annotations

import ctypes
import math
import os

_IS_WINDOWS = os.name == 'nt'


class _FileTime(ctypes.Structure):
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]


class _MemoryCounters(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('page_faults', ctypes.c_uint32),
                ('peak_working_set', ctypes.c_size_t), ('working_set', ctypes.c_size_t),
                ('peak_paged_pool', ctypes.c_size_t), ('paged_pool', ctypes.c_size_t),
                ('peak_nonpaged_pool', ctypes.c_size_t), ('nonpaged_pool', ctypes.c_size_t),
                ('pagefile', ctypes.c_size_t), ('peak_pagefile', ctypes.c_size_t)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'read_operations', 'write_operations', 'other_operations',
        'read_bytes', 'write_bytes', 'other_bytes')]


class _WindowsApi:
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        self.memory = psapi.GetProcessMemoryInfo
        self.memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MemoryCounters), ctypes.c_uint32]
        self.memory.restype = ctypes.c_int
        self.times = kernel.GetProcessTimes
        self.times.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(_FileTime)] * 4
        self.times.restype = ctypes.c_int
        self.io = kernel.GetProcessIoCounters
        self.io.argtypes = [ctypes.c_void_p, ctypes.POINTER(_IoCounters)]
        self.io.restype = ctypes.c_int

    def peak_working_set(self, handle):
        counters = _MemoryCounters()
        counters.size = ctypes.sizeof(counters)
        if not self.memory(handle, ctypes.byref(counters), counters.size):
            raise OSError('Process memory counters unavailable')
        return counters.peak_working_set

    def cpu_ticks(self, handle):
        created, exited, kernel, user = (_FileTime() for _ in range(4))
        if not self.times(handle, ctypes.byref(created), ctypes.byref(exited),
                          ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError('Process CPU counters unavailable')
        return (kernel.high << 32) + kernel.low + (user.high << 32) + user.low

    def io_bytes(self, handle):
        counters = _IoCounters()
        if not self.io(handle, ctypes.byref(counters)):
            raise OSError('Process I/O counters unavailable')
        return counters.read_bytes + counters.write_bytes + counters.other_bytes


def capture(process, *, api=None):
    """Return JSON-ready direct-process measurements; unavailable values are None.

    ram_gib is peak resident working set, not commit or process-tree memory.
    cpu_seconds is cumulative kernel+user CPU time, not elapsed duration.
    io_mib is cumulative OS read/write/other transfer volume, which may include
    network/device I/O. It is NOT physical disk traffic or a disk budget estimate.
    api is a test seam exposing peak_working_set/cpu_ticks/io_bytes(handle).
    All counters come from the existing trusted process handle, never model text.
    """
    result = dict(ram_gib=None, cpu_seconds=None, io_mib=None,
                  scope='direct_process', descendants_measured=False,
                  memory_scope='peak working set; excludes descendants and commit charge',
                  io_scope='OS read/write/other transfer bytes; includes non-disk I/O',
                  sources={name: 'unavailable' for name in ('ram_gib', 'cpu_seconds', 'io_mib')})
    if not _IS_WINDOWS and api is None:
        return result
    try:
        handle = int(process._handle)
        if handle <= 0:
            return result
        reader = api if api is not None else _WindowsApi()
    except Exception:
        return result
    for field, method, divisor, source in (
        ('ram_gib', 'peak_working_set', 1024**3, 'Windows GetProcessMemoryInfo'),
        ('cpu_seconds', 'cpu_ticks', 10_000_000, 'Windows GetProcessTimes'),
        ('io_mib', 'io_bytes', 1024**2, 'Windows GetProcessIoCounters'),
    ):
        try:
            raw = getattr(reader, method)(handle)
            if type(raw) is not int or raw < 0:
                continue
            value = raw / divisor
            if not math.isfinite(value):
                continue
            result[field] = value
            result['sources'][field] = source if api is None else 'injected test counters'
        except Exception:
            # Measurement failure must never change a test's execution outcome.
            continue
    return result
