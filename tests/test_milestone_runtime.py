"""Small real-loopback contracts for the integrated portable milestones."""
from contextlib import contextmanager
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import select
import sqlite3
import threading
import time

import pytest

from prometheus_assistant.compact_chat import candidates, generate, messages_for
from prometheus_assistant.hardware import HardwareProfile, ModelProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.ollama import GenerationCancelled, LocalModelError, OllamaClient
from prometheus_assistant import ui_server, ui_workspace


@contextmanager
def streaming_server(scenarios, disconnected=None, *, http11=False):
    reached, release = threading.Event(), threading.Event()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        # Close-delimited HTTP/1.0 streams exercise cancellation after getresponse
        # transfers the connection socket to HTTPResponse.
        protocol_version = 'HTTP/1.1' if http11 else 'HTTP/1.0'
        def log_message(self, *args):
            pass
        def do_GET(self):
            data = json.dumps({'models': [{'name': name} for name in scenarios]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(payload)
                scenario = scenarios[payload['model']]
                def stall():
                    reached.set()
                    deadline = time.monotonic()+3
                    while not release.is_set() and time.monotonic() < deadline:
                        readable, _, _ = select.select([self.connection], [], [], .02)
                        if readable and not self.connection.recv(1):
                            if disconnected is not None:
                                disconnected.set()
                            return False
                    return True
                if scenario == 'before_headers':
                    if not stall():
                        return
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                if http11:
                    self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                self.wfile.flush()
                if scenario == 'after_headers':
                    if not stall():
                        return
                if isinstance(scenario, list):
                    events = scenario
                else:
                    events = [{'message': {'role': 'assistant', 'content': 'Helpful '}, 'done': False},
                              {'message': {'role': 'assistant', 'content': 'answer.'}, 'done': True,
                               'done_reason': 'stop', 'eval_count': 3, 'load_duration': 10_000_000}]
                for event in events:
                    line = event if isinstance(event, bytes) else json.dumps(event).encode()+b'\n'
                    self.wfile.write((f'{len(line):X}\r\n'.encode()+line+b'\r\n') if http11 else line)
                    self.wfile.flush()
                if http11:
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', reached, release, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('http11', [False, True])
def test_real_http_stream_delivers_tokens_metrics_and_bounded_options(http11):
    with streaming_server({'qwen3:0.6b': 'good'}, http11=http11) as (url, _, _, requests):
        client = OllamaClient(url, 'qwen3:0.6b', num_ctx=1024, num_thread=2)
        pieces = []
        reply, count = client.chat_stream([{'role': 'user', 'content': 'Hello'}], on_token=pieces.append)
    assert reply == 'Helpful answer.' and pieces == ['Helpful ', 'answer.'] and count == 3
    assert client.last_metrics['first_token_seconds'] >= 0
    assert client.last_metrics['load_seconds'] == .01
    assert requests[0]['think'] is False and requests[0]['stream'] is True
    assert requests[0]['options']['num_ctx'] == 1024


@pytest.mark.parametrize('events', [
    [{'message': {'content': 'Partial'}, 'done': False}],
    [b'{not-json}\n'],
    [{'message': {'content': 'Partial'}, 'done': True, 'done_reason': 'length'}],
    [{'message': None, 'done': True}],
    [{'message': ['wrong schema'], 'done': True}],
    [{'message': {'content': 'Answer'}, 'done': True, 'load_duration': None}],
])
def test_incomplete_or_malformed_stream_raises_recoverable_model_error(events):
    with streaming_server({'first:tiny': events}) as (url, _, _, requests):
        client = OllamaClient(url, 'first:tiny', timeout=3)
        with pytest.raises(LocalModelError):
            client.chat_stream([{'role': 'user', 'content': 'Hello'}])
        assert len(requests) == 1, 'A connection failure must not masquerade as malformed-stream validation.'


@pytest.mark.parametrize('scenario', ['before_headers', 'after_headers'])
@pytest.mark.parametrize('http11', [False, True])
def test_cancel_interrupts_stalled_http_promptly(scenario, http11):
    disconnected = threading.Event()
    with streaming_server({'first:tiny': scenario}, disconnected, http11=http11) as (url, reached, release, _):
        cancelled = threading.Event()
        results = []
        def run():
            try:
                OllamaClient(url, 'first:tiny', timeout=5).chat_stream(
                    [{'role': 'user', 'content': 'Hello'}], cancel_event=cancelled)
            except Exception as error:
                results.append(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        assert reached.wait(2)
        cancelled.set()
        worker.join(1)
        was_still_waiting = worker.is_alive()
        saw_disconnect = disconnected.wait(.5)
        release.set()
        worker.join(2)
        assert not was_still_waiting, 'Cancellation must disconnect a stalled owned HTTP stream within one second.'
        assert saw_disconnect, 'The publisher must observe socket closure; cancellation cannot leave the request running.'
        assert results and isinstance(results[0], GenerationCancelled)


def test_stalled_stream_obeys_total_deadline_and_disconnects():
    disconnected = threading.Event()
    with streaming_server({'first:tiny': 'after_headers'}, disconnected, http11=True) as (url, _, _, _):
        start = time.monotonic()
        with pytest.raises(LocalModelError, match='time budget'):
            OllamaClient(url, 'first:tiny', timeout=.25).chat_stream([{'role': 'user', 'content': 'Hello'}])
        assert time.monotonic()-start < 1
        assert disconnected.wait(.5)


def test_cancelled_generation_saves_no_partial_exchange(tmp_path):
    evidence = ModelEvidence(tmp_path / 'evidence.db')
    memory_path = tmp_path / 'memory.sqlite3'
    cancelled = threading.Event()
    profile = ModelProfile('first:tiny', 1, 1024, 'light', 1)
    with streaming_server({'first:tiny': 'good'}) as (url, _, _, _):
        with pytest.raises(GenerationCancelled):
            generate({'prompt': 'Hello'}, memory_path, [profile], 'general', evidence,
                     base_url=url, cancel_event=cancelled, on_token=lambda text: cancelled.set())
    with MemoryStore(memory_path) as memory:
        assert memory.sessions() == []
        assert memory.db.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 0
    assert evidence.summary()['failures'] == []


def test_fallback_resets_partial_and_saves_only_completed_answer(tmp_path):
    evidence = ModelEvidence(tmp_path / 'evidence.db')
    first = [{'message': {'content': 'Truncated answer'}, 'done': True, 'done_reason': 'length'}]
    profiles = [ModelProfile(name, 1, 1024, 'light', 1) for name in ('first:tiny', 'second:tiny')]
    stages = []
    with streaming_server({'first:tiny': first, 'second:tiny': 'good'}) as (url, _, _, _):
        result = generate({'prompt': 'Hello'}, tmp_path / 'memory.sqlite3', profiles, 'general', evidence,
                          base_url=url, on_stage=lambda text, **kwargs: stages.append((text, kwargs)))
    assert result['reply'] == 'Helpful answer.'
    assert result['details']['routing']['attempts'] == 2
    assert len(stages) == 2 and all(row[1] == {'reset': True} for row in stages)
    with MemoryStore(tmp_path / 'memory.sqlite3') as memory:
        assert [row['content'] for row in memory.history(result['session'])] == ['Hello', 'Helpful answer.']
    assert evidence.order(['first:tiny', 'second:tiny'], 'general') == ['second:tiny']


def test_rank_uses_recorded_role_quality_then_timing_and_failure_backoff(tmp_path):
    evidence = ModelEvidence(tmp_path / 'evidence.db')
    for index in range(2):
        for name, passed, seconds in [('qwen3:0.6b', True, 1), ('llama3.2:1b', True, 4), ('hermes3:3b', False, .5)]:
            evidence.record_benchmark(name, 'general', str(index), passed=passed, seconds=seconds)
    names = ['hermes3:3b', 'unmeasured', 'llama3.2:1b', 'qwen3:0.6b']
    assert evidence.order(names, 'general') == ['qwen3:0.6b', 'llama3.2:1b', 'unmeasured', 'hermes3:3b']
    evidence.record_failure('qwen3:0.6b', 'Temporarily unavailable')
    role, profiles = candidates('Hello', set(names), HardwareProfile(16, 4, 'Windows', 12), evidence)
    assert role == 'general' and profiles[0].model == 'llama3.2:1b'
    evidence.record_success('qwen3:0.6b')
    assert evidence.order(names, 'general')[0] == 'qwen3:0.6b'


def test_context_budget_rejects_prompt_without_truncating_it():
    prompt = 'This must not be silently shortened. ' * 100
    with pytest.raises(ValueError, match='not been truncated'):
        messages_for(prompt, [], 1024)
    history = [{'role': 'user', 'content': 'Old question'}, {'role': 'assistant', 'content': 'Old answer'}]
    messages = messages_for('Current question', history, 2048, 'Python')
    assert messages[-1]['content'] == 'Current question' and messages[1:3] == history
    assert 'Python' in messages[0]['content']


def test_model_evidence_connections_are_closed_after_each_operation(tmp_path, monkeypatch):
    from prometheus_assistant import model_quality
    original = sqlite3.connect
    connections = []
    def tracked(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr(model_quality.sqlite3, 'connect', tracked)
    evidence = ModelEvidence(tmp_path / 'evidence.db')
    evidence.record_benchmark('tiny', 'general', 'sample', passed=True, seconds=1)
    evidence.record_failure('tiny', 'sample failure')
    evidence.summary()
    evidence.record_success('tiny')
    assert len(connections) == 5
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')


@pytest.fixture
def workspace_server(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_workspace, 'detect_hardware', lambda: HardwareProfile(8, 4, 'Windows', .5))
    monkeypatch.setattr(ui_workspace, 'resource_root', lambda: None)
    app = ui_server.Interface(tmp_path / 'data' / 'memory.sqlite3')
    server = ui_server.Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    def request(method, path, body=None, token=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            headers = {'Origin': server.origin, 'X-Prometheus-Token': app.token if token is None else token}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            client.request(method, path, None if body is None else json.dumps(body), headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()
    def job(body):
        status, value = request('POST', '/api/jobs', body)
        assert status == 202, value
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            status, result = request('GET', '/api/job/'+value['id'])
            assert status == 200
            if result['state'] != 'running':
                return result
            time.sleep(.01)
        pytest.fail('Bounded workspace action did not complete')
    try:
        yield app, request, job
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(2)


def test_document_and_edit_actions_require_current_session_token(workspace_server, tmp_path):
    app, request, job = workspace_server
    content = base64.b64encode(b'Zebrafish science reference.').decode()
    body = {'mode': 'document_import', 'name': 'reference.txt', 'content': content}
    assert request('POST', '/api/jobs', body, token='foreign-session')[0] == 403
    assert job({'mode': 'document_list'})['result']['details']['documents'] == []
    assert job(body)['state'] == 'done'
    found = job({'mode': 'documents', 'prompt': 'zebrafish'})
    assert found['state'] == 'done' and 'Zebrafish' in found['result']['reply']
    project = tmp_path / 'project'
    project.mkdir()
    file = project / 'answer.py'
    file.write_text('answer = 1\n', encoding='utf-8')
    read = job({'mode': 'code_read', 'project': str(project), 'file': 'answer.py'})
    assert read['state'] == 'done', read
    original = read['result']['details']['sha256']
    edit = {'mode': 'code_apply', 'project': str(project), 'file': 'answer.py',
            'content': 'answer = 2\n', 'expected_sha256': original}
    assert request('POST', '/api/jobs', edit, token='foreign-session')[0] == 403
    assert file.read_text(encoding='utf-8') == 'answer = 1\n'
    preview = job({**edit, 'mode': 'code_preview'})
    assert preview['state'] == 'done' and file.read_text(encoding='utf-8') == 'answer = 1\n'
    applied = job(edit)
    assert applied['state'] == 'done' and file.read_text(encoding='utf-8') == 'answer = 2\n'
    stale = job({**edit, 'content': 'answer = 3\n'})
    assert stale['state'] == 'error' and file.read_text(encoding='utf-8') == 'answer = 2\n'
    undone = job({'mode': 'code_undo', 'project': str(project), 'edit_id': applied['result']['details']['edit_id']})
    assert undone['state'] == 'done' and file.read_text(encoding='utf-8') == 'answer = 1\n'


def test_watched_source_api_and_idle_scheduler_use_only_selected_query(workspace_server, monkeypatch):
    app, _, job = workspace_server
    from prometheus_assistant import maintenance
    calls = []
    def public_search(query, *, provider, online, limit):
        calls.append((query, provider, online, limit))
        return {'query': query, 'provider': provider, 'retrieved_utc': '2026-10-09T00:00:00+00:00',
                'response_sha256': 'a'*64, 'results': [{'title': 'Moon', 'url': 'https://images.nasa.gov/details/moon'}]}
    monkeypatch.setattr(maintenance, 'public_search', public_search)
    assert job({'mode': 'watch_source', 'provider': 'nasa', 'prompt': 'Moon', 'enabled': True})['state'] == 'done'
    assert calls == []
    result = job({'mode': 'check_sources'})
    assert result['state'] == 'done' and calls == [('Moon', 'nasa', True, 5)]
    assert job({'mode': 'check_sources'})['result']['details']['checks'] == []
    schedule = ui_workspace.schedule(app)
    assert schedule.status()['watches'][0]['last_snapshot']
    schedule.watch('nasa', 'Mars', enabled=True, now=0)
    submitted = []
    monkeypatch.setattr(app, 'submit', lambda body: submitted.append(body))
    app.current = 'a-running-foreground-job'
    app.next_maintenance = 0
    app.idle_maintenance()
    assert submitted == []
    app.current = None
    app.next_maintenance = 0
    app.idle_maintenance()
    assert submitted == [{'mode': 'check_sources', 'prompt': ''}]
