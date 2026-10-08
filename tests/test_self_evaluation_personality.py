from types import SimpleNamespace
from prometheus_assistant.personality import Personality,detect_emotional_state,personality_instruction
from prometheus_assistant.self_evaluation import evaluate_agent_result

def core(reply="ok",passed=True,auth=True,accuracy=1.0,evidence=()):
    return SimpleNamespace(reply=reply,evidence=evidence,plan=SimpleNamespace(prompt="hello"),evaluation=SimpleNamespace(passed=passed,authorization_ok=auth,tool_accuracy=accuracy))

def test_self_evaluation_scores_clean_cycle():
    result=evaluate_agent_result(core())
    assert result.passed and result.overall>=.8

def test_self_evaluation_exposes_authorization_failure():
    result=evaluate_agent_result(core(reply=None,passed=False,auth=False))
    assert not result.passed and "authorization failed" in result.reasons

def test_emotional_state_is_bounded_presentation_signal():
    state=detect_emotional_state("I'm frustrated, this is not working")
    text=personality_instruction(Personality("companion",True),state)
    assert state.label=="frustrated"
    assert "never changes permissions" in text
    assert "Reduce banter" in text

def test_neutral_state_does_not_invent_emotion():
    assert detect_emotional_state("Show me the project status").label=="neutral"


def test_self_evaluation_flags_unsupported_current_system_claim():
    item=core(reply="version 99",evidence=())
    item.plan.prompt="What Core version is running?"
    result=evaluate_agent_result(item)
    assert not result.passed
    assert "current system/hardware/status claim lacks tool evidence" in result.reasons


def test_hardware_question_requires_runtime_evidence():
    c=core(reply="invented specs")
    c.plan=SimpleNamespace(prompt="What operating system and hardware resources are available on this computer?")
    result=evaluate_agent_result(c)
    assert not result.passed
    assert "current system/hardware/status claim lacks tool evidence" in result.reasons
