"""Bounded lifetime and output for the isolated, pinned caption interpreter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def read_transcript(url, *, cancel_event=None):
    from .application_policy import require_dispatch
    from .public_reach import youtube_url, _check_cancel
    require_dispatch('reach.read')
    require_dispatch('public.fetch')
    url = youtube_url(url)
    _check_cancel(cancel_event)
    # -I -S excludes user/site packages, PYTHONPATH, and Python startup hooks.
    # No provider tokens, cookies, proxy settings, or home configuration is read.
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE'}}
    args = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('transcript_worker.py')), url]
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    chunks, size, overflow = [], [0], threading.Event()
    def collect():
        while True:
            block = process.stdout.read(16384)
            if not block:
                return
            size[0] += len(block)
            if size[0] > 1_000_000:
                overflow.set()
                return
            chunks.append(block)
    thread = threading.Thread(target=collect, daemon=True, name='Prometheus-caption-output')
    thread.start()
    deadline = time.monotonic()+65
    try:
        while process.poll() is None:
            _check_cancel(cancel_event)
            if overflow.is_set():
                raise RuntimeError('The transcript reader exceeded its output limit.')
            if time.monotonic() > deadline:
                raise RuntimeError('The public transcript read timed out.')
            time.sleep(.05)
        thread.join(2)
        _check_cancel(cancel_event)
        if overflow.is_set() or thread.is_alive():
            raise RuntimeError('The transcript reader did not return bounded output.')
        try:
            result = json.loads(b''.join(chunks))
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('The transcript reader returned an invalid result.') from error
        if process.returncode or 'error' in result:
            raise RuntimeError(str(result.get('error', 'The public transcript is unavailable.'))[:400])
        if not isinstance(result.get('segments'), list) or not result['segments'] or result.get('url') != url:
            raise RuntimeError('The transcript reader returned no matching captions.')
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)
        thread.join(2)
        process.stdout.close()
