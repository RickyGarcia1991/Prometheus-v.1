from prometheus_assistant.hardware import HardwareProfile, select_model
from prometheus_assistant.compact_chat import candidates
from prometheus_assistant.model_quality import ModelEvidence


def test_measured_small_profiles_keep_explicit_headroom():
    names={'gemma3:270m','qwen2.5-coder:0.5b'}
    assert select_model(names,HardwareProfile(8,8,'Windows',.84)) is None
    assert select_model({'gemma3:270m'},HardwareProfile(8,8,'Windows',.85)).context==1024
    assert select_model({'qwen2.5-coder:0.5b'},HardwareProfile(8,8,'Windows',1.04),task='coding') is None
    profile=select_model({'qwen2.5-coder:0.5b'},HardwareProfile(8,8,'Windows',1.05),task='coding')
    assert profile.context==1024 and profile.cpu_threads<=4
    assert select_model({'qwen2.5-coder:0.5b'},HardwareProfile(8,8,'Windows',.72),.38,task='coding')


def test_benchmark_evidence_changes_selection_without_bypassing_ram(tmp_path):
    evidence=ModelEvidence(tmp_path/'evidence.sqlite3')
    for model,passed,latency in [('gemma3:270m',False,.2),('qwen2.5-coder:0.5b',True,.4)]:
        for case in ('case_a','case_b'):
            evidence.record_benchmark(model,'general',case,passed=passed,seconds=latency)
    names={'gemma3:270m','qwen2.5-coder:0.5b'}
    _,choices=candidates('Hello',names,HardwareProfile(8,8,'Windows',1.3),evidence)
    assert choices[0].model=='qwen2.5-coder:0.5b'
    _,choices=candidates('Hello',names,HardwareProfile(8,8,'Windows',.9),evidence)
    assert [p.model for p in choices]==['gemma3:270m']
    evidence.record_failure('gemma3:270m','Test failure')
    assert candidates('Hello',names,HardwareProfile(8,8,'Windows',.9),evidence)[1]==[]
