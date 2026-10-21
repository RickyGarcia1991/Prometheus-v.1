"""Exercise lifecycle scope in the real Interface worker, with fake transport."""
from contextlib import nullcontext
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import ui_server, model_lifecycle
from prometheus_assistant.ollama import OllamaClient


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, 'reference_session', nullcontext)
    app = ui_server.Interface(tmp_path / 'memory.sqlite3')
    app.project_resources.probe = SimpleNamespace(snapshot=lambda **kwargs: dict(
        total_ram_gib=16, available_ram_gib=8, free_disk_gib=20, cpu_percent=10,
        battery_percent=80, plugged_in=True, low_memory=False, thermal_distress=False,
        observed_at='fixture'))
    app.project_resources.set_mode_preference('Balanced')
    calls, bodies = [], []
    state = SimpleNamespace(app=app, calls=calls, bodies=bodies, transport_hook=None)
    def transport(self, path, payload=None):
        bodies.append(payload)
        if state.transport_hook:
            state.transport_hook()
        return {'message': {'content': 'complete'}}
    monkeypatch.setattr(OllamaClient, '_request_owned', transport)
    def execute(request):
        calls.append(request['mode'])
        # No explicit cancel_event/provider: the worker's scope must supply both.
        result = OllamaClient(model='fixture')._request('/api/chat',
            {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'fixture'}]})
        return {'reply': result['message']['content'], 'details': {}}
    app.execute = execute
    yield state
    app.close()


def submit(rig, *, background=False, mode=None):
    mode = mode or ('check_updates' if background else 'chat')
    job = rig.app.submit({'mode': mode, 'prompt': 'fixture'}, background=background)
    rig.app.executor.submit(lambda: None).result(timeout=10)
    return rig.app.jobs[job['id']]


@pytest.mark.parametrize('mode', ['Idle', 'Battery Saver'])
def test_foreground_worker_remains_usable_in_restrictive_optional_modes(rig, mode):
    rig.app.project_resources.set_mode_preference(mode)
    result = submit(rig)
    assert result['state'] == 'done', result
    assert rig.calls == ['chat'] and len(rig.bodies) == 1
    assert rig.bodies[0]['keep_alive'] == 0


def test_foreground_worker_refuses_inference_in_critical_recovery(rig):
    rig.app.project_resources.set_mode_preference('Critical Recovery')
    result = submit(rig)
    assert result['state'] == 'error' and 'mode blocks' in result['error']
    assert not rig.bodies


def test_background_worker_carries_optional_scope_to_model_adapter(rig):
    rig.app.project_resources.set_mode_preference('Idle')
    result = submit(rig, background=True)
    # Idle allows network maintenance but forbids incidental optional inference.
    assert rig.calls == ['check_updates']
    assert result['state'] == 'error' and 'mode blocks' in result['error']
    assert not rig.bodies


def test_background_worker_permits_balanced_optional_inference_without_residency(rig):
    result = submit(rig, background=True)
    assert result['state'] == 'done', result
    assert rig.bodies[0]['keep_alive'] == 0


def test_optional_scope_does_not_leak_to_next_foreground_job_on_same_worker(rig):
    rig.app.project_resources.set_mode_preference('Idle')
    assert submit(rig, background=True)['state'] == 'error'
    result = submit(rig)
    assert result['state'] == 'done', result
    assert rig.calls == ['check_updates', 'chat'] and len(rig.bodies) == 1


@pytest.mark.parametrize('mode', ['check_sources', 'check_updates', 'idle_run'])
def test_preference_change_after_submission_defers_background_before_execution(rig, mode):
    entered, release = threading.Event(), threading.Event()
    def queued_work():
        entered.set()
        assert release.wait(10)
    rig.app.executor.submit(queued_work)
    assert entered.wait(10)
    try:
        job = rig.app.submit({'mode': mode, 'prompt': ''}, background=True)
        rig.app.project_resources.set_mode_preference('Battery Saver')
    finally:
        release.set()
    rig.app.executor.submit(lambda: None).result(timeout=10)
    result = rig.app.jobs[job['id']]
    assert result['state'] == 'cancelled' and 'deferred' in result['error']
    assert not rig.calls and not rig.bodies
    assert rig.app.current is None


def test_worker_scope_cancellation_discards_blocking_result_and_releases_slot(rig):
    rig.transport_hook = rig.app.cancel_event.set
    result = submit(rig)
    assert result['state'] == 'cancelled'
    assert rig.app.current is None
    # The UI worker's cancelled context must not leak to this calling thread.
    with model_lifecycle.governor.lease('fixture', 'fixture', 2048):
        pass


def test_mode_change_during_blocking_response_discards_result_without_cooldown(rig):
    rig.transport_hook = lambda: rig.app.project_resources.set_mode_preference('Critical Recovery')
    result = submit(rig)
    assert result['state'] == 'error' and 'mode blocks' in result['error']
    assert rig.app.current is None
    assert not model_lifecycle.governor._failures
    with model_lifecycle.governor.lease('http://127.0.0.1:11434', 'fixture', 2048):
        pass
