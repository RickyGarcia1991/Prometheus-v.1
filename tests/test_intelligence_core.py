from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.cli import _memory_context_for_prompt, _offline_context_for_prompt

def test_recall_ranks_relevant_active_knowledge(tmp_path):
    with MemoryStore(tmp_path/"m.db") as m:
        m.remember("preference","robot actuation","Use electric actuators for hands and hydraulics for hips.",source_type="user",source_ref="test")
        m.remember("fact","unrelated","blue",source_type="user",source_ref="test")
        rows=m.recall("What actuator should the robot hands use?")
        assert rows and rows[0]["subject"]=="robot actuation"

def test_memory_context_can_be_disabled(tmp_path):
    with MemoryStore(tmp_path/"m.db") as m:
        m.remember("decision","storage","Keep snapshots immutable.",source_type="user",source_ref="test")
        assert "Keep snapshots immutable" in _memory_context_for_prompt(m,"storage snapshots")
        assert _memory_context_for_prompt(m,"storage snapshots",enabled=False) is None

def test_offline_context_handles_missing_resource_root(monkeypatch):
    monkeypatch.setattr("prometheus_assistant.cli.resource_root",lambda:None)
    assert _offline_context_for_prompt("Prometheus",enabled=True) is None
