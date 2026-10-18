"""Demand-only, primary-app disk observations; never admission or job cost.

Formulas: https://learn.microsoft.com/en-us/windows/win32/perfctrs/calculating-counter-values
Windows nonblocking pipes require Python 3.12+. A helper gets two seconds of
work and at most 0.25 seconds of owned-child termination/reaping grace. There
are no background or pipe-draining threads. Process creation is synchronous.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import math
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from . import windows_disk_observations as native


CACHE_SECONDS = 10.0
BACKOFF_SECONDS = 60.0
MAX_INTERVAL_SECONDS = 60.0
HELPER_TIMEOUT_SECONDS = 2.0
REAP_GRACE_SECONDS = 0.25
STDERR_LIMIT = 4096
_launch_lock = threading.Lock()
_quarantined_child = None
_DIGEST = re.compile(r'[0-9a-f]{64}')
_NATIVE_REASONS = frozenset({
    'invalid_request', 'platform_unsupported', 'native_unavailable',
    'directory_unavailable', 'volume_unavailable', 'metadata_unavailable',
    'identity_unavailable', 'identity_changed', 'mapping_unsupported',
    'mapping_ambiguous', 'topology_unsupported', 'cleanup_failed',
    'counter_unavailable', 'counter_metadata_unavailable', 'counter_buffer_invalid',
    'counter_data_invalid', 'instance_unavailable', 'observations_incomplete', 'output_limit',
})


class DiskObservationError(Exception):
    """Fixed diagnostic code, never native output or a path."""


def _helper_executable():
    # A Windows venv python.exe can redirect to another PID. Own the stdlib-only
    # interpreter directly so timeout termination reaches the actual helper.
    return str(Path(getattr(sys, '_base_executable', None) or sys.executable).resolve())


def _run_bounded(command, payload, *, cwd, env):
    """Private transport seam; production command is fixed by run_disk_helper."""
    global _quarantined_child
    with _launch_lock:
        if _quarantined_child is not None:
            try:
                if _quarantined_child.poll() is None:
                    _quarantined_child.kill()
                # No new launch until completion is confirmed. Polling/kill
                # alone is not enough, and no cleanup thread is left behind.
                _quarantined_child.wait(timeout=0)
            except Exception:
                raise DiskObservationError('helper_cleanup_pending') from None
            _quarantined_child = None
        return _run_owned(command, payload, cwd=cwd, env=env)


def _run_owned(command, payload, *, cwd, env):
    """Caller holds _launch_lock through launch, completion and quarantine."""
    global _quarantined_child
    started = time.monotonic()
    process = None
    streams = []
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, bufsize=0, cwd=cwd, env=env,
            close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        streams = [process.stdin, process.stdout, process.stderr]
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
        buffers = [bytearray(), bytearray()]
        limits = [native.MAX_OUTPUT_BYTES, STDERR_LIMIT]
        eof = [False, False]
        sent = 0
        while True:
            if time.monotonic() - started >= HELPER_TIMEOUT_SECONDS:
                raise DiskObservationError('helper_timeout')
            if not process.stdin.closed:
                try:
                    sent += os.write(process.stdin.fileno(), payload[sent:sent + 4096])
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    raise DiskObservationError('helper_failed') from None
                if sent == len(payload):
                    process.stdin.close()
            for index, stream in enumerate(streams[1:]):
                if eof[index]:
                    continue
                try:
                    chunk = os.read(stream.fileno(), min(4096, limits[index] + 1 - len(buffers[index])))
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    chunk = b''
                if not chunk:
                    eof[index] = True
                elif len(buffers[index]) + len(chunk) > limits[index]:
                    raise DiskObservationError('helper_output_limit')
                else:
                    buffers[index].extend(chunk)
            if process.poll() is not None and all(eof):
                if process.returncode != 0 or buffers[1]:
                    raise DiskObservationError('helper_failed')
                return bytes(buffers[0])
            time.sleep(0.005)
    except DiskObservationError:
        raise
    except (OSError, ValueError, NotImplementedError):
        raise DiskObservationError('helper_unavailable') from None
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=REAP_GRACE_SECONDS)
            except BaseException as exc:
                # Keep the actual owned interpreter reachable after failed
                # termination/reaping; later calls cannot accumulate children.
                _quarantined_child = process
                if not isinstance(exc, Exception):
                    raise
                raise DiskObservationError('helper_cleanup_failed') from None
            finally:
                for stream in streams:
                    stream.close()


def run_disk_helper(roots):
    """Fixed local helper. No model command, executable, module or cwd argument."""
    if os.name != 'nt':
        raise DiskObservationError('platform_unsupported')
    try:
        selected = native._roots(roots)
        payload = json.dumps({'schema_version': 1, 'roots': selected},
                             separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    except (native._Unavailable, UnicodeError):
        raise DiskObservationError('invalid_request') from None
    if len(payload) > native.MAX_INPUT_BYTES:
        raise DiskObservationError('helper_input_limit')
    source = str(Path(__file__).resolve().parent.parent)
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith('PYTHON')}
    environment.update(PYTHONPATH=source, PYTHONDONTWRITEBYTECODE='1',
                       PYTHONNOUSERSITE='1', PYTHONSAFEPATH='1')
    return _run_bounded([_helper_executable(), '-B', '-m',
        'prometheus_assistant.windows_disk_observations'], payload, cwd=source, env=environment)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _keys(value, keys):
    return type(value) is dict and set(value) == set(keys.split())


def _digest(value):
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _reason(value):
    return value is None or type(value) is str and value in _NATIVE_REASONS


def _validate_counter(value, metric):
    if _keys(value, 'status reason_code'):
        return value['status'] == 'unavailable' and value['reason_code'] in _NATIVE_REASONS
    if not _keys(value, 'status reason_code instance_id cstatus timestamp_100ns first second multi_count counter_type time_base scale default_scale'):
        return False
    return (value['status'] == 'raw' and value['reason_code'] is None
        and _digest(value['instance_id']) and _integer(value['cstatus'], 0, 1)
        and _integer(value['timestamp_100ns'], 1, 2**64 - 1)
        and _integer(value['first'], 0, 2**63 - 1) and _integer(value['second'], 0, 2**63 - 1)
        and _integer(value['multi_count'], 0, 1)
        and type(value['counter_type']) is int and value['counter_type'] == native.COUNTERS[metric][1]
        and (value['time_base'] is None if metric == 'queue' else _integer(value['time_base'], 1, 2**63 - 1))
        and _integer(value['scale'], -7, 7) and _integer(value['default_scale'], -7, 7))


def _validate_packet(payload, roles):
    try:
        if type(payload) is not bytes or len(payload) > native.MAX_OUTPUT_BYTES:
            raise ValueError()
        packet = json.loads(payload.decode('utf-8'), object_pairs_hook=native._unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not _keys(packet, 'schema_version status reason_code source scope volumes devices')
                or type(packet['schema_version']) is not int or packet['schema_version'] != 1
                or packet['source'] != 'windows_pdh_raw' or packet['scope'] != 'device'
                or packet['status'] not in ('raw', 'partial', 'unavailable') or not _reason(packet['reason_code'])
                or type(packet['volumes']) is not list or len(packet['volumes']) > 2
                or type(packet['devices']) is not list or len(packet['devices']) > 2):
            raise ValueError()
        seen_roles, linked = set(), set()
        for volume in packet['volumes']:
            if not type(volume) is dict or volume.get('role') not in roles or volume['role'] in seen_roles:
                raise ValueError()
            seen_roles.add(volume['role'])
            if volume.get('status') == 'mapped':
                if (not _keys(volume, 'role status reason_code volume_id device_id mapping_id')
                        or volume['reason_code'] is not None
                        or not all(_digest(volume[key]) for key in ('volume_id', 'device_id', 'mapping_id'))):
                    raise ValueError()
                linked.add(volume['device_id'])
            elif (not _keys(volume, 'role status reason_code') or volume['status'] != 'unavailable'
                    or volume['reason_code'] not in _NATIVE_REASONS):
                raise ValueError()
        if seen_roles != set(roles) and (packet['devices'] or packet['volumes']):
            raise ValueError()
        seen_devices = set()
        for device in packet['devices']:
            if (not _keys(device, 'device_id identity_basis status counters')
                    or not _digest(device['device_id']) or device['device_id'] in seen_devices
                    or device['identity_basis'] != 'storage_device_number_ex_hardware_guid'
                    or type(device['counters']) is not dict or set(device['counters']) != set(native.COUNTERS)
                    or not all(_validate_counter(value, metric) for metric, value in device['counters'].items())):
                raise ValueError()
            seen_devices.add(device['device_id'])
            raw = [item for item in device['counters'].values() if item['status'] == 'raw']
            expected = 'raw' if len(raw) == 4 else 'partial' if raw else 'unavailable'
            if device['status'] != expected or len({item['instance_id'] for item in raw}) > 1:
                raise ValueError()
        if seen_devices != linked:
            raise ValueError()
        valid = sum(item['status'] in ('raw', 'partial') for item in packet['devices'])
        complete = all(item['status'] == 'raw' for item in packet['devices']) and all(item['status'] == 'mapped' for item in packet['volumes'])
        expected = 'raw' if valid and complete else 'partial' if valid else 'unavailable'
        if packet['status'] != expected or (packet['reason_code'] is None) != (expected == 'raw'):
            raise ValueError()
        return packet
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise DiskObservationError('helper_output_invalid') from None


def _mapping(packet, device_id):
    return sorted((row['role'], row['volume_id'], row['mapping_id'])
        for row in packet['volumes'] if row['status'] == 'mapped' and row['device_id'] == device_id)


def _delta(current, previous, metric, elapsed):
    new, old = current['counters'][metric], previous['counters'][metric]
    if new['status'] != 'raw':
        return None, new['reason_code']
    if old['status'] != 'raw':
        return None, 'warming_up'
    metadata = ('instance_id', 'counter_type', 'time_base', 'scale', 'default_scale', 'multi_count')
    if any(new[key] != old[key] for key in metadata):
        return None, 'counter_changed'
    if new['timestamp_100ns'] <= old['timestamp_100ns'] or new['first'] < old['first'] or new['second'] < old['second']:
        return None, 'counter_reset'
    stamp_elapsed = (new['timestamp_100ns'] - old['timestamp_100ns']) / 10000000
    if abs(stamp_elapsed - elapsed) > 4:
        return None, 'sample_time_changed'
    return (new['first'] - old['first'], new['second'] - old['second'], new['time_base']), None


def _reduce(packet, previous, elapsed):
    devices = []
    previous_devices = {row['device_id']: row for row in previous['devices']} if previous else {}
    for current in packet['devices']:
        identifier = current['device_id']
        item = dict(device_id=identifier,
                    roles=[row[0] for row in _mapping(packet, identifier)],
                    status='unavailable', interval_seconds=None, latency_ms=None,
                    busy_percent=None, queue_length=None, transfers_per_second=None, reasons={})
        reasons = item['reasons']
        queue = current['counters']['queue']
        old = previous_devices.get(identifier)
        same_mapping = old is not None and _mapping(packet, identifier) == _mapping(previous, identifier)
        old_queue = old['counters']['queue'] if same_mapping else None
        if queue['status'] == 'raw' and queue['first'] <= 2**32 - 1:
            if (old_queue is not None and old_queue['status'] == 'raw'
                    and old_queue['instance_id'] == queue['instance_id']
                    and queue['timestamp_100ns'] <= old_queue['timestamp_100ns']):
                reasons['queue_length'] = 'counter_reset'
            else:
                item['queue_length'] = queue['first']
        else:
            reasons['queue_length'] = queue.get('reason_code') or 'counter_data_invalid'
        reset = None
        if old is None:
            reset = 'warming_up'
        elif _mapping(packet, identifier) != _mapping(previous, identifier):
            reset = 'identity_changed'
        elif elapsed is None or not CACHE_SECONDS <= elapsed <= MAX_INTERVAL_SECONDS:
            reset = 'sample_gap'
        deltas = {}
        for metric, field in (('latency', 'latency_ms'), ('idle', 'busy_percent'), ('transfers', 'transfers_per_second')):
            counter = current['counters'][metric]
            if counter['status'] != 'raw':
                reasons[field] = counter['reason_code']
            elif reset:
                reasons[field] = reset
            else:
                value, reason = _delta(current, old, metric, elapsed)
                if reason:
                    reasons[field] = reason
                else:
                    deltas[metric] = value
        if not reset:
            item['interval_seconds'] = elapsed
        if 'idle' in deltas:
            numerator, denominator, _ = deltas['idle']
            if (denominator > 0 and 0 <= numerator <= denominator
                    and abs(denominator / 10000000 - elapsed) <= 4):
                item['busy_percent'] = 100.0 * (1.0 - numerator / denominator)
            else:
                reasons['busy_percent'] = 'counter_data_invalid'
        if 'transfers' in deltas:
            count, ticks, frequency = deltas['transfers']
            seconds = ticks / frequency
            if ticks > 0 and abs(seconds - elapsed) <= 4:
                item['transfers_per_second'] = count / seconds
            else:
                reasons['transfers_per_second'] = 'counter_data_invalid'
        if 'latency' in deltas:
            ticks, count, frequency = deltas['latency']
            transfers = deltas.get('transfers')
            if transfers is None or item['transfers_per_second'] is None:
                reasons['latency_ms'] = 'transfers_unavailable'
            elif transfers[0] == 0 or count == 0:
                reasons['latency_ms'] = 'no_transfers'
            elif count != transfers[0]:
                reasons['latency_ms'] = 'transfer_count_mismatch'
            else:
                item['latency_ms'] = (ticks / frequency) / count * 1000.0
        for key in ('latency_ms', 'busy_percent', 'transfers_per_second'):
            if item[key] is not None and not math.isfinite(item[key]):
                item[key], reasons[key] = None, 'counter_data_invalid'
        values = [item[key] for key in ('latency_ms', 'busy_percent', 'queue_length', 'transfers_per_second')]
        if all(value is not None for value in values):
            item['status'] = 'observed'
        elif any(value is not None for value in values):
            item['status'] = 'warming_up' if reset else 'partial'
        devices.append(item)
    return devices


def _unavailable(reason):
    return dict(status='unavailable', source='windows_pdh', scope='whole_device',
                devices=[], volumes=[], observed_at=None, age_seconds=None,
                reason_code=reason, stale=True)


class DiskPressureProbe:
    """Primary-only demand observer. Review/reservation paths must not call it.

    ``runner(roots) -> bytes`` is an injection seam for deterministic tests.
    Snapshot copies are isolated; concurrent callers share a single cold read.
    Cache times are fixed, not user/model-controlled performance settings.
    """
    def __init__(self, roots, *, clock=time.monotonic, runner=run_disk_helper,
                 wall_clock=lambda: datetime.now(timezone.utc)):
        self._roots = native._roots(copy.deepcopy(roots))
        self._project_unsupported = False
        self._clock, self._runner, self._wall_clock = clock, runner, wall_clock
        self._lock = threading.Lock()
        self._cached = None
        self._sampled_at = None
        self._previous = None
        self._previous_at = None
        self._cache_seconds = CACHE_SECONDS

    def select_project(self, root):
        """Observe only the last successfully opened workspace, shared by viewers.

        This changes the next bounded read, without sampling or saving paths.
        A different selection discards interval baselines and displayed values,
        but preserves the read timestamp to prevent rapid opens flooding helpers.
        """
        with self._lock:
            roots = {'data': self._roots['data']}
            unsupported = False
            if root is not None:
                try:
                    roots = native._roots({**roots, 'project': root})
                except native._Unavailable:
                    # Network/unsupported paths remain usable workspaces. Never
                    # send them to the native helper or hide the app-data disk.
                    unsupported = True
            same_roots = ({key: ntpath.normcase(value) for key, value in roots.items()} ==
                          {key: ntpath.normcase(value) for key, value in self._roots.items()})
            if same_roots and unsupported == self._project_unsupported:
                return
            self._roots, self._project_unsupported = roots, unsupported
            self._previous, self._previous_at = None, None
            self._cached = _unavailable('selection_changed')
            self._cache_seconds = CACHE_SECONDS

    def _public_snapshot(self, result):
        result = copy.deepcopy(result)
        if self._project_unsupported:
            result['volumes'].append(dict(role='project', status='unavailable', reason_code='mapping_unsupported'))
            if result['status'] in {'observed', 'warming_up'}:
                result['status'], result['reason_code'] = 'partial', 'mapping_unsupported'
        return result

    def snapshot(self):
        with self._lock:
            now = self._clock()
            if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
                self._previous, self._previous_at = None, None
                self._cached, self._sampled_at = None, None
                return self._public_snapshot(_unavailable('clock_unavailable'))
            age = now - self._sampled_at if self._sampled_at is not None else None
            if age is not None and 0 <= age < self._cache_seconds:
                result = copy.deepcopy(self._cached)
                result['age_seconds'] = age if result['observed_at'] else None
                return self._public_snapshot(result)
            if age is not None and age < 0:
                self._previous = None
            try:
                packet = _validate_packet(self._runner(copy.deepcopy(self._roots)), self._roots)
                finished = self._clock()
                if type(finished) not in (int, float) or not math.isfinite(finished) or finished < now:
                    raise DiskObservationError('clock_unavailable')
                elapsed = finished - self._previous_at if self._previous_at is not None else None
                devices = _reduce(packet, self._previous, elapsed)
                valid = [item for item in devices if item['status'] != 'unavailable']
                mapped = all(row['status'] == 'mapped' for row in packet['volumes'])
                status = ('observed' if valid and mapped and all(row['status'] == 'observed' for row in devices)
                          else 'warming_up' if valid and mapped and all(row['status'] == 'warming_up' for row in devices)
                          else 'partial' if valid else 'unavailable')
                result = dict(status=status, source='windows_pdh', scope='whole_device',
                    devices=devices, volumes=copy.deepcopy(packet['volumes']),
                    observed_at=self._wall_clock().astimezone(timezone.utc).isoformat(),
                    age_seconds=0.0, reason_code=None if status == 'observed' else
                        'warming_up' if status == 'warming_up' else packet['reason_code'] or 'observations_incomplete',
                    stale=status == 'unavailable')
                self._previous = packet if packet['status'] != 'unavailable' else None
                self._previous_at = finished if self._previous else None
            except DiskObservationError as exc:
                allowed = {'helper_timeout', 'helper_failed', 'helper_output_limit', 'helper_unavailable',
                           'helper_cleanup_failed', 'helper_cleanup_pending', 'platform_unsupported', 'invalid_request',
                           'helper_input_limit', 'helper_output_invalid', 'clock_unavailable'}
                result = _unavailable(str(exc) if str(exc) in allowed else 'helper_unavailable')
                self._previous, self._previous_at = None, None
                finished = self._clock()
                if type(finished) not in (int, float) or not math.isfinite(finished) or finished < now:
                    finished = now
            except Exception:
                result = _unavailable('helper_unavailable')
                self._previous, self._previous_at = None, None
                finished = self._clock()
                if type(finished) not in (int, float) or not math.isfinite(finished) or finished < now:
                    finished = now
            self._cached, self._sampled_at = result, finished
            self._cache_seconds = BACKOFF_SECONDS if result['status'] == 'unavailable' else CACHE_SECONDS
            return self._public_snapshot(result)
