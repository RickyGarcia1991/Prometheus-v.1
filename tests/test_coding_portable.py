import subprocess
from pathlib import Path
import pytest
from prometheus_assistant import coding, hardware, integrations
from prometheus_assistant.hardware import HardwareProfile, select_model


def test_portable_tools_take_precedence_after_relocation(monkeypatch,tmp_path):
    root=tmp_path/'Moved Drive'/'Prometheus-Resources'
    for rel in hardware.PORTABLE_AGENTS.values():
        p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
    monkeypatch.setenv('PROMETHEUS_RESOURCE_ROOT',str(root))
    monkeypatch.setattr(hardware.shutil,'which',lambda name:'C:/host/'+name+'.exe')
    assert hardware.coding_agents()['hermes']==str(root/hardware.PORTABLE_AGENTS['hermes'])
    assert hardware.coding_agents()['codex']==str(root/hardware.PORTABLE_AGENTS['codex'])


def test_coding_handoff_preserves_literal_prompt_and_uses_foreground_session(monkeypatch,tmp_path):
    root=tmp_path/'resources';exe=root/hardware.PORTABLE_AGENTS['codex']
    exe.parent.mkdir(parents=True);exe.write_bytes(b'fixture')
    project=tmp_path/'project with spaces';project.mkdir()
    monkeypatch.setenv('PROMETHEUS_RESOURCE_ROOT',str(root))
    commands=[]
    monkeypatch.setattr(coding.subprocess,'call',lambda args,**kwargs:commands.append((args,kwargs)) or 0)
    prompt='Explain $() & this `literal` text'
    assert coding.launch_coding('codex',project,prompt=prompt)==0
    command,options=commands[0]
    assert command[-1]==prompt and '--no-daemon' in command
    assert command[command.index('--sandbox')+1]=='workspace-write'
    assert 'shell' not in options and options['cwd']==project.resolve()
    assert options['env']['CODEX_HOME']==str(root.parent/'Prometheus-Data/Codex')


def test_cannot_default_coding_to_runtime_directory(monkeypatch,tmp_path):
    exe=tmp_path/hardware.PORTABLE_AGENTS['codex'];exe.parent.mkdir(parents=True);exe.write_bytes(b'x')
    monkeypatch.setenv('PROMETHEUS_RESOURCE_ROOT',str(tmp_path))
    with pytest.raises(ValueError,match='project folder'):
        coding.launch_coding('codex',tmp_path)


def test_new_models_follow_role_and_available_memory():
    installed={'gpt-oss:20b','hermes3:3b','qwen2.5-coder:1.5b','llama3.2:1b'}
    assert select_model(installed,HardwareProfile(8,8,'Windows',5),task='coding').model=='qwen2.5-coder:1.5b'
    assert select_model(installed,HardwareProfile(8,8,'Windows',5)).model=='hermes3:3b'
    assert select_model(installed,HardwareProfile(32,16,'Windows',25)).model=='gpt-oss:20b'
    assert select_model(installed,HardwareProfile(64,32,'Windows',3)) is None
    assert select_model(installed,HardwareProfile(64,32,'Windows',5)).model=='llama3.2:1b'


def test_optional_integration_timeout_does_not_break_diagnostics(monkeypatch):
    def unavailable(*args,**kwargs):raise subprocess.TimeoutExpired('ollama',10)
    monkeypatch.setattr(integrations.subprocess,'run',unavailable)
    assert integrations.launch_integrations('ollama.exe')==set()
