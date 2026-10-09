"""Pressure-triggered disconnects, bounded unloads and honest source fallback."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import select
import threading
import time

import pytest

from prometheus_assistant import compact_chat, ui_server
from prometheus_assistant.hardware import HardwareProfile, ModelProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.ollama import GenerationCancelled, OllamaClient


@contextmanager
def pressure_server(*, unload_stalls=False):
    disconnected, stop = threading.Event(), threading.Event()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def do_GET(self):
            data = json.dumps({'models': [{'name': 'first:tiny'}, {'name': 'second:tiny'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                if self.path == '/api/generate' and not unload_stalls:
                    self.send_response(200)
                    self.send_header('Content-Length', '2')
                    self.end_headers()
                    self.wfile.write(b'{}')
                    return
                if self.path == '/api/chat':
                    self.send_response(200)
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    line = json.dumps({'message': {'role': 'assistant', 'content': 'Incomplete text'}, 'done': False}).encode()+b'\n'
                    self.wfile.write(f'{len(line):X}\r\n'.encode()+line+b'\r\n')
                    self.wfile.flush()
                deadline = time.monotonic()+5
                while not stop.is_set() and time.monotonic() < deadline:
                    ready, _, _ = select.select([self.connection], [], [], .02)
                    if ready and not self.connection.recv(1):
                        if self.path == '/api/chat':
                            disconnected.set()
                        return
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, disconnected
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_memory_signal_samples_pressure_at_half_second_intervals(monkeypatch):
    clock, available, observations = [0.0], [1.0], []
    def hardware():
        observations.append(clock[0])
        return HardwareProfile(8, 4, 'Windows', available[0])
    monkeypatch.setattr(compact_chat, 'detect_hardware', hardware)
    monkeypatch.setattr(compact_chat.time, 'monotonic', lambda: clock[0])
    user = threading.Event()
    signal = compact_chat.MemorySignal(user)
    assert signal.is_set() is False
    available[0], clock[0] = .2, .49
    assert signal.is_set() is False and observations == [0]
    clock[0] = .5
    assert signal.is_set() is True and signal.pressure is True
    user.set()
    clock[0] = 1
    assert signal.is_set() is True and observations == [0, .5]


@pytest.mark.parametrize('available, pressured', [(None, False), (.35, False), (.349, True)])
def test_pressure_threshold_and_unknown_reading(monkeypatch, available, pressured):
    monkeypatch.setattr(compact_chat, 'detect_hardware', lambda: HardwareProfile(8, 4, 'Windows', available))
    assert compact_chat.MemorySignal(None).is_set() is pressured


@pytest.mark.parametrize('unload_stalls', [False, True])
def test_midstream_memory_drop_disconnects_unloads_once_and_saves_nothing(tmp_path, monkeypatch, unload_stalls):
    free = [1.0]
    timeouts = []
    class ObservedClient(OllamaClient):
        def _request(self, path, payload=None):
            timeouts.append((path, self.timeout))
            return super()._request(path, payload)
    monkeypatch.setattr(compact_chat, 'OllamaClient', ObservedClient)
    monkeypatch.setattr(compact_chat, 'detect_hardware', lambda: HardwareProfile(8, 4, 'Windows', free[0]))
    evidence = ModelEvidence(tmp_path / 'evidence.sqlite3')
    memory_path = tmp_path / 'memory.sqlite3'
    with MemoryStore(memory_path) as memory:
        session = memory.create_session('original')
        memory.save_exchange(session, 'Previous question', 'Previous complete answer')
    profiles = [ModelProfile(name, 1, 1024, 'light', 1) for name in ('first:tiny', 'second:tiny')]
    tokens = []
    def partial(text):
        tokens.append(text)
        free[0] = .2
    with pressure_server(unload_stalls=unload_stalls) as (url, requests, disconnected):
        start = time.monotonic()
        with pytest.raises(compact_chat.ModelMemoryPressure):
            compact_chat.generate({'prompt': 'New question', 'session': session}, memory_path,
                                  profiles, 'general', evidence, base_url=url, on_token=partial)
        assert time.monotonic()-start < (3.5 if unload_stalls else 1.5)
        assert disconnected.wait(.5), 'Memory pressure must close the actual inference request.'
        assert [path for path, _ in requests] == ['/api/chat', '/api/generate']
        assert requests[-1][1] == {'model': 'first:tiny', 'keep_alive': 0}
    assert tokens == ['Incomplete text']
    assert timeouts == [('/api/tags', 3), ('/api/generate', 2)]
    assert evidence.summary()['failures'] == []
    with MemoryStore(memory_path) as memory:
        assert [row['content'] for row in memory.history(session)] == ['Previous question', 'Previous complete answer']
        assert memory.db.execute('SELECT COUNT(*) FROM model_events').fetchone()[0] == 0


def test_user_cancel_takes_precedence_over_low_memory(tmp_path, monkeypatch):
    free = [1.0]
    user = threading.Event()
    monkeypatch.setattr(compact_chat, 'detect_hardware', lambda: HardwareProfile(8, 4, 'Windows', free[0]))
    evidence = ModelEvidence(tmp_path / 'evidence.sqlite3')
    def partial(text):
        free[0] = .2
        user.set()
    with pressure_server() as (url, requests, disconnected):
        with pytest.raises(GenerationCancelled):
            compact_chat.generate({'prompt': 'New question'}, tmp_path / 'memory.sqlite3',
                [ModelProfile('first:tiny', 1, 1024, 'light', 1)], 'general', evidence,
                base_url=url, on_token=partial, cancel_event=user)
        assert disconnected.wait(.5)
        assert [path for path, _ in requests] == ['/api/chat', '/api/generate']
    assert evidence.summary()['failures'] == []
    with MemoryStore(tmp_path / 'memory.sqlite3') as memory:
        assert memory.sessions() == []


def test_interface_switches_to_cited_sources_after_pressure_without_saving_partial(tmp_path, monkeypatch):
    app = ui_server.Interface(tmp_path / 'memory.sqlite3')
    monkeypatch.setattr(ui_server, 'detect_hardware', lambda: HardwareProfile(8, 4, 'Windows', 4))
    monkeypatch.setattr(ui_server, 'installed_models', lambda: {'qwen3:0.6b'})
    monkeypatch.setattr(OllamaClient, 'resident_memory_gib', lambda self: 0)
    monkeypatch.setattr(app, 'ensure_runtime', lambda: None)
    stages = []
    original_progress = app.progress
    def progress(text, **kwargs):
        stages.append((text, kwargs))
        return original_progress(text, **kwargs)
    monkeypatch.setattr(app, 'progress', progress)
    def generate(*args, on_token, **kwargs):
        on_token('Incomplete model answer that must never be saved')
        raise compact_chat.ModelMemoryPressure('Available memory fell during generation.')
    monkeypatch.setattr(compact_chat, 'generate', generate)
    monkeypatch.setattr(ui_server, 'reference_query', lambda query, profile: {
        'results': [{'title': 'Official court source', 'excerpt': 'Court filing evidence.', 'url': 'https://www.uscourts.gov/'}]})
    try:
        job = app.submit({'mode': 'chat', 'prompt': 'court filing procedure'})
        app.executor.submit(lambda: None).result(timeout=3)
        finished = app.jobs[job['id']]
        assert finished['state'] == 'done'
        result = finished['result']
        assert result['label'] == 'Automatic memory recovery · source evidence'
        assert 'not a generated answer' in result['details']['memory_note']
        assert 'uscourts.gov' in result['reply']
        assert ('Memory pressure · switching to source evidence', {'reset': True}) in stages
        with MemoryStore(app.memory_path) as memory:
            history = memory.history(result['session'])
            assert len(history) == 2 and 'Incomplete model answer' not in history[1]['content']
        assert app.model_evidence.summary()['failures'] == []
    finally:
        app.close()
