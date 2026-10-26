"""Native Ubuntu audio adapters. Nothing records or plays at import time."""
import array
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave


def runtime_root():
    for parent in Path(__file__).resolve().parents:
        candidate = parent/'runtime/ubuntu-voice'
        if candidate.is_dir():
            return candidate
    raise ValueError('The native Ubuntu voice runtime is missing. Run voice setup.')


def native_environment():
    root = runtime_root()
    library = root/'usr/lib/x86_64-linux-gnu'
    return {**os.environ, 'LD_LIBRARY_PATH': str(library),
            'GGML_BACKEND_PATH': str(library/'ggml/backends0/libggml-cpu-x64.so')}


def readiness():
    try:
        root = runtime_root()
        return (shutil.which('arecord') is not None and
                (root/'usr/bin/whisper-cli').is_file() and
                (root/'usr/bin/espeak-ng').is_file() and
                all((root/'models'/f'ggml-{name}.bin').is_file() for name in ('base.en', 'tiny.en')))
    except (OSError, ValueError):
        return False


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def normalize_wav(path):
    """Bounded linear PCM conversion from eSpeak's native 22050 Hz to 16 kHz."""
    if path.stat().st_size > 13_230_256:
        raise ValueError('Local speech output exceeded five minutes.')
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 22050):
            raise ValueError('Invalid native speech format.')
        if not 0 < audio.getnframes() <= 6_615_000:
            raise ValueError('Invalid native speech duration.')
        samples = array.array('h', audio.readframes(audio.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    count = len(samples)*16000//22050
    output = array.array('h')
    for index in range(count):
        position, fraction = divmod(index*22050, 16000)
        left, right = samples[position], samples[min(position+1, len(samples)-1)]
        output.append(round(left+(right-left)*fraction/16000))
    if sys.byteorder != 'little':
        output.byteswap()
    result = io.BytesIO()
    with wave.open(result, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(output.tobytes())
    return result.getvalue()


def synthesize(text, cancelled):
    with tempfile.TemporaryDirectory(prefix='prometheus-speech-') as temp:
        destination = Path(temp)/'answer.wav'
        with tempfile.TemporaryFile() as input_stream:
            input_stream.write(text.encode('utf-8')); input_stream.seek(0)
            process = subprocess.Popen([str(runtime_root()/'usr/bin/espeak-ng'), '-v', 'en-us',
                                        '-s', '155', '--stdin', '-w', str(destination)],
                                       stdin=input_stream, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=native_environment())
            try:
                deadline = time.monotonic()+45
                while process.poll() is None:
                    if cancelled():
                        raise ValueError('Voice preparation stopped.')
                    if time.monotonic() >= deadline or (destination.exists() and destination.stat().st_size > 13_230_256):
                        raise ValueError('Local voice output exceeded its limit.')
                    time.sleep(.05)
                if process.returncode or cancelled() or not destination.is_file():
                    raise ValueError('Local speech preparation failed; the answer remains in text.')
                result = normalize_wav(destination)
                if cancelled():
                    raise ValueError('Voice preparation stopped.')
                return result
            finally:
                stop(process)


class LinuxInput:
    """One owned ALSA/PipeWire capture process with bounded nonblocking reads."""
    def __init__(self):
        self.process = None
        self.stopped = False
        self.partial = b''

    def __enter__(self):
        executable = shutil.which('arecord')
        if not executable:
            raise ValueError('Ubuntu microphone capture needs alsa-utils.')
        try:
            self.process = subprocess.Popen([executable, '-q', '-D', 'default', '-t', 'raw',
                                             '-f', 'S16_LE', '-r', '16000', '-c', '1'],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(self.process.stdout.fileno(), False)
            return self
        except BaseException:
            self.close()
            raise

    def read(self):
        if not self.process:
            raise ValueError('Microphone is not open.')
        try:
            data = os.read(self.process.stdout.fileno(), 5120)
        except BlockingIOError:
            data = b''
        if not data and not self.stopped and self.process.poll() is not None:
            raise ValueError('Ubuntu microphone disconnected or could not open; check Sound input settings.')
        data = self.partial+data
        self.partial = data[-1:] if len(data) % 2 else b''
        return data[:len(data)-len(self.partial)]

    def finish(self):
        self.stopped = True
        stop(self.process)
        parts = []
        # An owned pipe can retain only a bounded amount after its writer exits.
        for _ in range(64):
            data = self.read()
            if not data:
                break
            parts.append(data)
        if self.partial:
            raise ValueError('Microphone returned an incomplete PCM sample.')
        return b''.join(parts)

    def close(self):
        if self.process:
            stop(self.process)
            if self.process.stdout:
                self.process.stdout.close()
            self.process = None

    def __exit__(self, *args):
        self.close()


def record(destination, seconds, cancel_event):
    from .voice import _cancelled
    output = destination.open('xb')
    completed = False
    try:
        with output, wave.open(output, 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            with LinuxInput() as device:
                print('MICROPHONE ON — requested Ubuntu recording.', flush=True)
                deadline = time.monotonic()+seconds
                while time.monotonic() < deadline:
                    _cancelled(cancel_event)
                    audio.writeframesraw(device.read())
                    cancel_event.wait(.02)
                _cancelled(cancel_event)
                audio.writeframes(device.finish())
        completed = True
    finally:
        if not completed:
            destination.unlink(missing_ok=True)
        print('MICROPHONE OFF', flush=True)
