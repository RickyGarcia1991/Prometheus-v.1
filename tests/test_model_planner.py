from prometheus_assistant.model_planner import constrain_suggestions,parse_tool_suggestions

def test_model_cannot_add_shell_or_network_authority():
    p=constrain_suggestions("Show system status",["shell","network","system_status"])
    assert [s.tool for s in p.steps]==["system_status"]

def test_model_cannot_turn_casual_prompt_into_tool_use():
    p=constrain_suggestions("hello",["system_status","offline_knowledge"])
    assert p.steps==()

def test_model_can_narrow_deterministic_authority():
    p=constrain_suggestions("knowledge resources and system status",["resource_inventory"])
    assert [s.tool for s in p.steps]==["resource_inventory"]

def test_parser_accepts_only_known_read_only_names():
    assert parse_tool_suggestions("system_status, shell offline_knowledge")==("system_status","offline_knowledge")

def test_parser_rejects_non_text():
    assert parse_tool_suggestions(None)==()
