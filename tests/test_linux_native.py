import io
import os
import sys
from pathlib import Path
import threading
import wave

import pytest
from prometheus_assistant import linux_voice, remote_access
from prometheus_assistant.ollama import GenerationCancelled


def test_failed_capture_never_deletes_an_existing_file(tmp_path):
    path = tmp_path/'existing.wav'; path.write_bytes(b'owner audio')
    with pytest.raises(FileExistsError):
        linux_voice.record(path, 1, threading.Event())
    assert path.read_bytes() == b'owner audio'


def test_cancelled_capture_closes_device_and_discards_own_audio(tmp_path, monkeypatch):
    closed = []
    event = threading.Event()
    class Device:
        def __enter__(self): event.set(); return self
        def __exit__(self, *args): closed.append(True)
        def read(self): pytest.fail('Cancelled capture must not read')
    monkeypatch.setattr(linux_voice, 'LinuxInput', Device)
    path = tmp_path/'cancelled.wav'
    with pytest.raises(GenerationCancelled):
        linux_voice.record(path, 1, event)
    assert closed == [True] and not path.exists()


def test_pcm_conversion_preserves_duration_and_sign(tmp_path):
    import array
    path = tmp_path/'speech.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 22050, 0, 'NONE', ''))
        audio.writeframes(array.array('h', [-1234]*22050).tobytes())
    with wave.open(io.BytesIO(linux_voice.normalize_wav(path)), 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (1, 2, 16000, 16000)
        assert set(array.array('h', audio.readframes(16000))) == {-1234}


def test_linux_bridge_filters_hooks_and_uses_private_auth(tmp_path, monkeypatch):
    observed = {}
    (tmp_path/'runtime/remote-linux').mkdir(parents=True)
    monkeypatch.setattr(remote_access, '__file__', str(tmp_path/'src/remote_access.py'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    for name in ('NODE_OPTIONS', 'NODE_PATH', 'HTTP_PROXY', 'OPENAI_API_KEY'):
        monkeypatch.setenv(name, 'must-not-inherit')
    def verified(runtime, manifest, **kwargs):
        observed.update(kwargs)
        return Path('/usr/bin/node'), runtime/'node_modules/mcp-remote/dist/proxy.js'
    monkeypatch.setattr(remote_access, 'verify_runtime', verified)
    monkeypatch.setattr(remote_access, 'MCPBridge', lambda command, **kwargs: observed.update(command=command, **kwargs) or 'bridge')
    assert remote_access._launch_linux_bridge() == 'bridge'
    assert observed['expected'] == remote_access.LINUX_CLIENT_SHA256
    assert observed['system_node'] == '/usr/bin/node'
    assert not {'NODE_OPTIONS','NODE_PATH','HTTP_PROXY','OPENAI_API_KEY'} & observed['env'].keys()
    auth = Path(observed['env']['MCP_REMOTE_CONFIG_DIR'])
    assert auth.stat().st_mode & 0o777 == 0o700
    assert auth.parent == tmp_path/'PrometheusRemote'


def test_linux_state_rejects_relative_profile(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_STATE_HOME', 'relative')
    with pytest.raises(ValueError, match='absolute'):
        remote_access.host_root()


def test_linux_capture_reports_disconnection_and_closes_owned_process():
    device = linux_voice.LinuxInput()
    reader, writer = os.pipe(); os.close(writer)
    class Process:
        stdout = os.fdopen(reader, 'rb', buffering=0)
        def poll(self): return 1
    device.process = Process()
    with pytest.raises(ValueError, match='disconnected'):
        device.read()
    device.close()
    assert device.process is None


@pytest.mark.parametrize('failure', ['null', 'log', 'infer', 'count', 'nan', 'closed'])
def test_linux_vad_failure_releases_native_state(monkeypatch, failure):
    import ctypes
    from prometheus_assistant import linux_speech_activity as activity
    class API:
        freed = 0
        probability = (ctypes.c_float*5)(*[.9]*5)
        def whisper_vad_init_from_file_with_params(self, *args):
            if failure == 'log': activity._log(4, b'synthetic failure', None)
            return None if failure == 'null' else 42
        def whisper_vad_free(self, state):
            assert state == 42
            self.freed += 1
        def whisper_vad_detect_speech(self, state, samples, count):
            assert state == 42 and count == 2560
            return failure != 'infer'
        def whisper_vad_n_probs(self, state): return 1 if failure == 'count' else 5
        def whisper_vad_probs(self, state):
            if failure == 'nan': self.probability[0] = float('nan')
            return self.probability
    api = API()
    monkeypatch.setattr(activity, '_native', lambda: api)
    with pytest.raises(ValueError):
        with activity.LinuxSpeechDetector() as detector:
            frames = detector.feed(bytes(5120))
            if failure == 'closed':
                next(frames); detector.close()
            list(frames)
    assert api.freed == (0 if failure == 'null' else 1)


def test_compact_vision_preserves_small_host_headroom():
    from prometheus_assistant.model_routing import choose_helper
    from prometheus_assistant.hardware import HardwareProfile
    installed = {'qwen3.5:0.8b', 'qwen3-vl:2b'}
    assert choose_helper(installed, HardwareProfile(7, 8, 'Linux', 5), 'vision').model == 'qwen3.5:0.8b'
    assert choose_helper(installed, HardwareProfile(16, 8, 'Linux', 12), 'vision').model == 'qwen3-vl:2b'
