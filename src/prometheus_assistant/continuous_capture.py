"""On-demand Windows PCM capture with a bounded device queue and a disk-backed WAV.

No device is opened at import. WinMM buffers are polled rather than calling Python
from a driver thread; reset returns every buffer before it is unprepared/freed.
"""
from __future__ import annotations

import array
import ctypes
import math
import os
from pathlib import Path
import sys
import threading
import time
import wave

from .ollama import GenerationCancelled

RATE = 16000
MAX_AUDIO_BYTES = 100 * 1024 * 1024
SILENCE_SECONDS = 2.0
NO_SPEECH_SECONDS = 30.0
SPEECH_RMS = 200
MIN_SPEECH_SECONDS = .24


class _Format(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('tag', ctypes.c_uint16), ('channels', ctypes.c_uint16),
                ('rate', ctypes.c_uint32), ('bytes_per_second', ctypes.c_uint32),
                ('alignment', ctypes.c_uint16), ('bits', ctypes.c_uint16),
                ('extra', ctypes.c_uint16)]


class _Header(ctypes.Structure):
    _fields_ = [('data', ctypes.c_void_p), ('length', ctypes.c_uint32),
                ('recorded', ctypes.c_uint32), ('user', ctypes.c_size_t),
                ('flags', ctypes.c_uint32), ('loops', ctypes.c_uint32),
                ('next', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]


# A broken driver must never retain pointers to freed ctypes buffers. Normal
# cleanup empties these immediately; a failed close retains its bounded queue.
_failed_devices = []


class _WaveInput:
    """Default input, 16 kHz mono PCM16, twelve 80 ms reusable buffers."""
    def __init__(self):
        if _failed_devices:
            raise ValueError('A microphone driver did not close cleanly; restart Prometheus before recording again.')
        if os.name != 'nt':
            raise ValueError('Microphone capture currently supports Windows.')
        self.api = ctypes.WinDLL('winmm')
        self.handle = ctypes.c_void_p()
        self.buffers = []
        self.next_buffer = 0
        self.stopped = False
        self.api.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                       ctypes.POINTER(_Format), ctypes.c_size_t,
                                       ctypes.c_size_t, ctypes.c_uint]
        for name in ('waveInPrepareHeader', 'waveInAddBuffer', 'waveInUnprepareHeader'):
            getattr(self.api, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(_Header), ctypes.c_uint]
        for name in ('waveInStart', 'waveInStop', 'waveInReset', 'waveInClose'):
            getattr(self.api, name).argtypes = [ctypes.c_void_p]
        for name in ('waveInOpen', 'waveInPrepareHeader', 'waveInAddBuffer',
                     'waveInUnprepareHeader', 'waveInStart', 'waveInStop',
                     'waveInReset', 'waveInClose'):
            getattr(self.api, name).restype = ctypes.c_uint

    @staticmethod
    def check(status):
        if status:
            raise ValueError(f'Windows microphone operation failed ({status}); check microphone permissions and input device.')

    def __enter__(self):
        format = _Format(1, 1, RATE, RATE*2, 2, 16, 0)
        self.check(self.api.waveInOpen(ctypes.byref(self.handle), 0xffffffff,
                                      ctypes.byref(format), 0, 0, 0))
        try:
            for _ in range(12):
                data = ctypes.create_string_buffer(2560)
                header = _Header(ctypes.addressof(data), 2560, 0, 0, 0, 0, None, 0)
                self.check(self.api.waveInPrepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(header)))
                self.buffers.append((data, header))
                self.check(self.api.waveInAddBuffer(self.handle, ctypes.byref(header), ctypes.sizeof(header)))
            self.check(self.api.waveInStart(self.handle))
            return self
        except BaseException:
            self.close()
            raise

    def read(self):
        data, header = self.buffers[self.next_buffer]
        if not header.flags & 1:  # WHDR_DONE; the driver has returned this buffer.
            return b''
        if header.recorded > header.length or header.recorded % 2:
            raise ValueError('The microphone returned invalid PCM data.')
        result = data.raw[:header.recorded]
        self.next_buffer = (self.next_buffer+1) % len(self.buffers)
        if not self.stopped:
            header.recorded = 0
            self.check(self.api.waveInAddBuffer(self.handle, ctypes.byref(header), ctypes.sizeof(header)))
        else:
            header.flags &= ~1
        return result

    def finish(self):
        """Stop and retain the final partial buffer before resetting the queue."""
        self.check(self.api.waveInStop(self.handle))
        self.stopped = True
        parts = []
        for _ in range(len(self.buffers)):
            part = self.read()
            if not part:
                break
            parts.append(part)
        return b''.join(parts)

    def close(self):
        if not self.handle.value:
            return
        # Reset stops capture and returns all queued buffers, including partials.
        failures = []
        self.api.waveInStop(self.handle)
        status = self.api.waveInReset(self.handle)
        if status:
            failures.append(status)
        for _, header in self.buffers:
            status = self.api.waveInUnprepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(header))
            if status:
                failures.append(status)
        status = self.api.waveInClose(self.handle)
        if status:
            failures.append(status)
            _failed_devices.append(self)
        else:
            self.handle.value = None
            self.buffers.clear()
        if failures:
            raise ValueError(f'Windows microphone cleanup failed ({failures[0]}); close Prometheus before recording again.')

    def __exit__(self, *args):
        self.close()


def record_continuous(destination, *, cancel_event=None, finish_event=None,
                      lease_alive=lambda: True, on_level=None, on_started=None):
    """Record until a normal speech pause or manual finish; cancellation discards.

The 100 MiB transcriber/file limit is a resource limit, not a recording timer.
Silence cannot keep the microphone open indefinitely. Level is measured RMS,
scaled for display; it is never a generated animation or a transcription claim.
"""
    from .application_policy import require_dispatch
    require_dispatch('voice.record')
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    finish_event = finish_event if finish_event is not None else threading.Event()

    def check_cancel():
        if cancel_event.is_set():
            raise GenerationCancelled('Dictation cancelled; no transcript was submitted.')
        if not lease_alive():
            raise GenerationCancelled('Microphone stopped because the recording tab disconnected; recording was discarded.')

    check_cancel()
    destination = Path(destination)
    # Exclusive creation prevents deleting/overwriting an owner's existing audio.
    output = destination.open('xb')
    completed = False
    try:
        with output, wave.open(output, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(RATE)
            size = 44
            elapsed = speech_run = 0.
            last_speech = None
            with _WaveInput() as device:
                started = last_data = time.monotonic()
                if on_started:
                    on_started()

                def consume(data):
                    nonlocal size, elapsed, speech_run, last_speech, last_data
                    if not data:
                        return
                    if size+len(data) > MAX_AUDIO_BYTES:
                        raise ValueError('Recording reached the 100 MiB local audio limit; use a shorter recording.')
                    if len(data) % 2:
                        raise ValueError('The microphone returned incomplete PCM samples.')
                    audio.writeframesraw(data)
                    size += len(data)
                    duration = len(data)/(RATE*2)
                    elapsed += duration
                    last_data = time.monotonic()
                    samples = array.array('h', data)
                    if sys.byteorder != 'little':
                        samples.byteswap()
                    rms = math.sqrt(sum(sample*sample for sample in samples)/len(samples))
                    if rms >= SPEECH_RMS:
                        speech_run += duration
                        if speech_run >= MIN_SPEECH_SECONDS or last_speech is not None:
                            last_speech = elapsed
                    else:
                        speech_run = 0.
                    if on_level:
                        on_level(round(min(1., rms/4096), 4), round(elapsed, 2))

                while True:
                    check_cancel()
                    if finish_event.is_set():
                        consume(device.finish())
                        break
                    data = device.read()
                    consume(data)
                    now = time.monotonic()
                    if last_speech is not None and elapsed-last_speech >= SILENCE_SECONDS:
                        break
                    if last_speech is None and max(elapsed, now-started) >= NO_SPEECH_SECONDS:
                        raise ValueError('No audible speech detected for 30 seconds; microphone stopped and nothing was submitted.')
                    if now-last_data >= 5:
                        raise ValueError('The microphone stopped supplying audio; check the input device and try again.')
                    if not data:
                        cancel_event.wait(.02)
                check_cancel()
                if last_speech is None:
                    raise ValueError('No audible speech detected; nothing was submitted.')
        completed = True
    finally:
        if not completed:
            destination.unlink(missing_ok=True)
