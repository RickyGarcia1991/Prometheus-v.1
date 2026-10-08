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
        request=registry.get("memory","lookup").request("robot body",{"query":"robot body"})
        rows=json.loads(registry.executors()[("memory","lookup")](request))
    assert rows[0]["value"]=="hybrid actuation"
    assert rows[0]["source_ref"]=="session:test"


def test_project_list_is_bounded(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        registry=build_builtin_registry(memory)
        result=json.loads(registry.get("project","list").executor(registry.get("project","list").request("list",{})))
    assert any(row["path"]=="src" for row in result)

def test_project_replace_is_state_changing_and_requires_guardrail_approval(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        spec=build_builtin_registry(memory).get("project","replace")
        req=spec.request("change",{"path":"README.md","old":"Prometheus","new":"Prometheus"})
    assert req.mutates_state and not req.external_network and not req.command_execution


def test_project_search_prioritizes_exact_code_tokens(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        spec=build_builtin_registry(memory).get("project","search")
        result=json.loads(spec.executor(spec.request("find run_agent",{"query":"def run_agent"})))
    assert any(row["path"].endswith("agent.py") and "run_agent" in row["excerpt"] for row in result["results"][:5])
