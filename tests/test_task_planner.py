from prometheus_assistant.task_planner import plan_task

def test_status_prompt_plans_status_tool():
    p=plan_task("What is the Prometheus system status?")
    assert [s.tool for s in p.steps]==["system_status"]

def test_resource_prompt_plans_inventory():
    p=plan_task("What knowledge resources are installed?")
    assert [s.tool for s in p.steps]==["resource_inventory"]

def test_factual_prompt_plans_offline_knowledge():
    p=plan_task("Explain hydraulic actuators")
    assert [s.tool for s in p.steps]==["offline_knowledge"]
    assert p.steps[0].args==("Explain hydraulic actuators",)

def test_combined_prompt_prefers_specific_local_tools():
    p=plan_task("Explain what knowledge resources are installed and system status")
    assert [s.tool for s in p.steps]==["system_status","resource_inventory"]

def test_casual_prompt_needs_no_tool():
    assert plan_task("hello there").steps==()
