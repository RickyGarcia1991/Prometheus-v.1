"""WinMM ABI/buffer ownership, exercised with a driver-boundary simulator."""
import ctypes
from types import SimpleNamespace

import pytest

from prometheus_assistant import continuous_capture as capture


class Function:
    def __init__(self, handler):
        self.handler = handler

    def __call__(self, *args):
        return self.handler(*args)


class Driver:
    def __init__(self, *, fail=None):
        self.calls = []
        self.headers = []
        self.fail = fail
        self.started = False
        for name in ('Open', 'PrepareHeader', 'AddBuffer', 'Start', 'Stop', 'Reset', 'UnprepareHeader', 'Close'):
            setattr(self, 'waveIn'+name, Function(lambda *args, name=name: self.call(name, *args)))

    def call(self, name, *args):
        self.calls.append(name)
        if name == self.fail:
            return 1
        if name == 'Open':
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
            format = ctypes.cast(args[2], ctypes.POINTER(capture._Format)).contents
            assert (format.tag, format.channels, format.rate, format.bits, format.extra) == (1, 1, 16000, 16, 0)
        if name in {'PrepareHeader', 'AddBuffer', 'UnprepareHeader'}:
            header = ctypes.cast(args[1], ctypes.POINTER(capture._Header)).contents
            assert args[2] == ctypes.sizeof(capture._Header)
            if name == 'PrepareHeader':
                self.headers.append(header)
                header.flags = 2
            elif name == 'AddBuffer':
                header.flags = 2 | 16
            else:
                assert not header.flags & 16, 'Never unprepare a driver-owned buffer'
                header.flags = 0
        if name == 'Start':
            self.started = True
        if name in {'Stop', 'Reset', 'Close'}:
            self.started = False
        if name == 'Reset':
            for header in self.headers:
                header.flags = 2 | 1
        return 0

    def fill(self, index, data):
        header = self.headers[index]
        assert len(data) <= header.length
        ctypes.memmove(header.data, data, len(data))
        header.recorded = len(data)
        header.flags = 2 | 1


@pytest.fixture
def install(monkeypatch):
    monkeypatch.setattr(capture, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(capture, '_failed_devices', [])
    def create(**kwargs):
        driver = Driver(**kwargs)
        monkeypatch.setattr(ctypes, 'WinDLL', lambda name: driver, raising=False)
        return driver
    return create


def test_windows_structure_layout_is_correct_on_current_pointer_width():
    assert ctypes.sizeof(capture._Format) == 18
    assert ctypes.sizeof(capture._Header) == (48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32)
    assert capture._Header.recorded.offset == ctypes.sizeof(ctypes.c_void_p)+4


def test_stream_reuses_bounded_buffers_and_retains_manual_final_partial(install):
    driver = install()
    with capture._WaveInput() as device:
        assert driver.started and len(driver.headers) == 12
        assert sum(header.length for header in driver.headers) == 30720
        for index in range(30):
            pcm = b'\x34\x12'*1280
            driver.fill(index % 12, pcm)
            assert device.read() == pcm
            assert device.read() == b''
        driver.fill(30 % 12, b'\x21\x00'*17)
        assert device.finish() == b'\x21\x00'*17
        assert not driver.started
    assert driver.calls[-1] == 'Close'
    assert driver.calls.count('PrepareHeader') == driver.calls.count('UnprepareHeader') == 12
    assert not device.buffers and not device.handle.value


@pytest.mark.parametrize('failure', ['PrepareHeader', 'AddBuffer', 'Start'])
def test_partial_device_initialization_always_closes_and_unprepares(install, failure):
    driver = install(fail=failure)
    with pytest.raises(ValueError, match='operation failed'):
        with capture._WaveInput():
            pytest.fail('Initialization should fail')
    assert driver.calls[-1] == 'Close' and not driver.started
    assert driver.calls.count('UnprepareHeader') == len(driver.headers)


def test_processing_failure_stops_device_and_releases_every_buffer(install):
    driver = install()
    with pytest.raises(RuntimeError, match='disk full'):
        with capture._WaveInput():
            raise RuntimeError('disk full')
    assert not driver.started and driver.calls[-1] == 'Close'
    assert driver.calls.count('UnprepareHeader') == 12


def test_unclosed_driver_buffers_stay_owned_and_prevent_another_capture(install):
    driver = install(fail='Close')
    with pytest.raises(ValueError, match='cleanup failed'):
        with capture._WaveInput():
            pass
    assert not driver.started and len(capture._failed_devices) == 1
    before = driver.calls.copy()
    with pytest.raises(ValueError, match='restart Prometheus'):
        capture._WaveInput()
    assert driver.calls == before
