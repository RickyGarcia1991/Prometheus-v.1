"""Silero on Ubuntu whisper.cpp 1.8.3, using bounded overlapping windows.

This native API resets recurrent state for each call. Replaying up to 1.024
seconds preserves context; only the new 160 ms of decisions are emitted.
"""
import array
from collections import deque
import ctypes
import math
from pathlib import Path
import sys
import threading

from .linux_voice import runtime_root

_lock = threading.RLock()
_runtime = None
_errors = deque(maxlen=8)
_Callback = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


@_Callback
def _log(level, message, context):
    if level == 4:
        _errors.append((message or b'Native detector error')[:400])


class _Params(ctypes.Structure):
    _fields_ = [('threads', ctypes.c_int), ('gpu', ctypes.c_bool), ('device', ctypes.c_int)]


def _native():
    global _runtime
    with _lock:
        if _runtime:
            return _runtime[0]
        folder = runtime_root()/'usr/lib/x86_64-linux-gnu'
        try:
            base = ctypes.CDLL(str(folder/'libggml-base.so.0'), mode=ctypes.RTLD_GLOBAL)
            base.ggml_log_set.argtypes = [_Callback, ctypes.c_void_p]
            base.ggml_log_set(_log, None)
            ggml = ctypes.CDLL(str(folder/'libggml.so.0'), mode=ctypes.RTLD_GLOBAL)
            ggml.ggml_backend_load.argtypes = [ctypes.c_char_p]
            ggml.ggml_backend_load.restype = ctypes.c_void_p
            if not ggml.ggml_backend_load(str(folder/'ggml/backends0/libggml-cpu-x64.so').encode()):
                raise ValueError('Ubuntu voice CPU backend could not load.')
            api = ctypes.CDLL(str(folder/'libwhisper.so.1'))
            for name, args, result in (
                ('whisper_log_set', [_Callback, ctypes.c_void_p], None),
                ('whisper_vad_init_from_file_with_params', [ctypes.c_char_p, _Params], ctypes.c_void_p),
                ('whisper_vad_detect_speech', [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int], ctypes.c_bool),
                ('whisper_vad_n_probs', [ctypes.c_void_p], ctypes.c_int),
                ('whisper_vad_probs', [ctypes.c_void_p], ctypes.POINTER(ctypes.c_float)),
                ('whisper_vad_free', [ctypes.c_void_p], None),
            ):
                function = getattr(api, name)
                function.argtypes, function.restype = args, result
            api.whisper_log_set(_log, None)
            _runtime = (api, base, ggml)
            return api
        except (OSError, AttributeError) as error:
            raise ValueError('Ubuntu speech detector is unavailable; repair the native voice runtime.') from error


class LinuxSpeechDetector:
    def __init__(self):
        self.api = _native()
        self.state = None
        model = Path(__file__).with_name('vendor')/'voice_activity/ggml-silero-v6.2.0.bin'
        with _lock:
            _errors.clear()
            self.state = self.api.whisper_vad_init_from_file_with_params(str(model).encode(), _Params(1, False, 0))
            if not self.state or _errors:
                self.close()
                raise ValueError('Ubuntu speech detector could not initialize.')
        self.pending = bytearray()
        self.context = bytearray()
        self.votes = deque(maxlen=5)

    def __enter__(self):
        return self

    def close(self):
        with _lock:
            if self.state:
                self.api.whisper_vad_free(self.state)
                self.state = None

    def __exit__(self, *args):
        self.close()

    def feed(self, data, *, final=False):
        if not self.state:
            raise ValueError('The speech detector is closed.')
        self.pending.extend(data)
        while len(self.pending) >= 5120 or (final and self.pending):
            chunk = bytes(self.pending[:5120]); del self.pending[:5120]
            if len(chunk) % 2:
                raise ValueError('Incomplete PCM sample.')
            frames = [chunk[i:i+1024] for i in range(0, len(chunk), 1024)]
            padded = b''.join(frame.ljust(1024, b'\0') for frame in frames)
            self.context.extend(padded)
            del self.context[:-32768]
            samples = array.array('h', self.context)
            if sys.byteorder != 'little':
                samples.byteswap()
            values = (ctypes.c_float*len(samples))(*(sample/32768 for sample in samples))
            with _lock:
                if not self.state:
                    raise ValueError('The speech detector is closed.')
                _errors.clear()
                ok = self.api.whisper_vad_detect_speech(self.state, values, len(samples))
                count = self.api.whisper_vad_n_probs(self.state)
                ptr = self.api.whisper_vad_probs(self.state)
                if not ok or _errors or count != len(samples)//512 or not ptr:
                    raise ValueError('Ubuntu speech detection failed; recording was discarded.')
                probabilities = list(ptr[count-len(frames):count])
            for frame, probability in zip(frames, probabilities):
                if not self.state:
                    raise ValueError('The speech detector is closed.')
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError('Invalid speech probability.')
                voiced = probability >= .5
                self.votes.append(voiced)
                yield frame, voiced and sum(self.votes) >= 3
