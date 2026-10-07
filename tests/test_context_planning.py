from prometheus_assistant.intelligence import plan_context

def test_general_prompt_uses_memory_and_reasoning_only():
    p=plan_context("Help me brainstorm a project")
    assert p.use_memory and not p.use_offline and not p.use_online

def test_factual_prompt_uses_offline_knowledge():
    p=plan_context("Explain how a differential works")
    assert p.use_memory and p.use_offline and not p.use_online

def test_current_prompt_uses_online_only_when_enabled():
    off=plan_context("What is the latest robotics news?",online_enabled=False)
    on=plan_context("What is the latest robotics news?",online_enabled=True)
    assert off.use_offline and not off.use_online
    assert on.use_offline and on.use_online
