import json

import pytest

from prometheus_assistant import smart_home, ui_server
from prometheus_assistant.memory import MemoryStore


def test_home_route_uses_explicit_handler_without_saving_control_key(tmp_path, monkeypatch):
    calls = []
    control_key = 'fixture-control-key-123456789'
    def handle(prompt, root, *, control_key):
        calls.append((prompt, root, control_key))
        return {'reply': 'Front porch lights: off', 'provider': 'Home Assistant'}
    monkeypatch.setattr(smart_home, 'handle_command', handle)
    monkeypatch.setattr(ui_server, 'installed_models', lambda: pytest.fail('Home mode must not select a model'))
    app = ui_server.Interface(tmp_path/'memory.sqlite3')
    try:
        request = ui_server.validate_job({'mode': 'home', 'prompt': 'status front porch lights', 'home_key': control_key})
        result = app.execute(request)
        assert calls == [('status front porch lights', tmp_path/'SmartHome', control_key)]
        assert result['label'] == 'Smart home · configured devices'
        assert control_key not in json.dumps(result)
        with MemoryStore(app.memory_path) as memory:
            saved = memory.history(result['session'])
        assert [row['content'] for row in saved] == ['status front porch lights', 'Front porch lights: off']
        assert control_key not in json.dumps(saved)
    finally:
        app.close()


@pytest.mark.parametrize('mode,key', [('chat', 'secret'), ('home', 1), ('home', None), ('home', 'x'*129), ('home', 'x\x00y')])
def test_home_key_contract_rejects_invalid_values_without_echo(mode, key):
    with pytest.raises(ValueError, match='valid control key') as error:
        ui_server.validate_job({'mode': mode, 'prompt': 'hello', 'home_key': key})
    if isinstance(key, str):
        assert key not in str(error.value)


def test_home_standby_requires_no_key_or_model(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, 'installed_models', lambda: pytest.fail('Standby must not select a model'))
    app = ui_server.Interface(tmp_path/'memory.sqlite3')
    try:
        result = app.execute(ui_server.validate_job({'mode': 'home', 'prompt': 'turn off bedroom lights'}))
        assert result['details']['standby'] is True
        assert 'standby' in result['reply']
    finally:
        app.close()
