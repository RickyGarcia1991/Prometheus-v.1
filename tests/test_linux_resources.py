import json
from pathlib import Path
import pytest
from prometheus_assistant import hardware, linux_resources, ui_server, native_health

def test_linux_never_advertises_portable_exe(tmp_path, monkeypatch):
    exe = tmp_path / hardware.PORTABLE_AGENTS['codex']
    exe.parent.mkdir(parents=True); exe.write_bytes(b'Windows only')
    monkeypatch.setenv('PROMETHEUS_RESOURCE_ROOT', str(tmp_path))
    monkeypatch.setattr(hardware.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(hardware.shutil, 'which', lambda name: None)
    assert hardware.coding_agents() == {}

def test_windows_portable_agent_remains_supported(tmp_path, monkeypatch):
    exe = tmp_path / hardware.PORTABLE_AGENTS['codex']
    exe.parent.mkdir(parents=True); exe.write_bytes(b'Windows fixture')
    monkeypatch.setenv('PROMETHEUS_RESOURCE_ROOT', str(tmp_path))
    monkeypatch.setattr(hardware.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(hardware.shutil, 'which', lambda name: None)
    assert hardware.coding_agents()['codex'] == str(exe)

def test_linux_model_inventory_uses_running_server_with_shared_hdd(tmp_path, monkeypatch):
    manifest = tmp_path/'Ollama/.ollama/models/manifests/registry.ollama.ai/library/huge/70b'
    manifest.parent.mkdir(parents=True);manifest.write_text('{}')
    monkeypatch.setenv('PROMETHEUS_RESOURCE_ROOT', str(tmp_path))
    monkeypatch.setattr(ui_server.sys, 'platform', 'linux')
    monkeypatch.setattr(ui_server.OllamaClient, 'local_models', lambda self: [
        {'name':'small:1b'}, {'name':'remote:1b','remote_host':'external'},
        {'name':'cloud-model'}, {'name':''}])
    assert ui_server.installed_models() == {'small:1b'}

def test_writable_executable_rejected(tmp_path):
    exe = tmp_path/'node';exe.write_bytes(b'fake');exe.chmod(0o777)
    with pytest.raises(ValueError):
        linux_resources.system_executable(exe)

def test_changed_native_reader_rejected(tmp_path, monkeypatch):
    file = tmp_path/'reader.so';file.write_bytes(b'changed')
    monkeypatch.setattr(linux_resources, '_ZIM_PINS', {'reader.so':'0'*64})
    with pytest.raises(ImportError, match='pins'):
        linux_resources.zim_files(tmp_path)

def test_linked_native_reader_rejected(tmp_path, monkeypatch):
    real = tmp_path/'actual';real.write_bytes(b'fixture')
    (tmp_path/'reader.so').symlink_to(real)
    monkeypatch.setattr(linux_resources, '_ZIM_PINS', {'reader.so':'0'*64})
    with pytest.raises(ImportError, match='Linked'):
        linux_resources.zim_files(tmp_path)

def test_unconfigured_library_is_blocked():
    result = native_health.resources_check(None)
    assert result['status'] == 'blocked'

def test_native_health_never_uses_windows_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(linux_resources, 'system_executable', lambda value: Path(value))
    from prometheus_assistant import ollama, integrations
    monkeypatch.setattr(integrations,'ollama_executable',lambda:'/usr/bin/ollama')
    monkeypatch.setattr(ollama.OllamaClient,'local_models',lambda self:[{'name':'small:1b'}])
    (tmp_path/'pyproject.toml').write_text('[project]\nversion="test"\n')
    rows=native_health.checks(tmp_path,None,'test',tmp_path/'missing.sqlite3')
    assert rows[0]['status']=='untested'
    assert rows[-1]['models']==['small:1b']
    assert 'inference' in rows[-2]['scope'].lower()


def test_native_metadata_must_match_running_version(tmp_path, monkeypatch):
    monkeypatch.setattr(linux_resources, 'system_executable', lambda value: Path(value))
    from prometheus_assistant import ollama, integrations
    monkeypatch.setattr(integrations, 'ollama_executable', lambda: '/usr/bin/ollama')
    monkeypatch.setattr(ollama.OllamaClient, 'local_models', lambda self: [])
    (tmp_path/'pyproject.toml').write_text('[project]\nversion="current"\n')
    checkpoint = {'platform':'Linux', 'version':'old', 'source_commit':'a'*40}
    path = tmp_path/'NATIVE-CHECKPOINT.json'
    path.write_text(json.dumps(checkpoint))
    assert native_health.checks(tmp_path, None, 'current', tmp_path/'none')[0]['status'] == 'untested'
    checkpoint['version'] = 'current'; path.write_text(json.dumps(checkpoint))
    assert native_health.checks(tmp_path, None, 'current', tmp_path/'none')[0]['status'] == 'pass'
