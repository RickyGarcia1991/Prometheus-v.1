"""Low-frequency read-only resource observations; no worker or polling thread."""
from __future__ import annotations

import copy
import ctypes
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import shutil
import threading
import time

from .hardware import detect_hardware


def _cpu_times():
    if os.name == 'nt':
        class FileTime(ctypes.Structure):
            _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]
        idle, kernel, user = FileTime(), FileTime(), FileTime()
        if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError('CPU counters unavailable')
        def ticks(value):
            return (value.high << 32) | value.low
        return ticks(idle), ticks(kernel) + ticks(user)
    # Linux counters include guest time in user/nice already; do not count twice.
    values = Path('/proc/stat').read_text(encoding='ascii').splitlines()[0].split()
    if values[0] != 'cpu' or len(values) < 5:
        raise ValueError('CPU counters unavailable')
    counters = [int(value) for value in values[1:9]]
    return counters[3] + (counters[4] if len(counters) > 4 else 0), sum(counters)


def _power_status():
    if os.name != 'nt':
        from .linux_power import read_power
        return read_power()
    class PowerStatus(ctypes.Structure):
        _fields_ = [('ac', ctypes.c_ubyte), ('flag', ctypes.c_ubyte),
                    ('percent', ctypes.c_ubyte), ('reserved', ctypes.c_ubyte),
                    ('remaining', ctypes.c_uint32), ('full', ctypes.c_uint32)]
    status = PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise OSError('Power status unavailable')
    percent = status.percent if status.percent <= 100 and not status.flag & 128 else None
    plugged = {0: False, 1: True}.get(status.ac)
    return percent, plugged


def _sample(path):
    result = {'sources': {}}
    sources = result['sources']
    try:
        hardware = detect_hardware()
        result.update(total_ram_gib=hardware.ram_gib,
                      available_ram_gib=hardware.available_ram_gib)
        origin = 'hardware.detect_hardware: ' + hardware.system
        sources.update(total_ram_gib=origin, available_ram_gib=origin)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        result['free_disk_gib'] = shutil.disk_usage(path).free / 1024**3
        sources['free_disk_gib'] = 'shutil.disk_usage: configured data volume'
    except (OSError, ValueError):
        pass
    try:
        result['cpu_times'] = _cpu_times()
        sources['cpu_percent'] = ('Windows GetSystemTimes' if os.name == 'nt' else '/proc/stat')
    except (OSError, ValueError, AttributeError, IndexError):
        pass
    try:
        result['battery_percent'], result['plugged_in'] = _power_status()
        if os.name == 'nt':
            sources.update(battery_percent='Windows GetSystemPowerStatus',
                           plugged_in='Windows GetSystemPowerStatus')
        else:
            sources.update(battery_percent='/sys/class/power_supply capacity',
                           plugged_in='/sys/class/power_supply online')
    except (OSError, ValueError, AttributeError):
        pass
    return result


def _number(value, maximum=None):
    if type(value) not in (int, float) or value < 0 or maximum is not None and value > maximum:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _ticks(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    if any(type(item) is not int or item < 0 for item in value) or value[0] > value[1]:
        return None
    return tuple(value)


class ResourceProbe:
    """Sample on demand at most once per ten monotonic seconds.

    sample(path) is an optional deterministic test seam yielding raw measurements
    and cpu_times=(idle_ticks, total_ticks). CPU requires two valid samples; reset,
    wrap, missing or inconsistent counters produce None rather than a false zero.
    snapshot() is JSON-ready, returns independent copies, and never starts a thread.
    Whole-device battery change requires a 60-900 second observation window,
    with no gap over 60 seconds or observed power/charge discontinuity. It is
    not attributable to a job and does not predict energy use or runtime.
    Unknown thermal/GPU/IO/event data are explicitly None. No process or OS
    settings are modified. The first caller after cache expiry performs native I/O.
    """
    def __init__(self, path, *, clock=time.monotonic, sample=None):
        self.path = Path(path)
        self._clock = clock
        self._sample = sample or _sample
        self._injected = sample is not None
        self._lock = threading.Lock()
        self._cache = None
        self._sampled_at = None
        self._previous_cpu = None
        self._battery_baseline = None
        self._battery_previous = None

    def _battery(self, now, percent, plugged):
        result = dict(status='unavailable', scope='whole_device', percentage_points=None,
                      interval_seconds=None, reason='Waiting for an on-battery baseline.')
        if plugged is not False or percent is None:
            self._battery_baseline = self._battery_previous = None
            result['reason'] = ('Connected to external power; discharge interval reset.' if plugged is True
                else 'Power source is unknown; discharge interval reset.' if plugged is None
                else 'Battery percentage is unavailable; discharge interval reset.')
            return result
        previous, baseline = self._battery_previous, self._battery_baseline
        self._battery_previous = (now, percent)
        reset = None
        if previous is not None:
            if now < previous[0]:
                reset = 'The monotonic clock moved backward; starting a fresh interval.'
            elif now - previous[0] > 60:
                reset = 'Observation gap exceeded 60 seconds; starting a fresh interval.'
            elif percent > previous[1]:
                reset = 'Battery percentage increased; starting a fresh interval.'
            elif baseline is not None and now - baseline[0] > 900:
                reset = 'The 15-minute observation window ended; starting a fresh interval.'
        if baseline is None or reset:
            self._battery_baseline = (now, percent)
            result['reason'] = reset or result['reason']
            return result
        elapsed = now - baseline[0]
        if elapsed < 60:
            result['reason'] = 'Waiting for at least 60 seconds of on-battery observations.'
            return result
        return dict(status='observed', scope='whole_device',
                    percentage_points=round(baseline[1] - percent, 6),
                    interval_seconds=round(elapsed, 6),
                    reason='Observed battery percentage change across the whole device; includes all activity.')

    def snapshot(self, *, force=False):
        with self._lock:
            now = self._clock()
            if not force and self._cache is not None and 0 <= now - self._sampled_at < 10:
                return copy.deepcopy(self._cache)
            try:
                raw = self._sample(self.path)
            except (OSError, ValueError, AttributeError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            result = {name: None for name in ('total_ram_gib', 'available_ram_gib',
                'free_disk_gib', 'cpu_percent', 'battery_percent', 'plugged_in',
                'vram_gib', 'thermal_distress', 'disk_io_mib', 'battery_drain_percent',
                'low_memory')}
            for name in ('total_ram_gib', 'available_ram_gib', 'free_disk_gib'):
                result[name] = _number(raw.get(name))
            if result['total_ram_gib'] == 0:
                result['total_ram_gib'] = None
            result['battery_percent'] = _number(raw.get('battery_percent'), 100)
            if type(raw.get('plugged_in')) is bool:
                result['plugged_in'] = raw['plugged_in']
            current = _ticks(raw.get('cpu_times'))
            previous = self._previous_cpu
            if current is not None and previous is not None and now >= self._sampled_at:
                idle, total = current[0] - previous[0], current[1] - previous[1]
                if 0 <= idle <= total and total > 0:
                    result['cpu_percent'] = round(100 * (total - idle) / total, 2)
            self._previous_cpu = current
            supplied = raw.get('sources', {})
            if not isinstance(supplied, dict):
                supplied = {}
            result['sources'] = {
                name: (supplied.get(name, 'injected sampler' if self._injected else 'native observation')
                       if value is not None else 'unavailable')
                for name, value in result.items()}
            battery = self._battery(now, result['battery_percent'], result['plugged_in'])
            result['battery_observation'] = battery
            result['battery_drain_percent'] = battery['percentage_points']
            if battery['status'] == 'observed':
                result['sources']['battery_drain_percent'] = (
                    'Difference between observed battery percentages across the whole device')
            result['observed_at'] = datetime.now(timezone.utc).isoformat()
            result['cache_seconds'] = 10
            self._sampled_at = now
            self._cache = result
            return copy.deepcopy(result)
