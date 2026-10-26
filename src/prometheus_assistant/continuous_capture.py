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
from .speech_activity import SpeechDetector

RATE = 16000
MAX_AUDIO_BYTES = 100 * 1024 * 1024
SILENCE_SECONDS = .8
TENTATIVE_SILENCE_SECONDS = 1.2
MAX_SILENCE_SECONDS = 1.6
ESTABLISHED_SPEECH_SECONDS = .4
PRE_ROLL_BYTES = int(RATE * 2 * .8)


class EmptyRecording(GenerationCancelled):
    """A user finished a quiet recording; there is no failed device or transcript."""


class _Format(ctypes.Structure):
    _layout_ = 'ms'
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


if sys.platform == 'linux':
    from .linux_voice import LinuxInput as _WaveInput


def record_continuous(destination, *, cancel_event=None, finish_event=None,
                      lease_alive=lambda: True, on_level=None, on_started=None, on_diagnostics=None):
    """Record until a normal speech pause or manual finish; cancellation discards.

The 100 MiB transcriber/file limit is a resource limit, not a recording timer.
Waiting for speech retains only a short PCM pre-roll, while cancellation and the
visible-tab lease remain active. Level is measured RMS, scaled for display; it
is never a generated animation or a transcription claim.
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
    capture_started = time.monotonic()
    setup_seconds = 0.
    manually_finished = False
    destination = Path(destination)
    def open_device():
        # Model loading can take time. End/disconnection during it must not open
        # the microphone even briefly; the detector context still closes.
        check_cancel()
        return _WaveInput()

    # Exclusive creation prevents deleting/overwriting an owner's existing audio.
    output = destination.open('xb')
    completed = False
    try:
        with output, wave.open(output, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(RATE)
            size = 44
            elapsed = 0.
            last_speech = None
            speech_seconds = 0.
            pause_seconds = SILENCE_SECONDS
            pre_roll = bytearray()
            with SpeechDetector() as detector, open_device() as device:
                last_data = time.monotonic()
                setup_seconds = last_data-capture_started
                if on_started:
                    on_started()

                def write(data):
                    nonlocal size
                    if size+len(data) > MAX_AUDIO_BYTES:
                        raise ValueError('Recording reached the 100 MiB local audio limit; use a shorter recording.')
                    audio.writeframesraw(data)
                    size += len(data)

                def consume(data, *, final=False):
                    nonlocal elapsed, last_speech, last_data
                    if len(data) % 2:
                        raise ValueError('The microphone returned incomplete PCM samples.')
                    if data:
                        last_data = time.monotonic()
                    for frame, speech in detector.feed(data, final=final):
                        consume_frame(frame, speech)
                    if data and on_level:
                        samples = array.array('h', data)
                        if sys.byteorder != 'little':
                            samples.byteswap()
                        rms = math.sqrt(sum(sample*sample for sample in samples)/len(samples))
                        on_level(round(min(1., rms/4096), 4), round(elapsed, 2))

                def consume_frame(data, speech):
                    nonlocal elapsed, last_speech, speech_seconds, pause_seconds
                    had_speech = last_speech is not None
                    if not had_speech:
                        # Keep the quiet onset and confirmation frames, but never
                        # retain an idle session's accumulating silence in RAM/disk.
                        pre_roll.extend(data[-PRE_ROLL_BYTES:])
                        if len(pre_roll) > PRE_ROLL_BYTES:
                            del pre_roll[:-PRE_ROLL_BYTES]
                    duration = len(data)/(RATE*2)
                    elapsed += duration
                    if speech:
                        if last_speech is not None:
                            # Learn actual within-turn hesitations, without
                            # treating every background/noise frame as speech.
                            gap = max(0., elapsed-duration-last_speech)
                            pause_seconds = max(pause_seconds, min(MAX_SILENCE_SECONDS, gap*1.5+.2))
                        speech_seconds += duration
                        last_speech = elapsed
                    if had_speech:
                        write(data)
                    elif last_speech is not None:
                        write(pre_roll)
                        pre_roll.clear()

                while True:
                    check_cancel()
                    if finish_event.is_set():
                        manually_finished = True
                        consume(device.finish(), final=True)
                        break
                    data = device.read()
                    consume(data)
                    now = time.monotonic()
                    # Established speech can finish promptly; a tentative word
                    # gets more time to continue. This is acoustic timing, not a
                    # prediction that the speaker has completed a thought.
                    allowance = pause_seconds if speech_seconds >= ESTABLISHED_SPEECH_SECONDS else max(pause_seconds, TENTATIVE_SILENCE_SECONDS)
                    if last_speech is not None and elapsed-last_speech >= allowance:
                        break
                    if now-last_data >= 5:
                        raise ValueError('The microphone stopped supplying audio; check the input device and try again.')
                    if not data:
                        cancel_event.wait(.02)
                check_cancel()
                if last_speech is None:
                    raise EmptyRecording('No audible speech detected. Microphone off; nothing was submitted.')
        completed = True
        if on_diagnostics:
            on_diagnostics({key: round(min(86400., max(0., value)), 4) for key, value in {
                'capture_setup_seconds': setup_seconds,
                'capture_wall_seconds': time.monotonic()-capture_started,
                'captured_audio_seconds': (size-44)/(RATE*2),
                'speech_audio_seconds': speech_seconds,
                'endpoint_silence_audio_seconds': elapsed-last_speech,
                'manual_finish': int(manually_finished),
            }.items()})
    finally:
        if not completed:
            destination.unlink(missing_ok=True)
