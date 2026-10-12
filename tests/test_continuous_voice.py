"""Continuous dictation at the PCM/device boundary, without opening a microphone."""
import struct
import threading
import wave

import pytest

from prometheus_assistant import continuous_capture as capture
from prometheus_assistant.ollama import GenerationCancelled


class Clock:
    def __init__(self):
        self.value = 0.

    def __call__(self):
        return self.value


class Device:
    def __init__(self, clock, chunks, *, tick=.1):
        self.clock, self.chunks, self.tick = clock, iter(chunks), tick
        self.opened = self.closed = False

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self):
        self.clock.value += self.tick
        return next(self.chunks, b'')

    def finish(self):
        return b''


def pcm(level, seconds=.1):
    return struct.pack('<h', level) * round(16000*seconds)


@pytest.fixture
def runtime(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(capture.time, 'monotonic', clock)
    devices = []
    def install(chunks, **kwargs):
        device = Device(clock, chunks, **kwargs)
        devices.append(device)
        monkeypatch.setattr(capture, '_WaveInput', lambda: device)
        return device
    yield clock, install
    assert all(device.closed for device in devices if device.opened)


def test_speech_continues_beyond_thirty_seconds_and_preserves_short_pause(tmp_path, runtime):
    _, install = runtime
    install([pcm(1200)]*400 + [pcm(0)]*12 + [pcm(1800)]*50 + [pcm(0)]*25)
    levels = []
    path = tmp_path/'speech.wav'
    capture.record_continuous(path, on_level=lambda level, elapsed: levels.append((level, elapsed)))
    with wave.open(str(path), 'rb') as audio:
        assert audio.getframerate() == 16000 and audio.getnchannels() == 1
        assert 48 <= audio.getnframes()/16000 <= 48.3
    assert any(level > 0 for level, _ in levels)
    assert levels[-1][1] > 48


def test_manual_finish_keeps_audio_and_does_not_cancel(tmp_path, runtime):
    _, install = runtime
    device = install([pcm(1000)]*200)
    finish = threading.Event()
    cancel = threading.Event()
    def update(level, elapsed):
        if elapsed >= 12:
            finish.set()
    path = tmp_path/'manual.wav'
    capture.record_continuous(path, finish_event=finish, cancel_event=cancel, on_level=update)
    with wave.open(str(path), 'rb') as audio:
        assert 12 <= audio.getnframes()/16000 < 12.3
    assert not cancel.is_set() and device.closed


@pytest.mark.parametrize('reason', ['cancel', 'lease', 'silence', 'size', 'stalled'])
def test_failures_close_microphone_and_remove_partial_audio(tmp_path, runtime, monkeypatch, reason):
    clock, install = runtime
    install([pcm(0 if reason == 'silence' else 1000)]*400 if reason != 'stalled' else [])
    event = threading.Event()
    if reason == 'size':
        monkeypatch.setattr(capture, 'MAX_AUDIO_BYTES', 64044)
    def update(level, elapsed):
        if reason == 'cancel' and elapsed >= 1:
            event.set()
    path = tmp_path/'discard.wav'
    error = GenerationCancelled if reason in {'cancel', 'lease'} else ValueError
    with pytest.raises(error):
        capture.record_continuous(path, cancel_event=event, on_level=update,
                                  lease_alive=lambda: reason != 'lease' or clock() < 2)
    assert not path.exists()


def test_short_noise_is_not_accepted_as_speech(tmp_path, runtime):
    _, install = runtime
    install([pcm(1000)] + [pcm(0)]*350)
    with pytest.raises(ValueError, match='No audible speech'):
        capture.record_continuous(tmp_path/'noise.wav')


def test_cancelled_or_expired_request_does_not_open_microphone(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, '_WaveInput', lambda: pytest.fail('Microphone must stay closed'))
    event = threading.Event()
    event.set()
    with pytest.raises(GenerationCancelled):
        capture.record_continuous(tmp_path/'cancel.wav', cancel_event=event)
    with pytest.raises(GenerationCancelled):
        capture.record_continuous(tmp_path/'expired.wav', lease_alive=lambda: False)


def test_existing_destination_is_preserved(tmp_path, runtime):
    _, install = runtime
    device = install([])
    path = tmp_path/'existing.wav'
    path.write_bytes(b'owner audio')
    with pytest.raises(FileExistsError):
        capture.record_continuous(path)
    assert path.read_bytes() == b'owner audio' and not device.opened
