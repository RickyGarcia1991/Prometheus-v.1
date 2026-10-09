from pathlib import Path
import wave
import pytest
from prometheus_assistant import voice
from prometheus_assistant.hardware import HardwareProfile


def test_silence_is_not_sent_to_whisper(monkeypatch,tmp_path):
    exe=tmp_path/'whisper.exe';exe.write_bytes(b'fixture')
    (tmp_path/'ggml-tiny.en.bin').write_bytes(b'model')
    audio=tmp_path/'silence.wav'
    with wave.open(str(audio),'wb') as dst:
        dst.setnchannels(1);dst.setsampwidth(2);dst.setframerate(16000);dst.writeframes(b'\0'*32000)
    monkeypatch.setattr(voice,'voice_paths',lambda:(exe,tmp_path))
    monkeypatch.setattr(voice,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',1))
    monkeypatch.setattr(voice.subprocess,'run',lambda *a,**k:pytest.fail('Silence must not run inference'))
    with pytest.raises(ValueError,match='No audible speech'):
        voice.transcribe(audio)


def test_speak_treats_text_as_data_and_uses_no_network(monkeypatch):
    calls=[]
    class Result:returncode=0
    monkeypatch.setattr(voice.subprocess,'run',lambda *a,**k:calls.append((a,k)) or Result())
    utterance='$(Remove-Item anything) "literal text"'
    voice.speak(utterance)
    args,kwargs=calls[0]
    assert kwargs['input']==utterance
    assert utterance not in ' '.join(args[0])
    assert 'shell' not in kwargs


def test_voice_status_does_not_access_microphone(monkeypatch,tmp_path):
    monkeypatch.setattr(voice,'voice_paths',lambda:(tmp_path/'absent.exe',tmp_path))
    monkeypatch.setattr(voice,'record_windows',lambda *a:pytest.fail('Read-only status must not record'))
    assert voice.voice_status()['microphone'].startswith('off')
