"""Explicit, cancellable model preparation before opening a voice-chat input."""
import threading
import time
import tempfile
from pathlib import Path

from .compact_chat import candidates
from .hardware import detect_hardware
from .ollama import GenerationCancelled, OllamaClient
from . import voice

CALIBRATION_PHRASE = 'Prometheus is ready.'


class PreparationSignal:
    """Own-thread checks: no background loader can outlive this voice job."""
    def __init__(self, cancel_event, lease_alive):
        self.event = cancel_event
        self.lease_alive = lease_alive
        self.reason = None
        self.next_memory_check = 0.

    def is_set(self):
        if self.reason:
            return True
        if self.event.is_set() or not self.lease_alive():
            self.reason = 'Voice preparation stopped. Microphone stayed off.'
        elif time.monotonic() >= self.next_memory_check:
            self.next_memory_check = time.monotonic()+.5
            free = detect_hardware().available_ram_gib
            if free is not None and free < .35:
                self.reason = 'Voice preparation stopped because available memory fell too low. Microphone stayed off.'
        return self.reason is not None

    def wait(self, seconds):
        self.event.wait(min(seconds, .2))
        return self.is_set()

    def check(self):
        if self.is_set():
            raise GenerationCancelled(self.reason)


def prepare_voice(*, installed, evidence, ensure_runtime, cancel_event=None,
                  lease_alive=lambda: True, on_stage=lambda message: None):
    event = cancel_event if cancel_event is not None else threading.Event()
    signal = PreparationSignal(event, lease_alive)
    signal.check()
    started = time.monotonic()
    inventory = OllamaClient(timeout=.5)

    def select():
        signal.check()
        _, profiles = candidates('', installed(), detect_hardware(), evidence,
                                 task='general', resident=inventory.resident_memory_gib())
        if not profiles:
            raise ValueError('No local conversation helper fits the current memory and quality checks. Microphone stayed off.')
        return profiles[0]

    select()
    on_stage('Preparing conversation · microphone off')
    ensure_runtime(cancel_event=signal)
    profile = select()
    client = OllamaClient(model=profile.model, num_ctx=profile.context,
                          num_thread=profile.cpu_threads, timeout=120)
    # Inventory validation is bounded separately from the potentially cold load.
    inventory.model = profile.model
    inventory.ensure_local_model()
    signal.check()
    on_stage('Loading local conversation helper · microphone off')
    loading = False
    try:
        loading = True
        metrics = client.prepare(cancel_event=signal)
        signal.check()
        model_seconds = time.monotonic()-started
        # This fixed, synthetic self-check does not open a device, play sound,
        # or use owner conversation content. Owned children remain cancellable.
        with tempfile.TemporaryDirectory(prefix='prometheus-voice-ready-') as folder:
            on_stage('Preparing local voice output · microphone off')
            signal.check()
            before = time.monotonic()
            audio = voice.synthesize_wav(CALIBRATION_PHRASE, cancelled=signal.is_set)
            tts_seconds = time.monotonic()-before
            signal.check()
            path = Path(folder)/'calibration.wav'
            path.write_bytes(audio)
            on_stage('Preparing local speech recognition · microphone off')
            signal.check()
            before = time.monotonic()
            recognition = voice.transcribe(path, model='auto', cancel_event=signal)
            asr_seconds = time.monotonic()-before
            signal.check()
            recognizer_model = recognition.get('model')
            if recognizer_model not in {'base.en', 'tiny.en'}:
                raise ValueError('Local speech preparation did not report a supported recognizer.')
            # Keep only the public installed model name, never the decoded text.
            del recognition, audio
    except Exception:
        # The interface owns one worker. Release this cancelled preparation
        # before it can accept another job, using a bounded loopback request.
        if loading:
            try:
                OllamaClient(model=profile.model, timeout=2)._request('/api/generate',
                    {'model': profile.model, 'keep_alive': 0, 'stream': False})
            except Exception:
                pass  # The normal bounded residency still applies if unavailable.
        if signal.is_set():
            raise GenerationCancelled(signal.reason or 'Voice preparation stopped. Microphone stayed off.') from None
        raise
    result = {'voice_ready': True, 'model': profile.model, 'context': profile.context,
              'threads': profile.cpu_threads, 'batch': 32 if profile.context <= 2048 else 128,
              'preparation_seconds': round(min(3600., max(0., time.monotonic()-started)), 4)}
    result.update(recognizer_model=recognizer_model,
        model_preparation_seconds=round(min(3600., max(0., model_seconds)), 4),
        tts_preparation_seconds=round(min(3600., max(0., tts_seconds)), 4),
        asr_preparation_seconds=round(min(3600., max(0., asr_seconds)), 4))
    value = metrics.get('load_seconds')
    if type(value) in (int, float) and 0 <= value <= 3600:
        result['load_seconds'] = round(value, 4)
    return result
