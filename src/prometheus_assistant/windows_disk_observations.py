"""One-shot Windows disk metadata and raw PDH observations.

Run this fixed helper in an owned child with a parent-enforced timeout. Native
calls are synchronous and cannot be interrupted by a Python deadline. This
module creates no threads, retains no handles, and changes no counter settings.
Raw observations describe a device, never a job or calibrated disk pressure.
"""
from __future__ import annotations

import ctypes as c
from dataclasses import dataclass
import hashlib
import json
import ntpath
import os
import re
import struct
import sys


MAX_INPUT_BYTES = 8192
MAX_OUTPUT_BYTES = 32768
MAX_NATIVE_BYTES = 65536
MAX_INSTANCES = 64
MAX_PATH_CHARS = 4096
PDH_MORE_DATA = 0x800007D2
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x560000
IOCTL_STORAGE_GET_DEVICE_NUMBER_EX = 0x2D1084
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
# No performance-enabling IOCTLs are used. These three metadata requests are
# FILE_ANY_ACCESS, METHOD_BUFFERED, with no mutation payload.
_IOCTLS = {IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
           IOCTL_STORAGE_GET_DEVICE_NUMBER_EX, IOCTL_STORAGE_QUERY_PROPERTY}
_BUSES = {3, 4, 7, 10, 11, 12, 13, 17, 19}  # ATA/1394/USB/SAS/SATA/SD/MMC/NVMe/UFS
COUNTERS = {
    'latency': ('Avg. Disk sec/Transfer', 805438464),
    'idle': ('% Idle Time', 542573824),
    'queue': ('Current Disk Queue Length', 65536),
    'transfers': ('Disk Transfers/sec', 272696320),
}
_U32 = c.c_uint32
_PTR = c.c_void_p
_GUID_PATH = re.compile(r'\\\\\?\\Volume\{[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}\\')
_INSTANCE = re.compile(r'(0|[1-9][0-9]{0,9})(?: [A-Za-z]:)*')


class _Unavailable(Exception):
    """Only constant, non-sensitive reason codes cross the helper boundary."""


class _Raw(c.Structure):
    _fields_ = [('status', _U32), ('time_low', _U32), ('time_high', _U32),
                ('first', c.c_int64), ('second', c.c_int64), ('multi', _U32)]


class _Item(c.Structure):
    # Use a pointer rather than c_wchar_p: validate buffer bounds before reading.
    _fields_ = [('name', _PTR), ('raw', _Raw)]


@dataclass(frozen=True)
class _Identity:
    volume: str
    guid: bytes
    disk: int
    partition: int
    offset: int
    length: int
    bus: int
    flags: int

    @property
    def device_id(self):
        return _digest(['windows-disk-v1', self.guid.hex()])

    @property
    def volume_id(self):
        return _digest(['windows-volume-v1', self.volume.lower()])

    @property
    def mapping_id(self):
        return _digest([self.volume_id, self.device_id, self.disk, self.partition,
                        self.offset, self.length, self.bus, self.flags])


def _digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def _roots(value):
    if (type(value) is not dict or 'data' not in value
            or not set(value) <= {'data', 'project'}):
        raise _Unavailable('invalid_request')
    result = {}
    for role in ('data', 'project'):
        if role not in value:
            continue
        path = value[role]
        if (type(path) is not str or not 3 <= len(path) <= MAX_PATH_CHARS
                or not re.match(r'^[A-Za-z]:[\\/]', path)
                or any(ord(char) < 32 or char in '*?"<>|' for char in path)
                or ':' in path[2:] or any(part in ('.', '..')
                                         for part in re.split(r'[\\/]', path[3:]))):
            raise _Unavailable('invalid_request')
        result[role] = ntpath.normpath(path)
    return result


def _device_number(data):
    if len(data) != 40:
        raise _Unavailable('identity_unavailable')
    version, size, flags, kind, number, guid, partition = struct.unpack('<IIIII16sI', data)
    if (version != 40 or size != 40 or flags not in (0, 4) or kind != 7
            or number == 0xFFFFFFFF or not any(guid)):
        raise _Unavailable('identity_unavailable')
    return number, guid, partition, flags


def _extent(data):
    if len(data) != 32 or struct.unpack_from('<I', data)[0] != 1:
        raise _Unavailable('mapping_unsupported')
    number, offset, length = struct.unpack_from('<I4xqq', data, 8)
    if number == 0xFFFFFFFF or offset < 0 or length <= 0:
        raise _Unavailable('mapping_unsupported')
    return number, offset, length


def _bus(data):
    if len(data) < 36:
        raise _Unavailable('topology_unsupported')
    version, size = struct.unpack_from('<II', data)
    bus, extra = struct.unpack_from('<II', data, 28)
    if (version < 36 or not version <= size <= len(data) or extra > size - 36
            or data[8] != 0 or bus not in _BUSES):
        raise _Unavailable('topology_unsupported')
    return bus


def _bind(dll, name, arguments, result):
    function = getattr(dll, name)
    function.argtypes, function.restype = arguments, result


class _Native:
    def __init__(self):
        if os.name != 'nt':
            raise _Unavailable('platform_unsupported')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.p = c.WinDLL('pdh')
        for name, args, result in (
            ('GetFileAttributesW', [c.c_wchar_p], _U32),
            ('GetVolumePathNameW', [c.c_wchar_p, c.c_wchar_p, _U32], c.c_int32),
            ('GetVolumeNameForVolumeMountPointW', [c.c_wchar_p, c.c_wchar_p, _U32], c.c_int32),
            ('GetDriveTypeW', [c.c_wchar_p], _U32),
            ('CreateFileW', [c.c_wchar_p, _U32, _U32, _PTR, _U32, _U32, _PTR], _PTR),
            ('DeviceIoControl', [_PTR, _U32, _PTR, _U32, _PTR, _U32, c.POINTER(_U32), _PTR], c.c_int32),
            ('CloseHandle', [_PTR], c.c_int32),
        ):
            _bind(self.k, name, args, result)
        for name, args in (
            ('PdhOpenQueryW', [c.c_wchar_p, c.c_size_t, c.POINTER(_PTR)]),
            ('PdhAddEnglishCounterW', [_PTR, c.c_wchar_p, c.c_size_t, c.POINTER(_PTR)]),
            ('PdhCollectQueryData', [_PTR]),
            ('PdhGetCounterInfoW', [_PTR, c.c_int32, c.POINTER(_U32), _PTR]),
            ('PdhGetCounterTimeBase', [_PTR, c.POINTER(c.c_int64)]),
            ('PdhGetRawCounterArrayW', [_PTR, c.POINTER(_U32), c.POINTER(_U32), _PTR]),
            ('PdhCloseQuery', [_PTR]),
        ):
            _bind(self.p, name, args, _U32)

    def _open(self, path):
        handle = self.k.CreateFileW(path, 0, 7, None, 3, 0, None)
        if handle in (None, 0, c.c_void_p(-1).value):
            raise _Unavailable('metadata_unavailable')
        return handle

    def _close(self, handle):
        if not self.k.CloseHandle(handle):
            raise _Unavailable('cleanup_failed')

    def _ioctl(self, handle, code, capacity, payload=None):
        if code not in _IOCTLS or not 1 <= capacity <= MAX_NATIVE_BYTES:
            raise _Unavailable('metadata_unavailable')
        output, used = c.create_string_buffer(capacity), _U32()
        input_buffer = c.create_string_buffer(payload) if payload else None
        if not self.k.DeviceIoControl(handle, code, input_buffer, len(payload or b''),
                                      output, capacity, c.byref(used), None):
            raise _Unavailable('metadata_unavailable')
        if not 0 < used.value <= capacity:
            raise _Unavailable('metadata_unavailable')
        return output.raw[:used.value]

    def resolve(self, path):
        attributes = self.k.GetFileAttributesW(path)
        if attributes == 0xFFFFFFFF or not attributes & 0x10:
            raise _Unavailable('directory_unavailable')
        mount, volume = c.create_unicode_buffer(MAX_PATH_CHARS + 1), c.create_unicode_buffer(64)
        if not self.k.GetVolumePathNameW(path, mount, len(mount)):
            raise _Unavailable('volume_unavailable')
        if (not re.match(r'^[A-Za-z]:\\', mount.value)
                or not mount.value.endswith('\\') or self.k.GetDriveTypeW(mount.value) not in (2, 3)):
            raise _Unavailable('topology_unsupported')
        if (not self.k.GetVolumeNameForVolumeMountPointW(mount.value, volume, len(volume))
                or not _GUID_PATH.fullmatch(volume.value)):
            raise _Unavailable('volume_unavailable')
        handle = self._open(volume.value[:-1])
        try:
            disk, offset, length = _extent(self._ioctl(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, 32))
            number, guid, partition, flags = _device_number(
                self._ioctl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER_EX, 40))
            if number != disk or partition in (0, 0xFFFFFFFF):
                raise _Unavailable('mapping_unsupported')
        finally:
            self._close(handle)
        handle = self._open('\\\\.\\PhysicalDrive' + str(disk))
        try:
            physical = _device_number(self._ioctl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER_EX, 40))
            if physical[:2] != (disk, guid) or physical[2] not in (0, 0xFFFFFFFF) or physical[3] != flags:
                raise _Unavailable('mapping_unsupported')
            # StorageDeviceProperty / PropertyStandardQuery; no serial is decoded.
            bus = _bus(self._ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, 4096, b'\0' * 12))
        finally:
            self._close(handle)
        return _Identity(volume.value.lower(), guid, disk, partition, offset, length, bus, flags)

    def _counter_metadata(self, counter, expected_type):
        size = _U32()
        if self.p.PdhGetCounterInfoW(counter, False, c.byref(size), None) != PDH_MORE_DATA:
            raise _Unavailable('counter_metadata_unavailable')
        if not 24 <= size.value <= MAX_NATIVE_BYTES:
            raise _Unavailable('counter_metadata_unavailable')
        capacity = size.value
        buffer = c.create_string_buffer(capacity)
        if self.p.PdhGetCounterInfoW(counter, False, c.byref(size), buffer):
            raise _Unavailable('counter_metadata_unavailable')
        length, kind, _, _, scale, default_scale = struct.unpack_from('<IIIIii', buffer.raw)
        if not 24 <= length <= size.value <= capacity or kind != expected_type:
            raise _Unavailable('counter_metadata_unavailable')
        if not -7 <= scale <= 7 or not -7 <= default_scale <= 7:
            raise _Unavailable('counter_metadata_unavailable')
        time_base = None
        if kind != COUNTERS['queue'][1]:
            base = c.c_int64()
            if self.p.PdhGetCounterTimeBase(counter, c.byref(base)) or base.value <= 0:
                raise _Unavailable('counter_metadata_unavailable')
            time_base = base.value
        return {'counter_type': kind, 'time_base': time_base,
                'scale': scale, 'default_scale': default_scale}

    def _counter_rows(self, counter):
        size, count = _U32(), _U32()
        if self.p.PdhGetRawCounterArrayW(counter, c.byref(size), c.byref(count), None) != PDH_MORE_DATA:
            raise _Unavailable('counter_unavailable')
        if not 1 <= count.value <= MAX_INSTANCES or not c.sizeof(_Item) * count.value <= size.value <= MAX_NATIVE_BYTES:
            raise _Unavailable('counter_buffer_invalid')
        capacity = size.value
        buffer = c.create_string_buffer(capacity)
        if self.p.PdhGetRawCounterArrayW(counter, c.byref(size), c.byref(count), buffer):
            raise _Unavailable('counter_unavailable')
        if not 1 <= count.value <= MAX_INSTANCES or not c.sizeof(_Item) * count.value <= size.value <= capacity:
            raise _Unavailable('counter_buffer_invalid')
        rows = c.cast(buffer, c.POINTER(_Item))
        start, end = c.addressof(buffer), c.addressof(buffer) + size.value
        output = []
        for index in range(count.value):
            row = rows[index]
            address = row.name or 0
            if address < start + count.value * c.sizeof(_Item) or address >= end or (address - start) % 2:
                raise _Unavailable('counter_buffer_invalid')
            raw_name = c.string_at(address, min(end - address, 258))
            terminator = next((i for i in range(0, len(raw_name) - 1, 2) if raw_name[i:i + 2] == b'\0\0'), None)
            if terminator is None:
                raise _Unavailable('counter_buffer_invalid')
            try:
                name = raw_name[:terminator].decode('utf-16-le')
            except UnicodeError:
                raise _Unavailable('counter_buffer_invalid') from None
            output.append((name, _Raw.from_buffer_copy(row.raw)))
        return output

    def sample(self, disks):
        result = {disk: {} for disk in disks}
        query = _PTR()
        status = self.p.PdhOpenQueryW(None, 0, c.byref(query))
        if status or not query.value:
            raise _Unavailable('counter_unavailable')
        try:
            counters = {}
            for metric, (name, _) in COUNTERS.items():
                counter = _PTR()
                status = self.p.PdhAddEnglishCounterW(query, '\\PhysicalDisk(*)\\' + name, 0, c.byref(counter))
                if status or not counter.value:
                    for disk in disks:
                        result[disk][metric] = _missing('counter_unavailable')
                else:
                    counters[metric] = counter
            if not counters or self.p.PdhCollectQueryData(query):
                raise _Unavailable('counter_unavailable')
            for metric, counter in counters.items():
                try:
                    metadata = self._counter_metadata(counter, COUNTERS[metric][1])
                    rows = self._counter_rows(counter)
                    for disk in disks:
                        matches = [(name, raw) for name, raw in rows if _INSTANCE.fullmatch(name)
                                   and int(name.split(' ')[0]) == disk]
                        if len(matches) != 1:
                            result[disk][metric] = _missing('instance_unavailable')
                            continue
                        name, raw = matches[0]
                        timestamp = raw.time_low | (raw.time_high << 32)
                        if raw.status not in (0, 1) or raw.first < 0 or raw.second < 0 or not timestamp or raw.multi > 1:
                            result[disk][metric] = _missing('counter_data_invalid')
                            continue
                        result[disk][metric] = dict(status='raw', reason_code=None,
                            instance_id=_digest(['physicaldisk-instance-v1', name]),
                            cstatus=raw.status, timestamp_100ns=timestamp, first=raw.first,
                            second=raw.second, multi_count=raw.multi, **metadata)
                except _Unavailable as exc:
                    for disk in disks:
                        result[disk][metric] = _missing(str(exc))
            return result
        finally:
            if self.p.PdhCloseQuery(query):
                raise _Unavailable('cleanup_failed')


def _missing(reason):
    return {'status': 'unavailable', 'reason_code': reason}


def _result(reason=None):
    return dict(schema_version=1, status='unavailable', reason_code=reason,
                source='windows_pdh_raw', scope='device', volumes=[], devices=[])


def collect_disk_observations(roots, *, native=None):
    """Collect once. ``roots`` are trusted app-selected directories, not commands.

    ``native`` is a private test seam. Do not call in an app request thread;
    use the fixed module helper with a parent deadline. No prior sample/state is
    accepted. A status of ``raw`` says nothing about health or admission.
    """
    try:
        paths = _roots(roots)
    except _Unavailable as exc:
        return _result(str(exc))
    try:
        native = native if native is not None else _Native()
    except _Unavailable as exc:
        return _result(str(exc))
    except (OSError, AttributeError):
        return _result('native_unavailable')
    result, before, failures = _result(), {}, {}
    for role, path in paths.items():
        try:
            before[role] = native.resolve(path)
        except _Unavailable as exc:
            failures[role] = str(exc)
        except (OSError, ValueError, AttributeError):
            failures[role] = 'metadata_unavailable'
    # Conflicting GUID/number pairs cannot safely select a PDH instance.
    for role, identity in before.items():
        if any((identity.disk == other.disk) != (identity.guid == other.guid)
               for other in before.values()):
            failures[role] = 'mapping_ambiguous'
    disks = {item.disk for role, item in before.items() if role not in failures}
    readings = {}
    if disks:
        try:
            readings = native.sample(disks)
        except _Unavailable as exc:
            readings = {disk: {key: _missing(str(exc)) for key in COUNTERS} for disk in disks}
        except (OSError, ValueError, AttributeError):
            readings = {disk: {key: _missing('counter_unavailable') for key in COUNTERS} for disk in disks}
    for role, identity in before.items():
        if role in failures:
            continue
        try:
            if native.resolve(paths[role]) != identity:
                failures[role] = 'identity_changed'
        except (OSError, ValueError, AttributeError, _Unavailable):
            failures[role] = 'identity_changed'
    emitted = set()
    for role in paths:
        if role in failures:
            result['volumes'].append(dict(role=role, **_missing(failures[role])))
            continue
        identity = before[role]
        result['volumes'].append(dict(role=role, status='mapped', reason_code=None,
            volume_id=identity.volume_id, device_id=identity.device_id, mapping_id=identity.mapping_id))
        if identity.device_id in emitted:
            continue
        emitted.add(identity.device_id)
        metrics = readings[identity.disk]
        valid = sum(item['status'] == 'raw' for item in metrics.values())
        result['devices'].append(dict(device_id=identity.device_id,
            identity_basis='storage_device_number_ex_hardware_guid',
            status='raw' if valid == len(COUNTERS) else 'partial' if valid else 'unavailable',
            counters=metrics))
    valid = sum(item['status'] in ('raw', 'partial') for item in result['devices'])
    all_raw = all(item['status'] == 'raw' for item in result['devices']) and not failures
    result['status'] = 'raw' if valid and all_raw else 'partial' if valid else 'unavailable'
    result['reason_code'] = None if result['status'] == 'raw' else 'observations_incomplete'
    return result


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def main(argv=None, *, stdin=None, stdout=None):
    """Fixed bounded stdin/stdout protocol; no paths or commands in arguments."""
    argv = sys.argv[1:] if argv is None else argv
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    result = _result('invalid_request')
    try:
        if not argv:
            payload = stdin.read(MAX_INPUT_BYTES + 1)
            if len(payload) <= MAX_INPUT_BYTES:
                request = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_pairs,
                                     parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
                if (type(request) is dict and set(request) == {'schema_version', 'roots'}
                        and type(request['schema_version']) is int and request['schema_version'] == 1):
                    result = collect_disk_observations(request['roots'])
    except (ValueError, UnicodeError, RecursionError, OSError):
        pass
    except Exception:
        # Never print traceback, native names, request paths or system error text.
        result = _result('native_unavailable')
    encoded = json.dumps(result, separators=(',', ':'), ensure_ascii=True)
    if len(encoded.encode('ascii')) > MAX_OUTPUT_BYTES:
        encoded = json.dumps(_result('output_limit'), separators=(',', ':'))
    stdout.write(encoded + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
