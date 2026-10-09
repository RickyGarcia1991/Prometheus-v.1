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
    chosen=select_model({"gemma4:26b"},HardwareProfile(32.0,16,"Windows",28.0))
    assert chosen.model=="gemma4:26b"
    assert chosen.tier=="heavy"


def test_unknown_inventory_returns_none():
    assert select_model({"other:model"},HardwareProfile(64.0,32,"Windows")) is None


def test_agent_registry_only_reports_present_commands(monkeypatch):
    # This case checks PATH discovery in isolation from the attached SSD.
    monkeypatch.setattr("prometheus_assistant.hardware.resource_root",lambda: None)
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


def test_unknown_available_memory_does_not_load_large_model():
    assert select_model({"gemma4:26b"}, HardwareProfile(128, 64, "Windows")) is None


def test_busy_large_computer_falls_back_to_small_installed_model():
    chosen = select_model({"gemma4:26b", "llama3.2:1b"}, HardwareProfile(64, 32, "Windows", 5))
    assert chosen.model == "llama3.2:1b"
    assert chosen.cpu_threads == 16


def test_memory_pressure_does_not_start_any_model():
    assert select_model({"llama3.2:1b"}, HardwareProfile(8, 8, "Windows", .5)) is None


def test_resident_weights_can_be_reused_without_double_counting():
    chosen = select_model({"gemma4:26b"}, HardwareProfile(32, 16, "Windows", 7), 17)
    assert chosen.model == "gemma4:26b"


def test_context_and_threads_scale_but_remain_bounded():
    models = {"llama3.2:1b-instruct-q4_K_M", "gemma4:26b"}
    cases = [(8, 6, 3072, "balanced"), (16, 12, 4096, "balanced"),
             (32, 28, 4096, "heavy"), (64, 55, 8192, "heavy"), (256, 240, 8192, "heavy")]
    for ram, available, context, tier in cases:
        profile = select_model(models, HardwareProfile(ram, 64, "Windows", available))
        assert (profile.context, profile.tier) == (context, tier)
        assert profile.cpu_threads == 16


def test_two_cpu_threads_keep_one_available():
    assert select_model({"llama3.2:1b"}, HardwareProfile(8, 2, "Windows", 5)).cpu_threads == 1
