"""Native ABI/transport contracts; no disk workload or settings changes."""
import ctypes as c
from dataclasses import replace
import io
import json
import struct

import pytest

from prometheus_assistant import windows_disk_observations as disk


GUID = bytes(range(1, 17))
VOLUME = '\\\\?\\Volume{12345678-1234-1234-1234-123456789012}\\'


def put(pointer, value, kind=disk._U32):
    c.cast(pointer, c.POINTER(kind))[0] = value


def number(*, disk_number=2, guid=GUID, partition=1, flags=0, version=40, size=40, kind=7):
    return struct.pack('<IIIII16sI', version, size, flags, kind, disk_number, guid, partition)


def extent(*, count=1, disk_number=2, offset=1048576, length=1000000000):
    return struct.pack('<I4xI4xqq', count, disk_number, offset, length)


def descriptor(bus=7):
    data = bytearray(40)
    struct.pack_into('<II', data, 0, 40, 40)
    struct.pack_into('<II', data, 28, bus, 0)
    return bytes(data)


class Kernel:
    def __init__(self):
        self.opens, self.closes, self.ioctls = [], [], []
        self.outputs = {
            (101, disk.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS): extent(),
            (101, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX): number(),
            (102, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX): number(partition=0),
            (102, disk.IOCTL_STORAGE_QUERY_PROPERTY): descriptor(),
        }
        self.fail_open = None
        self.fail_ioctl = None
        self.drive_type = 3
        self.attributes = 0x10
        self.close_ok = True

    def GetFileAttributesW(self, path):
        return self.attributes

    def GetVolumePathNameW(self, path, output, capacity):
        assert capacity == disk.MAX_PATH_CHARS + 1
        output.value = 'E:\\'
        return 1

    def GetVolumeNameForVolumeMountPointW(self, mount, output, capacity):
        assert mount == 'E:\\' and capacity == 64
        output.value = VOLUME
        return 1

    def GetDriveTypeW(self, mount):
        return self.drive_type

    def CreateFileW(self, path, access, share, security, disposition, flags, template):
        self.opens.append((path, access, share, security, disposition, flags, template))
        handle = 100 + len(self.opens)
        return c.c_void_p(-1).value if handle == self.fail_open else handle

    def CloseHandle(self, handle):
        self.closes.append(handle)
        return self.close_ok

    def DeviceIoControl(self, handle, code, payload, payload_size, output, capacity, used, overlapped):
        self.ioctls.append((handle, code, payload_size, capacity, overlapped))
        assert code in disk._IOCTLS
        if code == disk.IOCTL_STORAGE_QUERY_PROPERTY:
            assert c.string_at(payload, payload_size) == b'\0' * 12
        else:
            assert payload is None and payload_size == 0
        if (handle, code) == self.fail_ioctl:
            return 0
        value = self.outputs[handle, code]
        c.memmove(output, value, min(len(value), capacity))
        put(used, len(value))
        return 1


class Pdh:
    def __init__(self):
        self.paths, self.closed, self.collects = [], [], 0
        self.collect_status = 0
        self.close_status = 0
        self.add_failure = None
        self.row_status = 0
        self.first = 0
        self.second = 20
        self.multi = 1
        self.timestamp = 12345678
        self.timebase = 10000000
        self.timebase_calls = []
        self.type_override = None
        self.instances = ['0 C:', '2 E:', '_Total']
        self.first_size = None
        self.first_count = None
        self.second_status = 0
        self.second_count = None
        self.bad_name_pointer = False
        self.no_terminator = False
        self.info_size = 64
        self.info_second_size = None
        self.array_calls = 0

    def PdhOpenQueryW(self, source, user_data, output):
        assert source is None and user_data == 0
        put(output, 200, c.c_void_p)
        return 0

    def PdhAddEnglishCounterW(self, query, path, user_data, output):
        self.paths.append(path)
        if self.add_failure and self.add_failure in path:
            return 0xC0000BC8
        put(output, len(self.paths), c.c_void_p)
        return 0

    def PdhCollectQueryData(self, query):
        self.collects += 1
        return self.collect_status

    def PdhCloseQuery(self, query):
        self.closed.append(query.value)
        return self.close_status

    def PdhGetCounterInfoW(self, counter, explain, size, buffer):
        assert not explain
        if buffer is None:
            put(size, self.info_size)
            return disk.PDH_MORE_DATA
        kind = list(disk.COUNTERS.values())[counter.value - 1][1]
        value = struct.pack('<IIIIii', 64, self.type_override or kind, 0, 0, 0, 0)
        c.memmove(buffer, value, len(value))
        put(size, self.info_size if self.info_second_size is None else self.info_second_size)
        return 0

    def PdhGetCounterTimeBase(self, counter, output):
        self.timebase_calls.append(counter.value)
        put(output, self.timebase, c.c_int64)
        return 0

    def PdhGetRawCounterArrayW(self, counter, size, count, buffer):
        self.array_calls += 1
        names = [(name + '\0').encode('utf-16-le') for name in self.instances]
        required = c.sizeof(disk._Item) * len(names) + sum(len(name) for name in names)
        if buffer is None:
            put(size, required if self.first_size is None else self.first_size)
            put(count, len(names) if self.first_count is None else self.first_count)
            return disk.PDH_MORE_DATA
        if self.second_status:
            return self.second_status
        base = c.addressof(buffer)
        pointer = base + c.sizeof(disk._Item) * len(names)
        rows = c.cast(buffer, c.POINTER(disk._Item))
        for index, name in enumerate(names):
            rows[index].name = base - 8 if self.bad_name_pointer else pointer
            payload = b'x' * len(name) if self.no_terminator else name
            c.memmove(pointer, payload, len(name))
            rows[index].raw = disk._Raw(self.row_status, self.timestamp & 0xFFFFFFFF,
                self.timestamp >> 32, self.first, self.second, self.multi)
            pointer += len(name)
        put(size, required)
        put(count, len(names) if self.second_count is None else self.second_count)
        return 0


def native(kernel=None, pdh=None):
    result = disk._Native.__new__(disk._Native)
    result.k, result.p = kernel or Kernel(), pdh or Pdh()
    return result


def identity(**changes):
    return replace(disk._Identity(VOLUME.lower(), GUID, 2, 1, 1048576, 1000000000, 7, 0), **changes)


class Provider:
    def __init__(self):
        self.calls = []
        self.identities = {'E:\\data': identity(), 'E:\\project': identity()}
        self.after = {}
        self.sampled = []
        self.pdh = Pdh()

    def resolve(self, path):
        self.calls.append(path)
        value = self.after.get(path, self.identities.get(path)) if self.sampled else self.identities.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise disk._Unavailable('directory_unavailable')
        return value

    def sample(self, disks):
        self.sampled.append(disks)
        return native(pdh=self.pdh).sample(disks)


def test_metadata_identity_uses_zero_access_and_closes_both_handles():
    probe = native()
    result = probe.resolve('E:\\data')
    assert result == identity()
    assert len(probe.k.opens) == 2
    assert all(call[1:] == (0, 7, None, 3, 0, None) for call in probe.k.opens)
    assert probe.k.opens[0][0] == VOLUME[:-1]
    assert probe.k.opens[1][0] == '\\\\.\\PhysicalDrive2'
    assert probe.k.closes == [101, 102]
    assert {call[1] for call in probe.k.ioctls} == disk._IOCTLS
    assert all(call[-1] is None for call in probe.k.ioctls)


@pytest.mark.parametrize('key', [(101, disk.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS),
    (101, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX),
    (102, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX), (102, disk.IOCTL_STORAGE_QUERY_PROPERTY)])
def test_metadata_failure_closes_every_opened_handle(key):
    probe = native()
    probe.k.fail_ioctl = key
    with pytest.raises(disk._Unavailable):
        probe.resolve('E:\\data')
    assert probe.k.closes == list(range(101, 101 + len(probe.k.opens)))


@pytest.mark.parametrize('changes', [dict(flags=1), dict(flags=2), dict(flags=5), dict(flags=8),
    dict(guid=b'\0' * 16), dict(version=39), dict(size=41), dict(kind=0), dict(disk_number=0xFFFFFFFF)])
def test_random_missing_unknown_or_malformed_device_identity_rejected(changes):
    with pytest.raises(disk._Unavailable, match='identity_unavailable'):
        disk._device_number(number(**changes))


def test_page83_identity_supported_and_number_is_not_stable_identity():
    assert disk._device_number(number(flags=4))[3] == 4
    before, after = identity(), identity(disk=12)
    assert before.device_id == after.device_id
    assert before.mapping_id != after.mapping_id


@pytest.mark.parametrize('value', [b'', extent(count=0), extent(count=2), extent(offset=-1),
    extent(length=0), extent(disk_number=0xFFFFFFFF), extent() + b'extra'])
def test_ambiguous_or_invalid_extent_rejected(value):
    with pytest.raises(disk._Unavailable):
        disk._extent(value)


@pytest.mark.parametrize('bus', [0, 1, 6, 8, 9, 14, 15, 16, 20, 255])
def test_unknown_network_virtual_or_aggregate_bus_rejected(bus):
    with pytest.raises(disk._Unavailable, match='topology_unsupported'):
        disk._bus(descriptor(bus))


@pytest.mark.parametrize('slot,value', [(28, 14), (0, 100), (4, 100), (32, 200)])
def test_malformed_storage_descriptor_cannot_qualify(slot, value):
    data = bytearray(descriptor())
    struct.pack_into('<I', data, slot, value)
    with pytest.raises(disk._Unavailable):
        disk._bus(bytes(data))


@pytest.mark.parametrize('key,value', [((101, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX), number(disk_number=3)),
    ((101, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX), number(partition=0)),
    ((102, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX), number(partition=1)),
    ((102, disk.IOCTL_STORAGE_GET_DEVICE_NUMBER_EX), number(guid=b'X' * 16, partition=0))])
def test_volume_and_physical_identity_must_agree(key, value):
    probe = native()
    probe.k.outputs[key] = value
    with pytest.raises(disk._Unavailable, match='mapping_unsupported'):
        probe.resolve('E:\\data')
    assert len(probe.k.opens) == len(probe.k.closes)


def test_missing_directory_and_network_mapping_do_not_open_device():
    for attr, drive in ((0xFFFFFFFF, 3), (0, 3), (0x10, 4)):
        probe = native()
        probe.k.attributes, probe.k.drive_type = attr, drive
        with pytest.raises(disk._Unavailable):
            probe.resolve('E:\\data')
        assert probe.k.opens == []


def test_open_and_close_failure_explicit():
    probe = native()
    probe.k.fail_open = 102
    with pytest.raises(disk._Unavailable, match='metadata_unavailable'):
        probe.resolve('E:\\data')
    assert probe.k.closes == [101]
    probe = native()
    probe.k.close_ok = False
    with pytest.raises(disk._Unavailable, match='cleanup_failed'):
        probe.resolve('E:\\data')


def test_fixed_counters_one_collection_and_raw_zero_is_actual_observation():
    probe = native()
    result = probe.sample({2})
    assert probe.p.paths == ['\\PhysicalDisk(*)\\' + item[0] for item in disk.COUNTERS.values()]
    assert probe.p.collects == 1 and probe.p.closed == [200]
    assert set(result[2]) == set(disk.COUNTERS)
    queue = result[2]['queue']
    assert queue['status'] == 'raw' and queue['first'] == 0
    assert queue['counter_type'] == 65536 and queue['time_base'] is None
    assert queue['timestamp_100ns'] == 12345678
    assert 'percent' not in queue and 'latency_ms' not in queue


@pytest.mark.parametrize('attribute,value,reason', [
    ('row_status', 0xC0000BC6, 'counter_data_invalid'), ('first', -1, 'counter_data_invalid'),
    ('second', -1, 'counter_data_invalid'), ('timestamp', 0, 'counter_data_invalid'),
    ('multi', 2, 'counter_data_invalid'),
    ('type_override', 123, 'counter_metadata_unavailable'),
    ('info_size', 65537, 'counter_metadata_unavailable'),
    ('info_second_size', 65537, 'counter_metadata_unavailable'),
    ('first_size', 65537, 'counter_buffer_invalid'), ('first_size', 1, 'counter_buffer_invalid'),
    ('first_count', 65, 'counter_buffer_invalid'), ('first_count', 0, 'counter_buffer_invalid'),
    ('second_count', 65, 'counter_buffer_invalid'),
    ('bad_name_pointer', True, 'counter_buffer_invalid'),
    ('no_terminator', True, 'counter_buffer_invalid'),
    ('second_status', disk.PDH_MORE_DATA, 'counter_unavailable'),
])
def test_bad_native_counter_data_remains_unknown_and_query_closed(attribute, value, reason):
    probe = native()
    setattr(probe.p, attribute, value)
    result = probe.sample({2})
    assert all(item == disk._missing(reason) for item in result[2].values())
    assert probe.p.closed == [200]
    assert probe.p.array_calls <= 8  # no size-race retry loop


def test_instantaneous_queue_does_not_request_a_timebase():
    probe = native()
    result = probe.sample({2})
    assert probe.p.timebase_calls == [1, 2, 4]
    assert result[2]['queue']['time_base'] is None


def test_invalid_timebase_rejects_rates_but_preserves_instantaneous_queue():
    probe = native()
    probe.p.timebase = 0
    result = probe.sample({2})
    assert result[2]['queue']['status'] == 'raw'
    assert all(result[2][key] == disk._missing('counter_metadata_unavailable')
               for key in ('latency', 'idle', 'transfers'))


@pytest.mark.parametrize('instances', [['_Total'], ['12 E:', '0 C:'], ['2 E:', '2 D:'], ['02 E:'], ['2 E:#1']])
def test_no_total_prefix_guess_duplicate_or_unrecognized_instance(instances):
    probe = native()
    probe.p.instances = instances
    result = probe.sample({2})
    assert all(item == disk._missing('instance_unavailable') for item in result[2].values())


def test_cstatus_new_data_accepted_and_one_missing_counter_is_partial():
    provider = Provider()
    provider.pdh.row_status = 1
    provider.pdh.add_failure = '% Idle Time'
    result = disk.collect_disk_observations({'data': 'E:/data'}, native=provider)
    assert result['status'] == 'partial'
    values = result['devices'][0]['counters']
    assert values['idle'] == disk._missing('counter_unavailable')
    assert values['queue']['cstatus'] == 1


@pytest.mark.parametrize('attribute,value,reason', [('collect_status', 5, 'counter_unavailable'),
    ('close_status', 5, 'cleanup_failed')])
def test_collect_and_close_errors_discard_raw_results(attribute, value, reason):
    provider = Provider()
    setattr(provider.pdh, attribute, value)
    result = disk.collect_disk_observations({'data': 'E:/data'}, native=provider)
    assert result['status'] == 'unavailable'
    assert all(item == disk._missing(reason) for item in result['devices'][0]['counters'].values())
    assert provider.pdh.closed == [200]


def test_same_disk_two_volumes_deduplicated_without_leaking_paths_or_guids():
    provider = Provider()
    provider.identities['E:\\project'] = identity(volume=VOLUME.replace('12345678-', '87654321-'), partition=2)
    result = disk.collect_disk_observations({'data': 'E:/data', 'project': 'E:/project'}, native=provider)
    assert result['status'] == 'raw'
    assert provider.sampled == [{2}]
    assert len(provider.calls) == 4 and len(result['devices']) == 1
    assert result['volumes'][0]['volume_id'] != result['volumes'][1]['volume_id']
    assert result['volumes'][0]['device_id'] == result['volumes'][1]['device_id']
    encoded = json.dumps(result)
    assert all(secret not in encoded for secret in ('E:', 'project\\', VOLUME, GUID.hex(), 'PhysicalDrive'))
    assert len(encoded) < disk.MAX_OUTPUT_BYTES


@pytest.mark.parametrize('change', [dict(guid=b'X' * 16), dict(volume=VOLUME.replace('12345678-', '87654321-')),
    dict(disk=3), dict(partition=2), dict(offset=2), dict(length=4), dict(bus=17)])
def test_identity_revalidated_after_collection_and_changed_mapping_discarded(change):
    provider = Provider()
    provider.after['E:\\data'] = identity(**change)
    result = disk.collect_disk_observations({'data': 'E:/data'}, native=provider)
    assert result['status'] == 'unavailable' and result['devices'] == []
    assert result['volumes'] == [{'role': 'data', **disk._missing('identity_changed')}]


def test_disconnected_volume_does_not_discard_other_independently_revalidated_disk():
    provider = Provider()
    provider.identities['E:\\project'] = identity(disk=0, guid=b'X' * 16)
    provider.after['E:\\data'] = OSError('secret path or hardware identifier')
    result = disk.collect_disk_observations({'data': 'E:/data', 'project': 'E:/project'}, native=provider)
    assert result['status'] == 'partial' and len(result['devices']) == 1
    assert result['volumes'][0]['reason_code'] == 'identity_changed'
    assert result['volumes'][1]['status'] == 'mapped'
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('changed', [identity(disk=0), identity(guid=b'X' * 16)])
def test_conflicting_same_number_or_guid_does_not_collect(changed):
    provider = Provider()
    provider.identities['E:\\project'] = changed
    result = disk.collect_disk_observations({'data': 'E:/data', 'project': 'E:/project'}, native=provider)
    assert provider.sampled == [] and result['devices'] == []
    assert all(row['reason_code'] == 'mapping_ambiguous' for row in result['volumes'])


@pytest.mark.parametrize('roots', [None, {}, {'project': 'E:/'}, {'data': 'E:/', 'other': 'C:/'},
    {'data': '../secret'}, {'data': 'E:relative'}, {'data': '\\\\server\\share'},
    {'data': '\\\\?\\E:\\'}, {'data': 'E:/a:stream'}, {'data': 'E:/a/../b'},
    {'data': 'E:/*'}, {'data': 'E:/secret\n'}, {'data': 'E:/' + 'a' * 4096}, {'data': True}])
def test_invalid_roots_do_not_touch_native(roots):
    provider = Provider()
    result = disk.collect_disk_observations(roots, native=provider)
    assert result['reason_code'] == 'invalid_request'
    assert provider.calls == [] and provider.sampled == []


def test_missing_native_api_and_platform_return_unavailable(monkeypatch):
    monkeypatch.setattr(disk, '_Native', lambda: (_ for _ in ()).throw(OSError('sensitive diagnostic')))
    assert disk.collect_disk_observations({'data': 'C:/'})['reason_code'] == 'native_unavailable'


def cli(payload, monkeypatch, *, argv=None, response=None):
    calls = []
    def collect(roots):
        calls.append(roots)
        return response if response is not None else disk._result('test_unavailable')
    monkeypatch.setattr(disk, 'collect_disk_observations', collect)
    output = io.StringIO()
    assert disk.main([] if argv is None else argv, stdin=io.BytesIO(payload), stdout=output) == 0
    return json.loads(output.getvalue()), calls


@pytest.mark.parametrize('payload', [b'', b'no json', b'{}', b'[]', b'{"schema_version":true,"roots":{}}',
    b'{"schema_version":1,"roots":{},"command":"execute"}',
    b'{"schema_version":1,"schema_version":1,"roots":{}}',
    b'{"schema_version":1,"roots":{"data":"C:/","data":"E:/"}}',
    b'{"schema_version":NaN,"roots":{}}', b'\xff', b' ' * (disk.MAX_INPUT_BYTES + 1)])
def test_helper_rejects_malformed_oversize_duplicate_or_unknown_fields(payload, monkeypatch):
    result, calls = cli(payload, monkeypatch)
    assert result['reason_code'] == 'invalid_request' and calls == []


def test_helper_no_arguments_and_valid_json_only(monkeypatch):
    payload = b'{"schema_version":1,"roots":{"data":"E:/data"}}'
    result, calls = cli(payload, monkeypatch, argv=['--path', 'E:/secret'])
    assert result['reason_code'] == 'invalid_request' and calls == []
    result, calls = cli(payload, monkeypatch)
    assert result['reason_code'] == 'test_unavailable' and calls == [{'data': 'E:/data'}]


def test_helper_bounds_output_and_does_not_echo_exception(monkeypatch):
    payload = b'{"schema_version":1,"roots":{"data":"E:/data"}}'
    result, _ = cli(payload, monkeypatch, response={'oversize': 'x' * disk.MAX_OUTPUT_BYTES})
    assert result['reason_code'] == 'output_limit'
    monkeypatch.setattr(disk, 'collect_disk_observations', lambda _: (_ for _ in ()).throw(RuntimeError('secret')))
    output = io.StringIO()
    disk.main([], stdin=io.BytesIO(payload), stdout=output)
    assert 'secret' not in output.getvalue()
    assert json.loads(output.getvalue())['reason_code'] == 'native_unavailable'
