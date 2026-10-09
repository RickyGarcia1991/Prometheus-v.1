"""Readiness follows the local API, never inherited helper stdout pipes."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from prometheus_assistant import ui_server
from prometheus_assistant.ollama import GenerationCancelled, LocalModelError, OllamaClient


class Helper:
    def __init__(self, returncode=None, *, needs_kill=False):
        self.returncode = returncode
        self.needs_kill = needs_kill
        self.terminated = 0
        self.killed = 0
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if not self.needs_kill:
            self.returncode = -15

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        assert timeout is not None and 0 < timeout <= 5, 'Helper cleanup waits must be bounded.'
        if self.returncode is None:
            raise subprocess.TimeoutExpired('test helper', timeout)
        return self.returncode

    def communicate(self, *args, **kwargs):
        pytest.fail('Runtime readiness must not wait for inherited helper pipe EOF.')


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance = ui_server.Interface(tmp_path / 'memory.sqlite3')
    monkeypatch.setattr(ui_server, 'resource_root', lambda: str(tmp_path / 'resources'))
    yield instance
    instance.close()


def helper_factory(monkeypatch, helper, *, on_spawn=None, stderr=''):
    spawned = []
    def spawn(argv, **kwargs):
        spawned.append((argv, kwargs))
        assert kwargs['stdout'] == subprocess.DEVNULL
        assert kwargs['stderr'] != subprocess.PIPE
        if stderr:
            try:
                kwargs['stderr'].write(stderr.encode())
            except TypeError:
                kwargs['stderr'].write(stderr)
            kwargs['stderr'].flush()
        if on_spawn:
            on_spawn()
        return helper
    monkeypatch.setattr(ui_server.subprocess, 'Popen', spawn)
    return spawned


def fake_inventory(monkeypatch, check):
    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs.get('timeout', 120) <= 2
        def local_models(self):
            return check()
    monkeypatch.setattr(ui_server, 'OllamaClient', Client)


def unavailable():
    raise LocalModelError('Local fixture is not ready')


def test_already_ready_runtime_does_not_spawn_helper(app, monkeypatch):
    fake_inventory(monkeypatch, lambda: [])
    monkeypatch.setattr(ui_server.subprocess, 'Popen', lambda *a, **k: pytest.fail('Already ready API needs no helper.'))
    app.ensure_runtime()


@pytest.mark.parametrize('needs_kill', [False, True])
def test_cancel_while_unready_cleans_owned_helper(app, monkeypatch, needs_kill):
    fake_inventory(monkeypatch, unavailable)
    helper = Helper(needs_kill=needs_kill)
    spawned = helper_factory(monkeypatch, helper, on_spawn=app.cancel_event.set)
    with pytest.raises(GenerationCancelled):
        app.ensure_runtime()
    assert len(spawned) == 1 and helper.terminated == 1
    assert helper.killed == int(needs_kill)
    assert helper.wait_timeouts


def test_failed_helper_surfaces_bounded_stderr(app, monkeypatch):
    fake_inventory(monkeypatch, unavailable)
    helper = Helper(returncode=7)
    helper_factory(monkeypatch, helper, stderr='Runtime binary missing: fixture failure')
    with pytest.raises(RuntimeError, match='Runtime binary missing: fixture failure'):
        app.ensure_runtime()
    assert helper.terminated == 0


def test_zero_helper_exit_cannot_substitute_for_api_readiness(app, monkeypatch):
    fake_inventory(monkeypatch, unavailable)
    helper_factory(monkeypatch, Helper(returncode=0))
    elapsed = [0.0]
    def monotonic():
        elapsed[0] += 40
        return elapsed[0]
    monkeypatch.setattr(ui_server, 'time', SimpleNamespace(monotonic=monotonic, sleep=lambda seconds: None))
    with pytest.raises(RuntimeError, match='ready|startup|runtime'):
        app.ensure_runtime()


@contextmanager
def local_inventory_server(ready):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            body = json.dumps({'models': []} if ready.is_set() else {'error': 'Starting'}).encode()
            self.send_response(200 if ready.is_set() else 503)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('helper_exit', [None, 0])
def test_real_http_ready_returns_without_helper_eof(app, monkeypatch, helper_exit):
    ready = threading.Event()
    helper = Helper(returncode=helper_exit)
    spawned = helper_factory(monkeypatch, helper, on_spawn=ready.set)
    with local_inventory_server(ready) as (url, calls):
        monkeypatch.setattr(ui_server, 'OllamaClient', lambda **kwargs: OllamaClient(url, **kwargs))
        started = time.monotonic()
        app.ensure_runtime()
        assert time.monotonic()-started < 2
        assert calls.count('/api/tags') >= 2, 'Require an actual post-spawn ready response.'
    assert len(spawned) == 1
    if helper_exit is None:
        assert helper.terminated == 1 and helper.wait_timeouts
    else:
        assert helper.terminated == 0
