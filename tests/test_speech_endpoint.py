"""Real detector/capture integration using synthetic spoken PCM, never a microphone."""
import array
import math
from pathlib import Path
import random
import threading
import wave

import pytest

from prometheus_assistant import continuous_capture as capture
from test_continuous_voice import Clock, Device


def phrase(index=0, scale=1):
    with wave.open(str(Path(__file__).parent/'fixtures/voice_endpoint'/f'speech-{index}.wav'), 'rb') as src:
        samples = array.array('h', src.readframes(src.getnframes()))
    # Remove synthetic speaker's trailing silence so the timing assertion measures
    # from the actual utterance; retain 100 ms to preserve final quiet consonants.
    last = max(i for i, value in enumerate(samples) if abs(value) > 120)
    return [round(value*scale) for value in samples[:last+1600]]


def background(seconds, kind='fan', level=500):
    rng = random.Random(817)
    return [round(rng.gauss(0, level)) if kind == 'fan' else
            round(level*math.sin(2*math.pi*120*i/16000)) if kind == 'hum' else
            level if kind == 'dc' else 0 for i in range(round(seconds*16000))]


def pcm(samples):
    return array.array('h', [max(-32768, min(32767, value)) for value in samples]).tobytes()


def record(tmp_path, monkeypatch, samples, *, finish_after=None, sizes=(2560,)):
    raw = pcm(samples)
    chunks, offset, index = [], 0, 0
    while offset < len(raw):
        size = sizes[index % len(sizes)]
        chunks.append(raw[offset:offset+size])
        offset += size
        index += 1
    clock = Clock()
    device = Device(clock, chunks, tick=.08)
    monkeypatch.setattr(capture.time, 'monotonic', clock)
    monkeypatch.setattr(capture, '_WaveInput', lambda: device)
    finish, levels = threading.Event(), []
    def update(level, elapsed):
        levels.append((level, elapsed))
        if finish_after is not None and elapsed >= finish_after:
            finish.set()
    path = tmp_path/'voice.wav'
    try:
        capture.record_continuous(path, finish_event=finish, on_level=update)
    finally:
        assert device.closed
    with wave.open(str(path), 'rb') as src:
        output = src.readframes(src.getnframes())
    return levels[-1][1], output


@pytest.mark.parametrize('kind', ['fan', 'hum', 'dc'])
def test_spoken_turn_ends_without_stop_despite_nonzero_background(tmp_path, monkeypatch, kind):
    speech = phrase()
    samples = [0]*16000 + speech + [0]*128000
    noise = background(len(samples)/16000, kind)
    samples = [voice+sound for voice, sound in zip(samples, noise)]
    elapsed, output = record(tmp_path, monkeypatch, samples)
    end = 1+len(speech)/16000
    assert end+.65 <= elapsed <= end+1.7, (end, elapsed)
    assert len(output) > len(speech)*2
    assert pcm(samples[16000:16000+len(speech)]) in output


@pytest.mark.parametrize('kind', ['fan', 'hum', 'dc'])
def test_noise_alone_waits_without_submitting(tmp_path, monkeypatch, kind):
    with pytest.raises(capture.EmptyRecording):
        record(tmp_path, monkeypatch, background(65, kind), finish_after=60)
    assert not (tmp_path/'voice.wav').exists()


@pytest.mark.parametrize('index,scale,noise', [(0,1,0), (1,1,500), (2,1,0),
                                             (0,.04,0), (2,.04,0), (1,.12,300)])
def test_immediate_quiet_and_short_speech_preserves_onset(tmp_path, monkeypatch, index, scale, noise):
    speech = phrase(index, scale)
    samples = speech+[0]*64000
    mixed = [voice+sound for voice,sound in zip(samples, background(len(samples)/16000, level=noise))]
    elapsed, output = record(tmp_path, monkeypatch, mixed)
    assert len(speech)/16000+.65 <= elapsed <= len(speech)/16000+1.7
    # No silent calibration or loss of the initial syllable, even if it takes
    # several detector frames to recognize a quiet first word.
    assert output.startswith(pcm(mixed[:len(speech)]))


def test_brief_pause_and_long_speech_stay_in_one_turn(tmp_path, monkeypatch):
    speech = phrase()
    # A first 1.2 s thinking pause conflicts with a prompt 0.8 s endpoint.
    # Brief half-second pauses and >30 s of audio must still stay together.
    samples = (speech+[0]*8000)*10 + phrase(1)
    end = len(samples)/16000
    elapsed, output = record(tmp_path, monkeypatch, samples+[0]*64000)
    assert elapsed > 30 and end+.65 <= elapsed <= end+1.7
    assert pcm(samples) in output


def test_repeated_clicks_do_not_hold_completed_turn_open(tmp_path, monkeypatch):
    speech = phrase()
    trailing = background(8, level=300)
    for start in range(0, len(trailing), 9600):
        # A 10 ms tap every 600 ms defeated the previous amplitude endpoint.
        for i in range(start, min(start+160, len(trailing))):
            trailing[i] += 8000 if i % 2 else -8000
    elapsed, _ = record(tmp_path, monkeypatch, speech+trailing)
    assert elapsed <= len(speech)/16000+1.7


def test_irregular_buffers_retain_every_pcm_sample(tmp_path, monkeypatch):
    speech = phrase()
    elapsed, output = record(tmp_path, monkeypatch, speech+[0]*64000,
                             sizes=(2, 318, 1026, 2560, 94, 790))
    assert output.startswith(pcm(speech))
    assert len(speech)/16000+.65 <= elapsed <= len(speech)/16000+1.7


def test_manual_finish_retains_subframe_tail(tmp_path, monkeypatch):
    from prometheus_assistant.speech_activity import SpeechDetector
    detector = SpeechDetector()
    raw = pcm(phrase())+b'\x01\x00'*37
    outputs = []
    for offset in range(0, len(raw), 258):
        outputs.extend(frame for frame, _ in detector.feed(raw[offset:offset+258]))
    outputs.extend(frame for frame, _ in detector.feed(b'', final=True))
    assert b''.join(outputs) == raw
    assert not detector.pending
    detector.close()
    detector.close()
    with pytest.raises(ValueError, match='closed'):
        list(detector.feed(b'\0'*1024))


def test_missing_detector_fails_before_opening_device(tmp_path, monkeypatch):
    from prometheus_assistant import speech_activity
    def missing(*args, **kwargs):
        raise OSError('missing test binary')
    import sys
    if sys.platform == 'linux':
        from prometheus_assistant import linux_speech_activity as native
    else:
        native = speech_activity
    monkeypatch.setattr(native, '_runtime', None)
    monkeypatch.setattr(speech_activity.ctypes, 'CDLL', missing)
    monkeypatch.setattr(capture, '_WaveInput', lambda: pytest.fail('device must stay closed'))
    with pytest.raises(ValueError, match='speech detector.*(load|unavailable)'):
        capture.record_continuous(tmp_path/'missing.wav')
    assert not (tmp_path/'missing.wav').exists()


def test_each_turn_starts_with_fresh_recurrent_state():
    from prometheus_assistant.speech_activity import SpeechDetector
    raw = pcm(phrase(0,.04)+[0]*32000)
    turns = []
    for _ in range(3):
        with SpeechDetector() as detector:
            turns.append([speech for _, speech in detector.feed(raw)])
    assert any(turns[0])
    assert turns[0] == turns[1] == turns[2]


def test_detector_bundle_is_verified_without_loading_or_recording(monkeypatch):
    from prometheus_assistant import speech_activity
    monkeypatch.setattr(speech_activity, '_native_detector', lambda: pytest.fail('No native load during status'))
    import sys
    status = speech_activity.detector_status()
    assert status['device_tested'] is False
    assert 'Silero v6.2.0' in status['engine']
    if sys.platform == 'linux':
        assert status['model_bundle_verified'] and status['native_runtime_present']
        assert 'Ubuntu' in status['engine']
    else:
        assert status['bundle_verified'] is True
