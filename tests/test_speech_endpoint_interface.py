"""Actual VAD/capture through an Interface job; only device and Whisper are fixtures."""
import array
from itertools import cycle
from pathlib import Path
import random
import threading
import time
import wave

import pytest

from prometheus_assistant import continuous_capture, ui_server, voice


@pytest.mark.parametrize('chunk_sizes', [(2560,), (642, 318, 1280, 96)])
def test_speech_pause_completes_one_job_without_manual_finish(tmp_path, monkeypatch, chunk_sizes):
    fixture = Path(__file__).parent/'fixtures/voice_endpoint/speech-0.wav'
    with wave.open(str(fixture), 'rb') as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        speech = array.array('h', audio.readframes(audio.getnframes()))
    rng = random.Random(316)
    samples = [0]*16000 + list(speech) + [0]*128000
    # Nonzero background was the physical failure case; do not model silence as zeros.
    pcm = array.array('h', [max(-32768, min(32767, value+round(rng.gauss(0, 500))))
                           for value in samples]).tobytes()
    events = []

    class PCMDevice:
        def __init__(self):
            self.offset = 0
            self.sizes = cycle(chunk_sizes)
            self.closed = False

        def __enter__(self):
            events.append('microphone opened')
            return self

        def read(self):
            assert self.offset < len(pcm), 'Speech pause must finish before all trailing background is read'
            end = min(len(pcm), self.offset+next(self.sizes))
            chunk = pcm[self.offset:end]
            self.offset = end
            return chunk

        def finish(self):
            pytest.fail('Automatic endpoint must never need manual finish')

        def __exit__(self, *args):
            self.closed = True
            events.append('microphone closed')

    device = PCMDevice()
    entered_transcription = threading.Event()
    release_transcription = threading.Event()
    transcripts = []
    app = ui_server.Interface(tmp_path/'memory.sqlite3')
    monkeypatch.setattr(continuous_capture, '_WaveInput', lambda: device)
    monkeypatch.setattr(voice, '_transcriber', lambda model: None)

    def transcribe(audio, *, cancel_event, on_diagnostics=None):
        # Keep the real record_continuous and native detector. Replace only the
        # expensive recognizer so this test proves device/job ordering separately.
        assert device.closed and events == ['microphone opened', 'microphone closed']
        assert not app.dictation_finish.is_set() and not cancel_event.is_set()
        with app.lock:
            row = app.jobs[app.current].copy()
        assert row['recording'] is False and row['stage'].startswith('Microphone off')
        with wave.open(str(audio), 'rb') as captured:
            assert (captured.getframerate(), captured.getnchannels(), captured.getsampwidth()) == (16000, 1, 2)
            assert captured.getnframes() >= len(speech)
        transcripts.append(Path(audio))
        events.append('transcription started')
        entered_transcription.set()
        assert release_transcription.wait(3), 'Test did not release the recognizer fixture'
        return {'text': 'What is six times seven?', 'model': 'test boundary', 'local': True}

    monkeypatch.setattr(voice, 'transcribe', transcribe)
    try:
        job = app.submit({'mode': 'dictate', 'prompt': ''})
        if not entered_transcription.wait(3):
            with app.lock:
                pytest.fail(f'Automatic endpoint did not reach transcription: {app.jobs[job["id"]]}')
        with app.lock:
            row = app.jobs[job['id']].copy()
        assert row['state'] == 'running' and row['recording'] is False
        assert app.state == 'thinking' and app.current == job['id']
        assert device.offset < len(pcm), 'End detection must occur while background audio continues'
        assert not app.dictation_finish.is_set()
        release_transcription.set()
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            with app.lock:
                row = app.jobs[job['id']].copy()
            if row['state'] != 'running':
                break
            time.sleep(.01)
        assert row['state'] == 'done' and row['result']['transcript'] == 'What is six times seven?'
        assert row['recording'] is False and row['level'] == 0
        assert len(app.jobs) == 1 and len(transcripts) == 1
        assert app.state == 'idle' and app.current is None
        assert events == ['microphone opened', 'microphone closed', 'transcription started']
        assert not transcripts[0].exists(), 'Completed job must erase temporary capture'
    finally:
        release_transcription.set()
        app.close()
