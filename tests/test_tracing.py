import json

from prometheus_assistant.tracing import LocalTraceLog, new_trace_id


def test_trace_id_format():
    value=new_trace_id()
    assert value.startswith("trace_")
    assert len(value)==38


def test_local_trace_is_jsonl_and_records_decision(tmp_path):
    path=tmp_path/"trace.jsonl"
    event=LocalTraceLog(path).record(
        trace_id=new_trace_id(),kind="guardrail",actor="code-worker",
        action="shell:test",decision="approval",reason="command execution",
    )
    row=json.loads(path.read_text(encoding="utf-8").strip())
    assert row["trace_id"]==event.trace_id
    assert row["decision"]=="approval"
    assert row["actor"]=="code-worker"
