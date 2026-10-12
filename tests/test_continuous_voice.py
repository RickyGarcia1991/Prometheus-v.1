"""Continuous dictation at the PCM/device boundary, without opening a microphone."""
import struct
import threading
import wave
from itertools import chain, repeat

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
    # These are capture lifecycle/buffering tests. Constant PCM is a control
    # signal, not speech; real speech/noise discrimination lives in
    # test_speech_endpoint.py and uses the bundled detector unchanged.
    class ControlledDetector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __init__(self):
            self.run = 0.

        def feed(self, data, *, final=False):
            if not data:
                return
            audible = abs(struct.unpack('<h', data[:2])[0]) >= 200
            self.run = self.run+len(data)/32000 if audible else 0.
            yield data, self.run >= .24

    monkeypatch.setattr(capture, 'SpeechDetector', ControlledDetector)
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
        assert 47.5 <= audio.getnframes()/16000 <= 47.8
    assert any(level > 0 for level, _ in levels)
    assert levels[-1][1] >= 47.5


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


@pytest.mark.parametrize('reason', ['cancel', 'lease', 'size', 'stalled'])
def test_failures_close_microphone_and_remove_partial_audio(tmp_path, runtime, monkeypatch, reason):
    clock, install = runtime
    install([pcm(1000)]*400 if reason != 'stalled' else [])
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
    finish = threading.Event()
    def update(level, elapsed):
        if elapsed >= 34:
            finish.set()
    with pytest.raises(capture.EmptyRecording, match='No audible speech'):
        capture.record_continuous(tmp_path/'noise.wav', finish_event=finish, on_level=update)


def test_waits_past_thirty_seconds_and_preserves_initial_quiet_syllables(tmp_path, runtime):
    _, install = runtime
    onset = pcm(80)+pcm(120)+pcm(180)
    install(chain(repeat(pcm(0), 400), [pcm(80), pcm(120), pcm(180)],
                  repeat(pcm(1000), 10), repeat(pcm(0), 25)))
    path = tmp_path/'delayed-speech.wav'
    elapsed = []
    capture.record_continuous(path, on_level=lambda level, seconds: elapsed.append(seconds))
    with wave.open(str(path), 'rb') as audio:
        assert 2.5 <= audio.getnframes()/16000 < 3.5
        frames = audio.readframes(audio.getnframes())
    assert onset+pcm(1000)*10 in frames
    assert elapsed[-1] >= 42.7


def test_long_idle_does_not_grow_wav_or_consume_audio_resource_limit(tmp_path, runtime, monkeypatch):
    _, install = runtime
    install(chain(repeat(pcm(0), 3000), repeat(pcm(1000), 10), repeat(pcm(0), 25)))
    # Five minutes of input exceeds this cap; the actual short utterance fits it.
    monkeypatch.setattr(capture, 'MAX_AUDIO_BYTES', 200000)
    path = tmp_path/'idle-then-speech.wav'
    checkpoints = []
    def update(level, elapsed):
        if 30 <= elapsed < 300:
            assert path.stat().st_size <= 44
            if elapsed in (60., 120., 240.):
                checkpoints.append(elapsed)
    capture.record_continuous(path, on_level=update)
    assert checkpoints == [60., 120., 240.]
    assert path.stat().st_size <= 128044


@pytest.mark.parametrize('stop', ['cancel', 'lease', 'finish'])
def test_end_or_disconnect_while_waiting_discards_idle_audio(tmp_path, runtime, stop):
    clock, install = runtime
    install(repeat(pcm(0), 1000))
    finish = threading.Event()
    cancel = threading.Event()
    def update(level, elapsed):
        if elapsed >= 45:
            if stop == 'finish':
                finish.set()
            elif stop == 'cancel':
                cancel.set()
    path = tmp_path/'ended-idle.wav'
    expected = capture.EmptyRecording if stop == 'finish' else GenerationCancelled
    message = 'No audible speech' if stop == 'finish' else 'cancelled|disconnected'
    with pytest.raises(expected, match=message):
        capture.record_continuous(path, cancel_event=cancel, finish_event=finish,
                                  on_level=update, lease_alive=lambda: stop != 'lease' or clock() < 45)
    assert clock() > 44 and not path.exists()


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
