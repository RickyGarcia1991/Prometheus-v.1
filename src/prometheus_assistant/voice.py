"""On-demand local voice; recording never starts during import or diagnostics."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import time
import wave
from .hardware import detect_hardware, resource_root


def voice_paths():
    root = resource_root()
    if not root:
        raise ValueError('Portable resources are not configured.')
    folder = Path(root)/'Voice/Whisper-b5454'
    return folder/'Release/whisper-cli.exe', folder/'models'


def voice_status():
    executable, models = voice_paths()
    return {'transcriber_installed': executable.is_file(),
            'models': {name:(models/f'ggml-{name}.bin').is_file() for name in ('tiny.en','base.en')},
            'speech_recognition':'OpenAI Whisper via whisper.cpp, local English models',
            'microphone':'off; starts only when requested in voice-chat',
            'speech_output':'Windows installed SAPI voices; no online voice service',
            'device_tested':False}


def transcribe(audio, *, model='auto'):
    audio = Path(audio).resolve(strict=True)
    if not audio.is_file() or audio.suffix.lower() not in ('.wav','.mp3','.flac','.ogg'):
        raise ValueError('Choose a WAV, MP3, FLAC or OGG audio file.')
    if audio.stat().st_size > 100*1024*1024:
        raise ValueError('Audio is limited to 100 MiB per request.')
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
    # Reject silent PCM captures before decoding; Whisper can hallucinate on silence.
    if audio.suffix.lower()=='.wav':
        with wave.open(str(audio),'rb') as src:
            if src.getsampwidth()==2 and src.getnchannels() in (1,2):
                import array
                audible=False
                while frames:=src.readframes(16000):
                    samples=array.array('h',frames)
                    if any(abs(sample)>120 for sample in samples):
                        audible=True;break
                if not audible:raise ValueError('No audible speech detected; nothing was submitted.')
    threads=max(1,min(8,hardware.cpu_threads-2))
    with tempfile.TemporaryDirectory(prefix='prometheus-voice-') as temp:
        output=Path(temp)/'transcript'
        result=subprocess.run([str(executable),'-m',str(weights),'-f',str(audio),'-t',str(threads),
                               '-ng','-l','en','-nt','-otxt','-of',str(output),'-bo','1','-bs','1'],
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=300,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        transcript=output.with_suffix('.txt')
        if result.returncode or not transcript.is_file():
            raise ValueError('Local Whisper transcription failed: '+result.stderr[-600:])
        text=transcript.read_text(encoding='utf-8-sig').strip()
        if not text:raise ValueError('No transcription returned; nothing was submitted.')
        return {'text':text,'model':model,'local':True}


def record_windows(destination, seconds=8):
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
        time.sleep(seconds)
        command(f'stop {alias}')
        command(f'save {alias} "{destination}"')
    finally:
        if opened:winmm.mciSendStringW(f'close {alias}',None,0,None)
        print('MICROPHONE OFF',flush=True)


def capture_prompt():
    with tempfile.TemporaryDirectory(prefix='prometheus-capture-') as temp:
        audio=Path(temp)/'capture.wav'
        record_windows(audio)
        result=transcribe(audio)
    print('You said: '+result['text'],flush=True)
    return result['text']


def speak(text):
    if os.name!='nt':raise ValueError('Speech output currently supports Windows SAPI.')
    script="$ErrorActionPreference='Stop'; $voice=New-Object -ComObject SAPI.SpVoice; $utterance=[Console]::In.ReadToEnd(); [void]$voice.Speak($utterance)"
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],input=text[:4000],text=True,
                          capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Windows speech output failed; the answer remains visible in text.')
