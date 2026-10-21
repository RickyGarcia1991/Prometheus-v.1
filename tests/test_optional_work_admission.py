"""Real admission at optional operation boundaries with synthetic local data."""
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import project_resources, ui_workspace


@pytest.fixture
def app(tmp_path, monkeypatch):
    resources = project_resources.ProjectResources(tmp_path, probe=SimpleNamespace(
        snapshot=lambda **kwargs: dict(total_ram_gib=16, available_ram_gib=8,
            free_disk_gib=20, cpu_percent=10, plugged_in=True, battery_percent=80,
            low_memory=False, thermal_distress=False, observed_at='fixture')))
    resources.set_mode_preference('Balanced')
    monkeypatch.setattr(ui_workspace, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=8))
    return SimpleNamespace(lock=threading.RLock(), data_root=tmp_path,
                           project_resources=resources)


@pytest.mark.parametrize('mode,allowed', [(None, True), ('Idle', True), ('Balanced', True),
    ('Performance', True), ('Battery Saver', False), ('Critical Recovery', False)])
def test_background_network_modes(app, mode, allowed):
    app.project_resources.set_mode_preference(mode)
    assert project_resources.background_allowed(app)['allowed'] is allowed


def test_unknown_background_mode_fails_closed(app, monkeypatch):
    monkeypatch.setattr(app.project_resources, 'specialist_snapshot',
        lambda **kwargs: {'mode': {'requested': None, 'effective': 'unknown'}})
    assert project_resources.background_allowed(app)['allowed'] is False


@pytest.mark.parametrize('requested', ['Idle', 'Battery Saver', 'Balanced', 'Performance'])
def test_critical_pressure_overrides_owner_preference(app, requested):
    app.project_resources.set_mode_preference(requested)
    app.project_resources.probe = SimpleNamespace(snapshot=lambda **_: dict(
        total_ram_gib=16, available_ram_gib=.1, free_disk_gib=20, cpu_percent=10,
        plugged_in=True, battery_percent=80, low_memory=True, thermal_distress=False))
    assert project_resources.inference_mode(app) == 'Critical Recovery'


@pytest.mark.parametrize('mode', ['Idle', 'Battery Saver', 'Critical Recovery'])
def test_restrictive_embedding_mode_keeps_keyword_results_without_model(app, monkeypatch, mode):
    library = ui_workspace.library(app, semantic=False)
    library.import_bytes('manual.txt', b'Automobile service instructions')
    class Forbidden:
        def identity(self):
            pytest.fail('Deferred embedding must not query model identity')
        def embed(self, texts):
            pytest.fail('Deferred embedding must not invoke a model')
    monkeypatch.setattr('prometheus_assistant.document_library.OllamaEmbeddingClient', Forbidden)
    app.project_resources.set_mode_preference(mode)
    library = ui_workspace.library(app)
    assert library.index_missing_embeddings()['status'] == 'resource_deferred'
    found = library.search('automobile')
    assert found['semantic_status'] == 'resource_deferred'
    assert found['retrieval'] == 'keyword' and found['results']


def test_preference_change_between_embedding_batches_preserves_remaining_work(app, monkeypatch):
    library = ui_workspace.library(app, semantic=False)
    for index in range(3):
        library.import_bytes(f'note{index}.txt', f'Automobile service step {index}'.encode())
    calls = []
    class Embeddings:
        def identity(self):
            return 'fixture:qualified'
        def embed(self, texts):
            calls.append(texts)
            app.project_resources.set_mode_preference('Idle')
            return [[1., 0.] for text in texts]
    monkeypatch.setattr('prometheus_assistant.document_library.OllamaEmbeddingClient', Embeddings)
    result = ui_workspace.library(app).index_missing_embeddings()
    assert result['status'] == 'resource_deferred'
    assert result['indexed'] == 2 and result['pending'] == 1 and len(calls) == 1


def test_lifecycle_mode_obeys_restrictive_owner_intent_during_dwell(app):
    app.project_resources.set_mode_preference('Idle')
    assert project_resources.inference_mode(app) == 'Idle'


def test_background_snapshot_does_not_claim_foreground_work(app, monkeypatch):
    calls = []
    original = app.project_resources.scheduler.mode
    def mode(*args, **kwargs):
        calls.append(kwargs['foreground'])
        return original(*args, **kwargs)
    monkeypatch.setattr(app.project_resources.scheduler, 'mode', mode)
    app.project_resources.specialist_snapshot()
    project_resources.background_allowed(app)
    assert calls == [True, False]
