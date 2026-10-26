import ctypes
import subprocess
import sys
import threading
import time
import wave
import pytest
from prometheus_assistant import voice
from prometheus_assistant.hardware import HardwareProfile
from prometheus_assistant.ollama import GenerationCancelled


@pytest.fixture
def audio_runtime(tmp_path,monkeypatch):
    audio=tmp_path/'speech.wav'
    with wave.open(str(audio),'wb') as dst:
        dst.setnchannels(1);dst.setsampwidth(2);dst.setframerate(16000)
        dst.writeframes(b'\xff\x10'*16000)
    exe=tmp_path/'whisper.exe';exe.write_bytes(b'fixture')
    (tmp_path/'ggml-tiny.en.bin').write_bytes(b'fixture')
    monkeypatch.setattr(voice,'voice_paths',lambda:(exe,tmp_path))
    monkeypatch.setattr(voice,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',1.5))
    return audio


def test_cancelled_capture_does_not_open_microphone(monkeypatch):
    event=threading.Event();event.set()
    monkeypatch.setattr(voice,'record_windows',lambda *a,**k:pytest.fail('Microphone must stay closed'))
    with pytest.raises(GenerationCancelled):voice.capture_prompt(cancel_event=event)


def test_preflight_rejects_low_ram_before_microphone(monkeypatch):
    monkeypatch.setattr(voice,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',.2))
    monkeypatch.setattr(voice,'voice_paths',lambda:(None,None))
    monkeypatch.setattr(voice,'record_windows',lambda *a,**k:pytest.fail('Microphone must stay closed'))
    with pytest.raises(ValueError,match='RAM'):voice.capture_prompt()


def test_missing_runtime_checked_before_microphone(tmp_path,monkeypatch):
    monkeypatch.setattr(voice,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',1.5))
    monkeypatch.setattr(voice,'voice_paths',lambda:(tmp_path/'absent.exe',tmp_path))
    monkeypatch.setattr(voice,'record_windows',lambda *a,**k:pytest.fail('Microphone must stay closed'))
    with pytest.raises(ValueError,match='missing'):voice.capture_prompt()


def test_record_cancel_closes_device_without_saving(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(voice, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(voice, 'os', SimpleNamespace(name='nt'))
    event=threading.Event();calls=[]
    class Command:
        def __call__(self,text,*args):
            calls.append(text)
            if text.startswith('record '):event.set()
            return 0
    class WinMM:mciSendStringW=Command()
    monkeypatch.setattr(ctypes,'WinDLL',lambda name:WinMM(),raising=False)
    with pytest.raises(GenerationCancelled):
        voice.record_windows(tmp_path/'unused.wav',cancel_event=event)
    assert calls[-1]=='close prometheus_voice'
    assert not any(command.startswith('save ') for command in calls)


@pytest.mark.parametrize('reason',['cancel','memory'])
def test_owned_decoder_is_stopped_and_reaped(audio_runtime,monkeypatch,reason):
    original=subprocess.Popen;children=[];event=threading.Event()
    def start(argv,**options):
        child=original([sys.executable,'-B','-c','import time; time.sleep(30)'],**options)
        children.append(child)
        if reason=='cancel':event.set()
        else:monkeypatch.setattr(voice,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',.2))
        return child
    monkeypatch.setattr(voice.subprocess,'Popen',start)
    before=time.monotonic()
    with pytest.raises(GenerationCancelled if reason=='cancel' else ValueError):
        voice.transcribe(audio_runtime,cancel_event=event)
    assert time.monotonic()-before<5
    assert children and children[0].poll() is not None


def test_successful_decode_returns_transcript_and_stage(audio_runtime,monkeypatch):
    original=subprocess.Popen;stages=[]
    def start(argv,**options):
        output=argv[argv.index('-of')+1]+'.txt'
        return original([sys.executable,'-B','-c',
                         'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("Blue bicycle 42.")',output],**options)
    monkeypatch.setattr(voice.subprocess,'Popen',start)
    result=voice.transcribe(audio_runtime,on_stage=stages.append)
    assert result=={'text':'Blue bicycle 42.','model':'tiny.en','local':True}
    assert stages==['Microphone off · transcribing locally']


def test_decoder_error_is_bounded(audio_runtime,monkeypatch):
    original=subprocess.Popen
    def start(argv,**options):
        return original([sys.executable,'-B','-c','import sys;sys.stderr.write("x"*5000);sys.exit(2)'],**options)
    monkeypatch.setattr(voice.subprocess,'Popen',start)
    with pytest.raises(ValueError,match='transcription failed') as result:voice.transcribe(audio_runtime)
    assert len(str(result.value))<700


def test_transcription_diagnostics_contain_only_bounded_numeric_timings(audio_runtime, monkeypatch):
    original = subprocess.Popen
    diagnostics = []
    def start(argv, **options):
        output = argv[argv.index('-of')+1]+'.txt'
        script = ('import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("private fixture words");'
            'sys.stderr.write("secret/path private words\\nwhisper_print_timings: load time = 125.00 ms\\n'
            'whisper_print_timings: encode time = 222.00 ms / 1 runs\\n'
            'whisper_print_timings: decode time = 99999999999 ms\\n")')
        return original([sys.executable, '-B', '-c', script, output], **options)
    monkeypatch.setattr(voice.subprocess, 'Popen', start)
    result = voice.transcribe(audio_runtime, on_diagnostics=diagnostics.append)
    assert result['text'] == 'private fixture words'
    assert len(diagnostics) == 1
    assert diagnostics[0]['asr_load_seconds'] == .125
    assert diagnostics[0]['asr_encode_seconds'] == .222
    assert 'asr_decode_seconds' not in diagnostics[0]
    assert all(type(v) in (int, float) and 0 <= v <= 3600 for v in diagnostics[0].values())
    assert 0 < diagnostics[0]['transcription_seconds'] < 5
