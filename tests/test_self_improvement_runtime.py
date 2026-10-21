import json
import threading
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from prometheus_assistant import self_improvement_runtime as runtime


def test_context_counts_schema_and_unicode_without_shortening():
    messages = [{'role': 'user', 'content': 'é' * 300}]
    schema = {'enum': ['source-name-' * 100]}
    before = json.dumps([messages, schema])
    with pytest.raises(ValueError, match='No source was truncated'):
        runtime._check_context(messages, schema, 2048, 1024)
    assert json.dumps([messages, schema]) == before
    runtime._check_context(messages, schema, 4096, 1024)


def test_missing_host_setup_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    with pytest.raises(ValueError, match='not configured on this computer'):
        runtime.get_self_improvement(SimpleNamespace())


def test_verification_rejects_valid_but_no_longer_selected_release(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    config = tmp_path / 'Prometheus/self-improvement-host.json'
    config.parent.mkdir()
    config.write_text('{}')
    active = tmp_path / 'active'
    active.mkdir()
    (active / 'SHA256-MANIFEST.json').write_text('[]')
    events = []
    class Host:
        def __init__(self, path):
            assert path == config
        def capture_selection(self, root, digest):
            events.append('capture')
            return 'selected'
        def verify(self, root, digest):
            events.append('signature')
        def assert_selection(self, selected):
            assert selected == 'selected'
            raise ValueError('Active release changed')
    monkeypatch.setattr(runtime, 'WindowsPromotionHost', Host)
    def engine(*args, **kwargs):
        return SimpleNamespace(verify=kwargs['verifier'])
    monkeypatch.setattr(runtime, 'SelfImprovementEngine', engine)
    app = SimpleNamespace(memory_path=tmp_path / 'memory.sqlite3')
    result = runtime.get_self_improvement(app)
    assert runtime.get_self_improvement(app) is result
    with pytest.raises(ValueError, match='Active release changed'):
        result.verify(active)
    assert events == ['capture']


def test_generator_rejects_too_small_models_before_inference(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass
        def local_models(self):
            return [{'name': 'tiny'}, {'name': 'remote', 'remote_host': 'example'}]
        def resident_memory_gib(self):
            return 0
    seen = []
    def profiles(prompt, names, *args, **kwargs):
        seen.append(names)
        return 'coding', [SimpleNamespace(context=1024)]
    monkeypatch.setattr(runtime, 'OllamaClient', Client)
    monkeypatch.setattr(runtime, 'candidates', profiles)
    app = SimpleNamespace(cancel_event=threading.Event(), model_evidence=None)
    with pytest.raises(ValueError, match='No installed local helper fits'):
        runtime._generator(app)([{'role': 'user', 'content': 'small change'}], {})
    assert seen == [{'tiny'}]


def test_cancelled_generation_never_contacts_runtime():
    stop = threading.Event()
    stop.set()
    with pytest.raises(runtime.GenerationCancelled):
        runtime._generator(SimpleNamespace(cancel_event=stop))([], {})


@pytest.mark.parametrize('missing', ['state', 'payloads_unchanged', 'isolation_clean'])
def test_test_counts_do_not_override_missing_integrity_proof(tmp_path, missing):
    report = {'state': 'passed', 'payloads_unchanged': True, 'isolation_clean': True,
        'checks': [{'id': name, 'executed': True, 'exit_code': 0, 'tests_run': 5}
                   for name in runtime.REQUIRED_CHECKS]}
    report.pop(missing)
    runner = tmp_path / 'fixed_runner.py'
    runner.write_text('import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index("--report")+1]).write_text('
                      + repr(json.dumps(report)) + ',encoding="utf-8")\n', encoding='utf-8')
    host = SimpleNamespace(jobs=tmp_path / 'jobs', tool=lambda name: sys.executable if name == 'python' else runner)
    app = SimpleNamespace(progress=lambda message: None)
    with pytest.raises(ValueError, match='unchanged payloads and clean isolation'):
        runtime._run_checks(host, app, tmp_path / 'candidate', tmp_path / 'baseline', runtime.REQUIRED_CHECKS)
