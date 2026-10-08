import json
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.memory import MemoryStore

def test_builtin_registry_is_read_only(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        registry=build_builtin_registry(memory)
        for key in [("memory","lookup"),("knowledge","articles"),
                    ("knowledge","resources"),("system","summary")]:
            tool=registry.get(*key)
            assert not tool.mutates_state
            assert not tool.external_network
            assert not tool.command_execution

def test_memory_tool_preserves_provenance(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        memory.remember("decision","robot body","hybrid actuation",
                        source_type="user",source_ref="session:test",confidence=.9)
        registry=build_builtin_registry(memory)
        request=registry.get("memory","lookup").request("robot body")
        rows=json.loads(registry.executors()[("memory","lookup")](request))
    assert rows[0]["value"]=="hybrid actuation"
    assert rows[0]["source_ref"]=="session:test"
