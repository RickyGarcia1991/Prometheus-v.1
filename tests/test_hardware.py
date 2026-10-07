from prometheus_assistant.hardware import HardwareProfile, coding_agents, select_model


def test_low_memory_prefers_small_model():
    installed={"gemma4:26b","llama3.2:1b-instruct-q4_K_M","llama3.2:1b"}
    chosen=select_model(installed,HardwareProfile(8.0,8,"Windows"))
    assert chosen.model=="llama3.2:1b-instruct-q4_K_M"
    assert chosen.context==3072


def test_16_gib_host_does_not_overcommit_26b_model():
    installed={"gemma4:26b","llama3.2:1b"}
    chosen=select_model(installed,HardwareProfile(16.0,8,"Windows"))
    assert chosen.model=="llama3.2:1b"


def test_heavy_model_requires_heavy_memory():
    chosen=select_model({"gemma4:26b"},HardwareProfile(32.0,16,"Windows"))
    assert chosen.model=="gemma4:26b"
    assert chosen.tier=="heavy"


def test_unknown_inventory_returns_none():
    assert select_model({"other:model"},HardwareProfile(64.0,32,"Windows")) is None


def test_agent_registry_only_reports_present_commands(monkeypatch):
    monkeypatch.setattr("shutil.which",lambda name: "C:/tools/"+name+".exe" if name=="opencode" else None)
    assert coding_agents()=={"opencode":"C:/tools/opencode.exe"}


def test_resource_root_requires_existing_directory(monkeypatch,tmp_path):
    from prometheus_assistant.hardware import resource_root
    monkeypatch.setenv("PROMETHEUS_RESOURCE_ROOT",str(tmp_path))
    assert resource_root()==str(tmp_path.resolve())
    monkeypatch.setenv("PROMETHEUS_RESOURCE_ROOT",str(tmp_path/"missing"))
    assert resource_root() is None


def test_16_gib_prefers_balanced_when_available():
    installed={"gemma4:26b","llama3.2:1b-instruct-q4_K_M","llama3.2:1b"}
    chosen=select_model(installed,HardwareProfile(16.0,8,"Windows"))
    assert chosen.model=="llama3.2:1b-instruct-q4_K_M"
    assert chosen.tier=="balanced"
