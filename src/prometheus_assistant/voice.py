"""On-demand local voice; recording never starts during import or diagnostics."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave
from .hardware import detect_hardware, resource_root
from .ollama import GenerationCancelled


def voice_paths():
    root = resource_root()
    if not root:
        raise ValueError('Portable resources are not configured.')
    folder = Path(root)/'Voice/Whisper-b5454'
    return folder/'Release/whisper-cli.exe', folder/'models'


def voice_status():
    from .speech_activity import detector_status
    executable, models = voice_paths()
    return {'transcriber_installed': executable.is_file(),
            'speech_detector': detector_status(),
            'models': {name:(models/f'ggml-{name}.bin').is_file() for name in ('tiny.en','base.en')},
            'speech_recognition':'OpenAI Whisper via whisper.cpp, local English models',
            'microphone':'off; starts only when requested in voice-chat',
            'speech_output':'Windows installed SAPI voices; no online voice service',
            'device_tested':False}


def _cancelled(event):
    if event.is_set():
        raise GenerationCancelled('Dictation cancelled; no transcript was submitted.')


def _transcriber(model):
    executable, models = voice_paths()
    hardware = detect_hardware()
    available = hardware.available_ram_gib
    if available is not None and available < .75:
        raise ValueError('Not enough available RAM for transcription; free some memory and retry.')
    if model == 'auto':
        model = 'base.en' if available is not None and available >= 2 else 'tiny.en'
    if model not in ('tiny.en','base.en'):
        raise ValueError('Unsupported installed voice model.')
    weights=models/f'ggml-{model}.bin'
    if not executable.is_file() or not weights.is_file():
        raise ValueError('Portable Whisper runtime or model is missing.')
    return executable, weights, model, hardware


def transcribe(audio, *, model='auto', cancel_event=None, on_stage=None):
    from .application_policy import require_dispatch
    require_dispatch('voice.transcribe')
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    _cancelled(cancel_event)
    audio = Path(audio).resolve(strict=True)
    if not audio.is_file() or audio.suffix.lower() not in ('.wav','.mp3','.flac','.ogg'):
        raise ValueError('Choose a WAV, MP3, FLAC or OGG audio file.')
    if audio.stat().st_size > 100*1024*1024:
        raise ValueError('Audio is limited to 100 MiB per request.')
    executable, weights, model, hardware = _transcriber(model)
    # Reject silent PCM captures before decoding; Whisper can hallucinate on silence.
    if audio.suffix.lower()=='.wav':
        with wave.open(str(audio),'rb') as src:
            if src.getsampwidth()==2 and src.getnchannels() in (1,2):
                import array
                audible=False
                while frames:=src.readframes(16000):
                    _cancelled(cancel_event)
                    samples=array.array('h',frames)
                    if any(abs(sample)>120 for sample in samples):
                        audible=True;break
                if not audible:raise ValueError('No audible speech detected; nothing was submitted.')
    threads=max(1,min(8,hardware.cpu_threads-2))
    with tempfile.TemporaryDirectory(prefix='prometheus-voice-') as temp:
        output=Path(temp)/'transcript'
        _cancelled(cancel_event)
        if on_stage: on_stage('Microphone off · transcribing locally')
        with tempfile.TemporaryFile() as errors:
            process=subprocess.Popen([str(executable),'-m',str(weights),'-f',str(audio),'-t',str(threads),
                                      '-ng','-l','en','-nt','-otxt','-of',str(output),'-bo','1','-bs','1'],
                                     stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+300
            next_memory_check=0
            try:
                while process.poll() is None:
                    _cancelled(cancel_event)
                    now=time.monotonic()
                    if now>=deadline:
                        raise ValueError('Local transcription exceeded five minutes; recording was not submitted.')
                    if now>=next_memory_check:
                        available=detect_hardware().available_ram_gib
                        if available is not None and available<.35:
                            raise ValueError('Transcription stopped because available memory fell too low; recording was not submitted.')
                        next_memory_check=now+.5
                    cancel_event.wait(.1)
                _cancelled(cancel_event)
                if process.returncode:
                    errors.seek(0,2)
                    errors.seek(max(0,errors.tell()-600))
                    raise ValueError('Local Whisper transcription failed: '+errors.read(600).decode('utf-8',errors='replace'))
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        transcript=output.with_suffix('.txt')
        if not transcript.is_file():
            raise ValueError('Local Whisper did not produce a transcript.')
        if transcript.stat().st_size>1024*1024:
            raise ValueError('Transcript exceeds the 1 MiB limit.')
        text=transcript.read_text(encoding='utf-8-sig').strip()
        if not text:raise ValueError('No transcription returned; nothing was submitted.')
        _cancelled(cancel_event)
        return {'text':text,'model':model,'local':True}


def record_windows(destination, seconds=8, *, cancel_event=None):
    from .application_policy import require_dispatch
    require_dispatch('voice.record')
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    _cancelled(cancel_event)
    if os.name!='nt':raise ValueError('Microphone capture currently supports Windows.')
    if not 1<=seconds<=30:raise ValueError('Recording duration must be 1–30 seconds.')
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Recording destination already exists.')
    if '"' in str(destination):raise ValueError('Invalid recording path.')
    winmm=ctypes.WinDLL('winmm')
    winmm.mciSendStringW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_void_p]
    winmm.mciSendStringW.restype=ctypes.c_uint
    alias='prometheus_voice'
    def command(text):
        status=winmm.mciSendStringW(text,None,0,None)
        if status:raise ValueError(f'Windows microphone operation failed ({status}); check microphone permissions and input device.')
    opened=False
    try:
        command(f'open new type waveaudio alias {alias}');opened=True
        command(f'set {alias} time format milliseconds')
        command(f'set {alias} format tag pcm channels 1 samplespersec 16000 bitspersample 16 alignment 2 bytespersec 32000')
        print(f'MICROPHONE ON — recording {seconds} seconds. Ctrl+C cancels.',flush=True)
        command(f'record {alias}')
        cancel_event.wait(seconds)
        _cancelled(cancel_event)
        command(f'stop {alias}')
        command(f'save {alias} "{destination}"')
    finally:
        if opened:winmm.mciSendStringW(f'close {alias}',None,0,None)
        print('MICROPHONE OFF',flush=True)


def capture_prompt(*, cancel_event=None, on_stage=None):
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    _cancelled(cancel_event)
    # Check RAM and installed files before opening an input device.
    _transcriber('auto')
    with tempfile.TemporaryDirectory(prefix='prometheus-capture-') as temp:
        audio=Path(temp)/'capture.wav'
        if on_stage: on_stage('Microphone on · recording up to eight seconds; Cancel stops it')
        record_windows(audio,cancel_event=cancel_event)
        result=transcribe(audio,cancel_event=cancel_event,on_stage=on_stage)
    _cancelled(cancel_event)
    print('You said: '+result['text'],flush=True)
    return result['text']


def capture_prompt_continuous(*, cancel_event=None, finish_event=None, on_stage=None,
                              on_level=None, on_started=None, lease_alive=lambda: True):
    """UI voice turn: local capture, silence/manual finish, then local transcription."""
    from .application_policy import require_dispatch
    from .continuous_capture import record_continuous
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    _cancelled(cancel_event)
    # Refuse disabled transcription before opening a microphone or collecting audio.
    require_dispatch('voice.record')
    require_dispatch('voice.transcribe')
    _transcriber('auto')
    with tempfile.TemporaryDirectory(prefix='prometheus-capture-') as temp:
        audio = Path(temp)/'capture.wav'
        def started():
            if on_started:
                on_started()
            if on_stage:
                on_stage('Listening · pause when finished, or press Stop recording')
        record_continuous(audio, cancel_event=cancel_event, finish_event=finish_event,
                          lease_alive=lease_alive, on_level=on_level, on_started=started)
        # Device context has closed before status changes or Whisper is invoked.
        if on_stage:
            on_stage('Microphone off · transcribing locally')
        result = transcribe(audio, cancel_event=cancel_event)
    _cancelled(cancel_event)
    return result['text']


def speak(text):
    from .application_policy import require_dispatch
    require_dispatch('voice.speak')
    if os.name!='nt':raise ValueError('Speech output currently supports Windows SAPI.')
    script="$ErrorActionPreference='Stop'; $voice=New-Object -ComObject SAPI.SpVoice; $utterance=[Console]::In.ReadToEnd(); [void]$voice.Speak($utterance)"
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],input=text[:4000],text=True,
                          capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Windows speech output failed; the answer remains visible in text.')


def synthesize_wav(text, *, cancelled=lambda: False):
    """Render installed Windows speech to bounded PCM; this never plays or records audio."""
    from .application_policy import require_dispatch
    require_dispatch('voice.speak')
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or '\x00' in text:
        raise ValueError('Speech requires 1–4000 characters of answer text.')
    if os.name != 'nt':
        raise ValueError('Local speech output requires Windows. The answer remains in text.')
    if cancelled():
        raise ValueError('Voice preparation stopped.')
    # Text travels through stdin; neither answer text nor a request path becomes code.
    script = """$ErrorActionPreference='Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
 $format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
 $voice.SetOutputToWaveFile($env:PROMETHEUS_SPEECH_OUTPUT,$format)
 $voice.Speak([Console]::In.ReadToEnd())
} finally { $voice.Dispose() }
"""
    with tempfile.TemporaryDirectory(prefix='prometheus-speech-') as temp:
        destination = Path(temp)/'answer.wav'
        env = {**os.environ, 'PROMETHEUS_SPEECH_OUTPUT': str(destination)}
        with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as input_stream:
            input_stream.write(text.encode('utf-8'))
            input_stream.seek(0)
            process = subprocess.Popen(['powershell.exe', '-NoProfile', '-Command', script],
                                       stdin=input_stream, stdout=subprocess.DEVNULL, stderr=errors,
                                       text=True, encoding='utf-8', env=env,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic()+45
                while process.poll() is None:
                    if cancelled():
                        raise ValueError('Voice preparation stopped.')
                    if time.monotonic() >= deadline or (destination.exists() and destination.stat().st_size > 9_600_256):
                        raise ValueError('Local voice output exceeded its limit; use text or a shorter answer.')
                    time.sleep(.05)
                if process.returncode or cancelled() or not destination.is_file():
                    raise ValueError('Local voice preparation failed; your answer remains in text.')
                if destination.stat().st_size > 9_600_256:
                    raise ValueError('Local voice output exceeded five minutes.')
                with wave.open(str(destination), 'rb') as audio:
                    if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or not 0 < audio.getnframes() <= 4_800_000:
                        raise ValueError('Invalid local voice output.')
                return destination.read_bytes()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
