"""Native VAD ownership/failure contracts using a fake API, never unsafe DLL calls."""
from collections import deque
import ctypes
from pathlib import Path
import threading

import pytest

from prometheus_assistant import continuous_capture as capture, speech_activity as activity
from prometheus_assistant.ollama import GenerationCancelled


class API:
    def __init__(self):
        self.events = []
        self.failure = None
        self.on_init = lambda: None
        self.state = 0xC0DE
        self.probability = (ctypes.c_float*1)(.9)

    def whisper_vad_init_from_file_with_params(self, path, params):
        self.events.append('init')
        assert path.endswith(b'ggml-silero-v6.2.0.bin')
        assert (params.threads, params.gpu, params.device) == (1, False, 0)
        if self.failure == 'init_log':
            activity._log(4, b'model initialization failed', None)
        self.on_init()
        return None if self.failure == 'init_null' else self.state

    def whisper_vad_reset_state(self, state):
        assert state == self.state
        self.events.append('reset')
        if self.failure == 'reset_exception':
            raise RuntimeError('synthetic reset failure')
        if self.failure == 'reset_log':
            activity._log(4, b'model reset failed', None)

    def whisper_vad_detect_speech_no_reset(self, state, samples, count):
        assert state == self.state, 'A closed native handle must never reach inference'
        assert count == 512 and len(samples) == 512
        assert all(-1 <= value <= 1 for value in samples)
        self.events.append('detect')
        if self.failure == 'infer_exception':
            raise RuntimeError('synthetic inference failure')
        if self.failure == 'infer_log':
            activity._log(4, b'compute graph failed', None)
        return self.failure != 'infer_false'

    def whisper_vad_n_probs(self, state):
        assert state == self.state
        return 2 if self.failure == 'count' else 1

    def whisper_vad_probs(self, state):
        assert state == self.state
        if self.failure == 'null_probability':
            return ctypes.POINTER(ctypes.c_float)()
        if self.failure == 'nan':
            self.probability[0] = float('nan')
        if self.failure == 'range':
            self.probability[0] = 1.5
        return self.probability

    def whisper_vad_free(self, state):
        assert state == self.state
        assert 'free' not in self.events, 'Each native model must be freed exactly once'
        self.events.append('free')


@pytest.fixture
def api(monkeypatch):
    instance = API()
    monkeypatch.setattr(activity, '_errors', deque(maxlen=8))
    monkeypatch.setattr(activity, '_native_detector', lambda: (instance, Path('model-fixture'), None))
    return instance


def test_pinned_context_parameter_abi():
    assert ctypes.sizeof(activity._Params) == 12
    assert (activity._Params.threads.offset, activity._Params.gpu.offset, activity._Params.device.offset) == (0, 4, 8)


def test_reset_precedes_first_inference_and_close_is_idempotent(api):
    detector = activity.SpeechDetector()
    assert api.events == ['init', 'reset']
    assert len(list(detector.feed(b'\x00\x00'*512))) == 1
    assert api.events == ['init', 'reset', 'detect']
    detector.close()
    detector.close()
    with pytest.raises(ValueError, match='closed'):
        list(detector.feed(b'\x00\x00'*512))
    assert api.events == ['init', 'reset', 'detect', 'free']


def test_paused_frame_generator_cannot_use_handle_after_close(api):
    detector = activity.SpeechDetector()
    frames = detector.feed(b'\x00\x00'*1024)
    next(frames)
    detector.close()
    with pytest.raises(ValueError, match='closed'):
        next(frames)
    assert api.events == ['init', 'reset', 'detect', 'free']


@pytest.mark.parametrize('failure', ['init_null', 'init_log', 'reset_exception', 'reset_log'])
def test_model_initialization_failure_prevents_device_open_and_releases_state(api, monkeypatch, tmp_path, failure):
    api.failure = failure
    monkeypatch.setattr(capture, '_WaveInput', lambda: pytest.fail('A failed model must not open the microphone'))
    output = tmp_path/'never-recorded.wav'
    with pytest.raises((ValueError, RuntimeError), match='model|reset'):
        capture.record_continuous(output)
    assert api.events.count('free') == (0 if failure == 'init_null' else 1)
    assert not output.exists()


def test_input_open_failure_releases_model_and_partial_wav(api, monkeypatch, tmp_path):
    class FailedInput:
        def __enter__(self):
            assert api.events == ['init', 'reset']
            raise ValueError('input device failed to open')
        def __exit__(self, *args):
            pytest.fail('A device that never entered has no device context to exit')
    monkeypatch.setattr(capture, '_WaveInput', FailedInput)
    output = tmp_path/'input-failed.wav'
    with pytest.raises(ValueError, match='input device'):
        capture.record_continuous(output)
    assert api.events == ['init', 'reset', 'free'] and not output.exists()


@pytest.mark.parametrize('failure', ['cancel', 'lease', 'callback', 'infer_false', 'infer_exception',
                                    'infer_log', 'count', 'null_probability', 'nan', 'range'])
def test_recording_failure_closes_input_before_freeing_model_and_discards_wav(api, monkeypatch, tmp_path, failure):
    api.failure = failure
    cancelled = threading.Event()
    lease = [True]
    class Input:
        def __enter__(self):
            api.events.append('input opened')
            return self
        def __exit__(self, *args):
            api.events.append('input closed')
        def read(self):
            if failure == 'cancel':
                cancelled.set()
            if failure == 'lease':
                lease[0] = False
            return b'\x00\x04'*1280
        def finish(self):
            pytest.fail('Failure cleanup must not submit a manual finish')
    monkeypatch.setattr(capture, '_WaveInput', Input)
    def update(level, elapsed):
        if failure == 'callback':
            raise RuntimeError('synthetic UI callback failure')
    output = tmp_path/'discard.wav'
    expected = GenerationCancelled if failure in {'cancel', 'lease'} else RuntimeError if failure in {'callback', 'infer_exception'} else ValueError
    with pytest.raises(expected):
        capture.record_continuous(output, cancel_event=cancelled, lease_alive=lambda: lease[0], on_level=update)
    assert api.events[-2:] == ['input closed', 'free']
    assert api.events.count('free') == 1 and not output.exists()


def test_already_cancelled_request_never_loads_model_or_device(api, monkeypatch, tmp_path):
    monkeypatch.setattr(capture, '_WaveInput', lambda: pytest.fail('Cancelled request must not open a device'))
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(GenerationCancelled):
        capture.record_continuous(tmp_path/'cancelled.wav', cancel_event=cancelled)
    assert api.events == []


@pytest.mark.parametrize('reason', ['cancel', 'lease'])
def test_request_ended_during_model_load_never_opens_microphone(api, monkeypatch, tmp_path, reason):
    cancelled = threading.Event()
    lease = [True]
    def end_request():
        if reason == 'cancel':
            cancelled.set()
        else:
            lease[0] = False
    api.on_init = end_request
    monkeypatch.setattr(capture, '_WaveInput', lambda: pytest.fail('Request ended during model load must not open a device'))
    output = tmp_path/'ended-while-loading.wav'
    with pytest.raises(GenerationCancelled):
        capture.record_continuous(output, cancel_event=cancelled, lease_alive=lambda: lease[0])
    assert api.events == ['init', 'reset', 'free'] and not output.exists()
