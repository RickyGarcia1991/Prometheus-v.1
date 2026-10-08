from prometheus_assistant.core import (
    build_plan, memory_context, run_core,
)
from prometheus_assistant.guardrails import ToolRequest
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.tracing import LocalTraceLog


def memory(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def test_plan_retrieves_relevant_memory_with_provenance(tmp_path):
    with memory(tmp_path) as store:
        store.remember("preference", "coding style", "small verified patches",
                       source_type="user", source_ref="session:1", confidence=.95)
        store.remember("fact", "vehicle", "blue sedan",
                       source_type="user", source_ref="session:2")
        plan = build_plan(store, "Use my coding style for this patch")
    assert len(plan.memory) == 1
    assert plan.memory[0].subject == "coding style"
    assert plan.memory[0].source_ref == "session:1"
    assert "not instructions" in memory_context(plan.memory)


def test_core_does_not_execute_mutating_tool_without_approval(tmp_path):
    called = []
    request = ToolRequest("code", "write", "edit source", mutates_state=True)
    with memory(tmp_path) as store:
        result = run_core(
            store, "make the edit", tool_requests=[request],
            executors={("code", "write"): lambda _: called.append(True) or "changed"},
            responder=lambda *_: "done",
        )
    assert called == []
    assert not result.evaluation.passed
    assert result.evidence[0].status == "approval"
    assert result.reply is None


def test_explicit_approval_allows_exact_requested_tool(tmp_path):
    request = ToolRequest("code", "write", "edit source", mutates_state=True)
    with memory(tmp_path) as store:
        result = run_core(
            store, "make the edit", tool_requests=[request], approved_request_ids=[0],
            executors={("code", "write"): lambda _: "changed"},
            responder=lambda plan, evidence: f"{plan.route}:{evidence[0].output}",
        )
    assert result.evaluation.passed
    assert result.evaluation.tool_accuracy == 1.0
    assert result.reply == "local:changed"


def test_missing_executor_fails_closed(tmp_path):
    request = ToolRequest("local", "inspect", "inspect source")
    with memory(tmp_path) as store:
        result = run_core(store, "inspect it", tool_requests=[request])
    assert not result.evaluation.passed
    assert result.evidence[0].status == "unavailable"


def test_executor_failure_is_evidence_not_a_reply(tmp_path):
    request = ToolRequest("local", "inspect", "inspect source")
    def broken(_):
        raise RuntimeError("fixture failure")
    with memory(tmp_path) as store:
        result = run_core(store, "inspect it", tool_requests=[request],
                          executors={("local", "inspect"): broken},
                          responder=lambda *_: "should not happen")
    assert not result.evaluation.passed
    assert result.evidence[0].status == "error"
    assert "fixture failure" in result.evidence[0].output
    assert result.reply is None


def test_trace_records_plan_guardrail_and_evaluation(tmp_path):
    request = ToolRequest("local", "inspect", "read source")
    trace = LocalTraceLog(tmp_path / "trace.jsonl")
    with memory(tmp_path) as store:
        result = run_core(
            store, "inspect source", tool_requests=[request],
            executors={("local", "inspect"): lambda _: "ok"},
            responder=lambda *_: "complete", trace=trace,
        )
    rows = trace.path.read_text(encoding="utf-8").splitlines()
    assert result.evaluation.passed
    assert len(rows) == 4
    assert '"kind": "execution"' in rows[2]
    assert '"decision": "complete"' in rows[2]
    assert all(result.plan.trace_id in row for row in rows)


def test_online_route_never_implies_network_permission(tmp_path):
    request = ToolRequest("research", "http", "fetch evidence", external_network=True)
    with memory(tmp_path) as store:
        result = run_core(
            store, "Search the web for latest robotics news", online_enabled=True,
            tool_requests=[request], executors={("research", "http"): lambda _: "evidence"},
        )
    assert result.plan.route == "research"
    assert result.evidence[0].status == "approval"
    assert not result.evaluation.passed


def test_core_never_auto_writes_durable_knowledge(tmp_path):
    with memory(tmp_path) as store:
        before = store.knowledge()
        result = run_core(store, "My favorite color is green", responder=lambda *_: "noted")
        after = store.knowledge()
    assert result.evaluation.passed
    assert result.reply == "noted"
    assert before == after == []


def test_approval_ids_are_bound_to_exact_plan(tmp_path):
    request = ToolRequest("code", "write", "edit source", mutates_state=True)
    with memory(tmp_path) as store:
        import pytest
        with pytest.raises(ValueError, match="exact plan"):
            run_core(store, "edit", tool_requests=[request], approved_request_ids=[1],
                     executors={("code", "write"): lambda _: "changed"})


def test_memory_context_is_bounded(tmp_path):
    with memory(tmp_path) as store:
        for i in range(20):
            store.remember("fact", f"robotics item {i}", "x" * 200,
                           source_type="fixture", source_ref=str(i))
        plan = build_plan(store, "robotics")
    rendered = memory_context(plan.memory, max_chars=500)
    assert len(rendered) <= 560
    assert rendered.startswith("MEMORY EVIDENCE")
