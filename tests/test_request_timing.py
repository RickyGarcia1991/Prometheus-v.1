"""Deterministic request diagnostics; no model service, secrets or live data."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import compact_chat, request_timing as timing, ui_server as ui
from prometheus_assistant.hardware import ModelProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.ollama import GenerationCancelled


class Clock:
    value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def bare_app(monkeypatch, clock):
    # Real worker dispatch/finalization, without runtime/remote/executor startup.
    app = object.__new__(ui.Interface)
    app.lock = threading.Lock()
    app.jobs = {}
    app.current = 'fixture'
    app.cache_context = object()
    app.state = 'thinking'
    app.cancel_event = threading.Event()
    monkeypatch.setattr(ui, 'time', SimpleNamespace(monotonic=clock))
    return app


def test_fixed_labels_drop_arguments_results_and_exception_text():
    clock = Clock()
    secret = 'fixture-private-text-must-not-be-retained'
    def operation(value):
        clock.advance(2.5)
        raise ValueError(value)
    with timing.RequestTiming(clock=clock) as collector:
        with pytest.raises(ValueError, match=secret):
            timing.timed('retrieval', operation, secret)
        report = collector.snapshot('failed')
    assert report['stages'] == [{'stage': 'retrieval', 'start_seconds': 0,
                                  'seconds': 2.5, 'status': 'failed'}]
    assert secret not in json.dumps(report)
    with pytest.raises(ValueError, match='Unknown timing stage'):
        timing.timed(secret, lambda: None)


def test_collector_bounds_rows_and_preserves_exceptions_after_bound():
    clock = Clock()
    with timing.RequestTiming(clock=clock) as collector:
        for _ in range(timing.MAX_STAGES+3):
            with timing.stage('generation'):
                clock.advance(1)
        with pytest.raises(GenerationCancelled):
            with timing.stage('generation'):
                raise GenerationCancelled('Not retained')
        report = collector.snapshot('cancelled')
    assert len(report['stages']) == timing.MAX_STAGES
    assert report['dropped_stages'] == 4
    assert report['worker_total_seconds'] == timing.MAX_STAGES+3


def test_nested_request_context_restores_parent_and_clears_after_exit():
    clock = Clock()
    with timing.RequestTiming(clock=clock) as first:
        timing.timed('retrieval', lambda: clock.advance(1))
        with timing.RequestTiming(clock=clock) as second:
            timing.timed('generation', lambda: clock.advance(2))
        timing.timed('history_persistence', lambda: clock.advance(3))
    timing.timed('generation', lambda: clock.advance(4))
    assert [row['stage'] for row in first.rows] == ['retrieval', 'history_persistence']
    assert [row['stage'] for row in second.rows] == ['generation']


def test_parallel_contexts_keep_timings_separate():
    barrier = threading.Barrier(2)
    def work(label):
        clock = Clock()
        with timing.RequestTiming(clock=clock) as collector:
            with timing.stage(label):
                barrier.wait(timeout=3)
                clock.advance(1)
            return collector.snapshot('completed')
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(work, label) for label in ('retrieval', 'generation')]
        reports = [future.result(timeout=4) for future in futures]
    assert [[row['stage'] for row in report['stages']] for report in reports] == [['retrieval'], ['generation']]


@pytest.mark.parametrize('error,state,status', [
    (None, 'done', 'completed'),
    (ValueError('private fixture error'), 'error', 'failed'),
    (GenerationCancelled('private fixture cancellation'), 'cancelled', 'cancelled'),
])
def test_worker_returns_diagnostics_on_all_outcomes(monkeypatch, error, state, status):
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    def execute(request):
        with timing.stage('retrieval'):
            clock.advance(7)
            if error:
                raise error
        return {'reply': 'Public fixture reply'}
    app.execute = execute
    app._work('fixture', {'mode': 'ask', 'prompt': 'private fixture prompt'})
    row = app.jobs['fixture']
    assert row['state'] == state and row['seconds'] == 7
    assert row['diagnostics']['status'] == status
    assert row['diagnostics']['stages'][0]['status'] == status
    assert 'private fixture' not in json.dumps(row['diagnostics'])
    assert app.current is None


def test_reference_cache_failure_is_timed(monkeypatch):
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    app.cache_context = None
    def cache():
        clock.advance(2)
        raise OSError('private fixture path')
    monkeypatch.setattr(ui, 'reference_session', cache)
    app._work('fixture', {'mode': 'ask', 'prompt': 'Public fixture'})
    report = app.jobs['fixture']['diagnostics']
    assert report['stages'][0]['stage'] == 'reference_cache'
    assert report['stages'][0]['seconds'] == 2
    assert report['status'] == 'failed'


def test_completed_timing_reaches_result_details_without_mutating_evidence(monkeypatch):
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    saved_details = {'sources': [{'id': 'S1', 'text': 'Public fixture excerpt'}],
                     'metrics': {'total_seconds': 1}}
    original_payload = {'reply': 'Public fixture [S1].', 'details': saved_details}
    app.execute = lambda request: original_payload
    app._work('fixture', {'mode': 'ask', 'prompt': 'Public fixture'})
    row = app.jobs['fixture']
    visible_details = row['result']['details']
    assert visible_details['request_timing'] == row['diagnostics']
    assert visible_details['sources'] == saved_details['sources']
    assert visible_details['metrics'] == saved_details['metrics']
    assert 'request_timing' not in saved_details
    assert original_payload['details'] is saved_details
    assert row['result'] is not original_payload


def test_final_worker_time_includes_wait_to_publish_result(monkeypatch):
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    class DelayedLock:
        def __enter__(self):
            clock.advance(2)
        def __exit__(self, *unused):
            pass
    app.lock = DelayedLock()
    app.execute = lambda request: {'reply': 'Public fixture'}
    app._work('fixture', {'mode': 'chat', 'prompt': 'Public fixture'})
    assert app.jobs['fixture']['seconds'] == 2
    assert app.jobs['fixture']['diagnostics']['worker_total_seconds'] == 2


@pytest.mark.parametrize('save_fails', [False, True])
def test_final_persistence_is_in_job_timing_without_changing_old_metrics(tmp_path, monkeypatch, save_fails):
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    app.memory_path = tmp_path/'memory.sqlite3'
    monkeypatch.setattr(compact_chat, 'time', SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(compact_chat, 'load_profile', lambda memory: None)
    writes = []

    class Client:
        def __init__(self, base_url, model, timeout, num_ctx, num_thread):
            self.model, self.num_ctx, self.num_thread = model, num_ctx, num_thread
            self.last_metrics = {'seconds': 1.0}

        def ensure_local_model(self):
            pass

        def chat_stream(self, *args, **kwargs):
            clock.advance(1)
            return 'Public neutral answer.', 3

    class Evidence:
        def summary(self):
            clock.advance(3)
            return {'scope': 'Fixture'}

        def record_success(self, model):
            pass

    original = MemoryStore.save_exchange
    def delayed_save(self, *args, **kwargs):
        writes.append(kwargs['details'])
        clock.advance(120)
        if save_fails:
            raise OSError('private fixture path')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(compact_chat, 'OllamaClient', Client)
    monkeypatch.setattr(MemoryStore, 'save_exchange', delayed_save)
    profile = ModelProfile('qwen3:1.7b', 2, 4096, 'Fixture', 4)
    app.execute = lambda request: compact_chat.generate(request, app.memory_path, [profile], 'general', Evidence())
    app._work('fixture', {'mode': 'chat', 'prompt': 'Give a brief greeting.'})
    row = app.jobs['fixture']
    stages = {stage['stage']: stage for stage in row['diagnostics']['stages']}
    assert row['seconds'] == row['diagnostics']['worker_total_seconds'] == 124
    assert stages['generation']['seconds'] == 1
    assert stages['quality_summary']['seconds'] == 3
    assert stages['history_persistence']['seconds'] == 120
    assert stages['history_persistence']['status'] == ('failed' if save_fails else 'completed')
    assert row['state'] == ('error' if save_fails else 'done')
    assert len(writes) == 1 and writes[0]['metrics']['total_seconds'] == 1
    assert 'diagnostics' not in writes[0]
    if not save_fails:
        assert row['result']['details']['request_timing']['worker_total_seconds'] == 124
        assert 'request_timing' not in writes[0]
        with MemoryStore(app.memory_path) as memory:
            history = memory.history(row['result']['session'], include_metadata=True)
        assert len(history) == 2
        assert history[-1]['details']['metrics']['total_seconds'] == 1
        assert 'request_timing' not in history[-1]['details']


def test_grounded_dispatch_splits_retrieval_routing_and_runtime(monkeypatch):
    from prometheus_assistant import capabilities
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    app.memory_path = None
    app.model_evidence = object()
    app.progress = lambda *args, **kwargs: None
    app.append_partial = lambda text: None
    def advance(value):
        clock.advance(1)
        return value
    monkeypatch.setattr(capabilities, 'retrieve', lambda *args: advance({'source': 'documents', 'sources': [{}]}))
    monkeypatch.setattr(ui, 'OllamaClient', lambda **kwargs: SimpleNamespace(resident_memory_gib=lambda: advance(0)))
    monkeypatch.setattr(ui, 'installed_models', lambda: advance({'fixture'}))
    monkeypatch.setattr(ui, 'detect_hardware', lambda: advance('hardware fixture'))
    monkeypatch.setattr(compact_chat, 'candidates', lambda *args, **kwargs: advance(('general', [object()])))
    monkeypatch.setattr(compact_chat, 'generate', lambda *args, **kwargs: {'reply': 'Public fixture'})
    app.ensure_runtime = lambda: advance(None)
    app.execute = app.answer_with_evidence
    app._work('fixture', {'mode': 'ask', 'prompt': 'Public fixture'})
    row = app.jobs['fixture']
    assert row['state'] == 'done'
    assert [record['stage'] for record in row['diagnostics']['stages']] == [
        'retrieval', 'runtime_client_setup', 'model_inventory', 'hardware', 'resident_memory', 'eligibility',
        'runtime_readiness', 'model_inventory', 'hardware', 'resident_memory', 'eligibility']
    assert row['seconds'] == 10


def test_generation_cancellation_retains_timing_and_existing_cleanup(tmp_path, monkeypatch):
    clock = Clock()
    app = bare_app(monkeypatch, clock)
    monkeypatch.setattr(compact_chat, 'time', SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(compact_chat, 'load_profile', lambda memory: None)
    unloads = []
    class Client:
        def __init__(self, base_url, model, **kwargs):
            self.model = model
        def ensure_local_model(self):
            pass
        def chat_stream(self, *args, **kwargs):
            clock.advance(5)
            raise GenerationCancelled('private fixture cancellation')
        def _request(self, path, payload):
            unloads.append((path, payload))
            clock.advance(2)
    monkeypatch.setattr(compact_chat, 'OllamaClient', Client)
    profile = ModelProfile('qwen3:1.7b', 2, 4096, 'Fixture', 4)
    app.execute = lambda request: compact_chat.generate(request, tmp_path/'fixture.sqlite3', [profile],
                                                        'general', object())
    app._work('fixture', {'mode': 'chat', 'prompt': 'Public neutral greeting'})
    row = app.jobs['fixture']
    stages = {record['stage']: record for record in row['diagnostics']['stages']}
    assert row['state'] == 'cancelled' and row['seconds'] == 7
    assert stages['generation']['status'] == 'cancelled' and stages['generation']['seconds'] == 5
    assert stages['model_unload']['status'] == 'completed' and stages['model_unload']['seconds'] == 2
    assert 'history_persistence' not in stages
    assert len(unloads) == 1 and unloads[0][1]['keep_alive'] == 0
    assert 'private fixture' not in json.dumps(row['diagnostics'])


@pytest.mark.parametrize('unlocked,status', [(False, 401), (True, 200)])
def test_job_diagnostics_use_existing_owner_authentication(monkeypatch, unlocked, status):
    app = bare_app(monkeypatch, Clock())
    app.token = 'fixture-token'
    app.jobs['fixture'] = {'state': 'done', 'diagnostics': {'worker_total_seconds': 1}}
    handler = object.__new__(ui.Handler)
    handler.path = '/api/job/fixture'
    handler.headers = {'Host': 'localhost:54555', 'X-Prometheus-Token': app.token}
    handler.server = SimpleNamespace(app=app, origin='http://127.0.0.1:54555', owner_origin='http://localhost:54555',
        owner_access=SimpleNamespace(required=True, authenticated=lambda cookie: unlocked))
    replies = []
    handler.respond = lambda *args: replies.append(args)
    handler.do_GET()
    assert replies[0][0] == status
    assert ('diagnostics' in replies[0][1]) == unlocked
