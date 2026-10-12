from dataclasses import replace

import pytest

from prometheus_assistant.hardware import HardwareProfile, ModelProfile, grounded_profile


QWEN = ModelProfile("qwen3:1.7b", 6.0, 4096, "balanced", cpu_threads=6)


@pytest.mark.parametrize("available, expected", [
    (4.999, 4096), (5.0, 8192), (5.499, 8192), (5.5, 12288), (6.5, 12288),
])
def test_context_admission_keeps_two_gib_for_host(available, expected):
    result = grounded_profile(QWEN, HardwareProfile(16, 8, "Windows", available))
    assert result.context == expected
    assert replace(result, context=QWEN.context) == QWEN


@pytest.mark.parametrize("ram, expected", [(11.999, 4096), (12, 12288)])
def test_larger_grounded_context_requires_twelve_gib_host(ram, expected):
    assert grounded_profile(QWEN, HardwareProfile(ram, 8, "Windows", 6.5)).context == expected


def test_single_reusable_runtime_memory_is_credited_once():
    hardware = HardwareProfile(16, 8, "Windows", 2.0)
    assert grounded_profile(QWEN, hardware, 3.499).context == 8192
    assert grounded_profile(QWEN, hardware, 3.5).context == 12288
    assert grounded_profile(QWEN, hardware) is QWEN


@pytest.mark.parametrize("field", ["ram_gib", "available_ram_gib", "resident"])
@pytest.mark.parametrize("value", [None, True, "6.5", float("nan"), float("inf"), -float("inf"), -0.1, 10**1000])
def test_invalid_memory_measurement_never_enlarges_context(field, value):
    hardware = HardwareProfile(16, 8, "Windows", 6.5)
    resident = 0
    if field == "resident":
        resident = value
    else:
        hardware = replace(hardware, **{field: value})
    assert grounded_profile(QWEN, hardware, resident) is QWEN


@pytest.mark.parametrize("available, resident", [(16.1, 0), (6.5, 16.1)])
def test_impossible_individual_memory_measurements_do_not_enlarge(available, resident):
    assert grounded_profile(QWEN, HardwareProfile(16, 8, "Windows", available), resident) is QWEN


@pytest.mark.parametrize("name", ["hermes3:3b", "qwen3:0.6b", "qwen3:1.7b-candidate", "qwen3:latest"])
def test_only_exact_measured_model_is_eligible(name):
    profile = replace(QWEN, model=name)
    assert grounded_profile(profile, HardwareProfile(16, 8, "Windows", 12)) is profile


@pytest.mark.parametrize("context", [8192, 12288, 16384])
def test_existing_context_is_never_reduced(context):
    profile = replace(QWEN, context=context)
    assert grounded_profile(profile, HardwareProfile(16, 8, "Windows", 4.5)) is profile
    result = grounded_profile(profile, HardwareProfile(16, 8, "Windows", 6.5))
    assert result.context == max(context, 12288)


def test_context_eligibility_never_increases_threads_or_changes_profile():
    profile = replace(QWEN, cpu_threads=2)
    result = grounded_profile(profile, HardwareProfile(16, 32, "Windows", 6.5))
    assert result == replace(profile, context=12288)
    assert profile.context == 4096
