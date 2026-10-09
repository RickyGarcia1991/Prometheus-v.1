import pytest
from prometheus_assistant.hardware import HardwareProfile
from prometheus_assistant.model_routing import infer_role,choose_helper,routing_record


@pytest.mark.parametrize('prompt,role',[
    ('Debug this Python function','coding'),('Translate this into Spanish','translation'),
    ('Draft a short story','writing'),('Prove this theorem','reasoning'),('Hello there','general'),
    ('Review this SQL query','coding'),('Write a C++ parser','coding')])
def test_roles(prompt,role):assert infer_role(prompt)==role


def test_task_and_attachment_override_text():
    assert infer_role('write a story','coding')=='coding'
    assert infer_role('debug this function',image=True)=='vision'


def test_8_gib_coder_uses_small_fitting_helper():
    installed={'qwen2.5-coder:0.5b','qwen2.5-coder:1.5b','gpt-oss:20b'}
    model=choose_helper(installed,HardwareProfile(8,8,'Windows',2.4),'coding')
    assert model.model=='qwen2.5-coder:0.5b' and model.context==2048


def test_upgrade_to_specialist_when_headroom_returns():
    installed={'qwen2.5-coder:0.5b','qwen2.5-coder:1.5b'}
    assert choose_helper(installed,HardwareProfile(8,8,'Windows',4),'coding').model=='qwen2.5-coder:1.5b'


def test_no_fit_never_selects_embedding_or_oversized_model():
    assert choose_helper({'qwen3-embedding:0.6b','gpt-oss:20b'},HardwareProfile(8,8,'Windows',.8),'general') is None
    assert choose_helper({'llama3.2:1b'},HardwareProfile(32,16,'Windows',20),'vision') is None


def test_large_host_can_use_reasoning_helper():
    assert choose_helper({'gpt-oss:20b','qwen3:1.7b'},HardwareProfile(32,16,'Windows',24),'reasoning').model=='gpt-oss:20b'


def test_coding_to_translation_switch_is_automatic():
    names={'qwen2.5-coder:1.5b','qwen3:1.7b'};host=HardwareProfile(8,8,'Windows',4)
    a=routing_record('debug this code',names,host)
    b=routing_record('translate into French',names,host)
    assert a[1].model=='qwen2.5-coder:1.5b' and b[1].model=='qwen3:1.7b'
    assert a[2]['automatic'] and b[2]['parallel_models']==1
