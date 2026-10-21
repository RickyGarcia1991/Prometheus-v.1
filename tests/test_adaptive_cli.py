from prometheus_assistant import cli
from prometheus_assistant.hardware import HardwareProfile


def install_fixture(monkeypatch, hardware):
    clients = []
    class Model:
        def __init__(self, base_url, model, **options):
            self.model, self.base_url, self.options = model, base_url, options
            clients.append(self)
        def local_models(self):
            return [{'name': 'llama3.2:1b'}, {'name': 'llama3.2:1b-instruct-q4_K_M'}, {'name': 'gemma4:26b'}]
        def resident_memory_gib(self):
            return 0
        def ensure_local_model(self):
            return {'name': self.model}
    monkeypatch.setattr(cli, 'OllamaClient', Model)
    monkeypatch.setattr(cli, 'detect_hardware', lambda: hardware)
    return clients


def test_default_auto_applies_context_and_cpu_budget(monkeypatch, tmp_path):
    clients = install_fixture(monkeypatch, HardwareProfile(16, 8, 'Windows', 10))
    assert cli.main(['--memory', str(tmp_path/'m.db'), 'doctor', '--json']) == 0
    assert clients[-1].model == 'llama3.2:1b-instruct-q4_K_M'
    assert clients[-1].options == {'num_ctx': 4096, 'num_thread': 6}


def test_explicit_model_is_not_silently_changed(monkeypatch, tmp_path):
    clients = install_fixture(monkeypatch, HardwareProfile(32, 16, 'Windows', 28))
    assert cli.main(['--memory', str(tmp_path/'m.db'), '--model', 'llama3.2:1b', 'doctor']) == 0
    assert len(clients) == 1 and clients[-1].model == 'llama3.2:1b'


def test_pressure_blocks_loading_before_creating_memory(monkeypatch, tmp_path, capsys):
    clients = install_fixture(monkeypatch, HardwareProfile(8, 8, 'Windows', 1))
    db = tmp_path/'m.db'
    assert cli.main(['--memory', str(db), 'doctor']) == 1
    assert 'available RAM' in capsys.readouterr().err
    assert not db.exists() and len(clients) == 1
