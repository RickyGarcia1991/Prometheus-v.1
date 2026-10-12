"""Local streaming Silero VAD using the pinned whisper.cpp CPU runtime.

No library, model or device opens at import. Each recording owns its model state.
"""
import array
from collections import deque
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading

FRAME_BYTES = 1024  # Silero: 512 samples, 32 ms at 16 kHz mono PCM16
_lock = threading.RLock()
_runtime = None
_errors = deque(maxlen=8)
_LogCallback = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


@_LogCallback
def _log(level, message, context):
    if level == 4:  # GGML_LOG_LEVEL_ERROR (pinned b5454 ggml.h)
        _errors.append((message or b'Native detector failure')[:400])


class _Params(ctypes.Structure):
    _fields_ = [('threads', ctypes.c_int), ('gpu', ctypes.c_bool), ('device', ctypes.c_int)]


def _native_detector():
    global _runtime
    with _lock:
        if _runtime is not None:
            return _runtime
        if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError('Speech detection requires the portable Windows x64 runtime.')
        folder = Path(__file__).with_name('vendor')/'voice_activity'
        directory = None
        try:
            directory = os.add_dll_directory(str(folder))
            base = ctypes.CDLL(str(folder/'ggml-base.dll'))
            base.ggml_log_set.argtypes = [_LogCallback, ctypes.c_void_p]
            base.ggml_log_set.restype = None
            base.ggml_log_set(_log, None)
            ggml = ctypes.CDLL(str(folder/'ggml.dll'))
            ggml.ggml_backend_load.argtypes = [ctypes.c_char_p]
            ggml.ggml_backend_load.restype = ctypes.c_void_p
            if not ggml.ggml_backend_load(str(folder/'ggml-cpu-x64.dll').encode('utf-8')):
                raise OSError('CPU backend could not load')
            api = ctypes.CDLL(str(folder/'whisper.dll'))
            signatures = {
                'whisper_log_set': ([_LogCallback, ctypes.c_void_p], None),
                'whisper_vad_init_from_file_with_params': ([ctypes.c_char_p, _Params], ctypes.c_void_p),
                'whisper_vad_detect_speech_no_reset': ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int], ctypes.c_bool),
                'whisper_vad_n_probs': ([ctypes.c_void_p], ctypes.c_int),
                'whisper_vad_probs': ([ctypes.c_void_p], ctypes.POINTER(ctypes.c_float)),
                'whisper_vad_free': ([ctypes.c_void_p], None),
                'whisper_vad_reset_state': ([ctypes.c_void_p], None),
            }
            for name, (args, result) in signatures.items():
                function = getattr(api, name)
                function.argtypes, function.restype = args, result
            api.whisper_log_set(_log, None)
            # Retain library handles and callback. Free model state each turn.
            _runtime = (api, folder, directory, base, ggml)
            return _runtime
        except (OSError, AttributeError) as error:
            if directory is not None:
                directory.close()
            raise ValueError('The local speech detector could not load. Verify or repair the Prometheus installation.') from error


class SpeechDetector:
    def __init__(self):
        self.api, folder, *_ = _native_detector()
        self.state = None
        with _lock:
            _errors.clear()
            state = self.api.whisper_vad_init_from_file_with_params(
                str(folder/'ggml-silero-v6.2.0.bin').encode('utf-8'), _Params(1, False, 0))
            if not state or _errors:
                if state:
                    self.api.whisper_vad_free(state)
                raise ValueError('The local speech model could not load. Verify or repair the Prometheus installation.')
            self.state = state
            # Streaming API deliberately skips reset. Explicitly initialize the
            # recurrent buffer before the first frame of every new recording.
            try:
                self.api.whisper_vad_reset_state(self.state)
                if _errors:
                    raise ValueError('The local speech model could not initialize.')
            except BaseException:
                self.close()
                raise
        self.pending = bytearray()
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
        """Yield original audio and confirmed speech; pad only detector input.

        Three voiced frames in 160 ms accept short words and quiet consonants,
        while rejecting isolated impulses.
        The same qualification applies after speech starts, so one noisy frame
        cannot continually postpone the end of a turn.
        """
        if not self.state:
            raise ValueError('The speech detector is closed.')
        self.pending.extend(data)
        while len(self.pending) >= FRAME_BYTES or (final and self.pending):
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            samples = array.array('h', frame.ljust(FRAME_BYTES, b'\0'))
            if sys.byteorder != 'little':
                samples.byteswap()
            values = (ctypes.c_float*512)(*(sample/32768 for sample in samples))
            with _lock:
                if not self.state:
                    raise ValueError('The speech detector is closed.')
                _errors.clear()
                ok = self.api.whisper_vad_detect_speech_no_reset(self.state, values, 512)
                if not ok or _errors or self.api.whisper_vad_n_probs(self.state) != 1:
                    raise ValueError('Local speech detection failed; recording was stopped without submitting it.')
                probabilities = self.api.whisper_vad_probs(self.state)
                probability = probabilities[0] if probabilities else float('nan')
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError('Local speech detection returned an invalid result.')
            voiced = probability >= .5
            self.votes.append(voiced)
            yield frame, voiced and sum(self.votes) >= 3


def detector_status():
    """Verify package presence/integrity without loading native code or a device."""
    folder = Path(__file__).with_name('vendor')/'voice_activity'
    try:
        rows = json.loads((folder/'LOCK.json').read_text(encoding='utf-8'))['files']
        verified = bool(rows) and all(
            Path(row['path']).name == row['path'] and
            (folder/row['path']).is_file() and
            hashlib.sha256((folder/row['path']).read_bytes()).hexdigest() == row['sha256']
            for row in rows)
    except (OSError, ValueError, KeyError, TypeError):
        verified = False
    return {'engine': 'Silero v6.2.0 via whisper.cpp b5454, local CPU',
            'bundle_verified': verified, 'device_tested': False}
