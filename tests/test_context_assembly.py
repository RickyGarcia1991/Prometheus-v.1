import pytest
from prometheus_assistant.context import Evidence, assemble_context

def test_context_respects_budget_and_priority():
    result=assemble_context([
        Evidence("offline","low","L"*100,10),
        Evidence("memory","high","H"*100,90),
    ],max_chars=100)
    assert result["chars"] <= 100
    assert result["sources"][0]["ref"]=="high"
    assert "low" not in result["context"]

def test_context_records_provenance():
    result=assemble_context([Evidence("wikipedia","Article","body",70)])
    assert result["sources"]==[{"type":"wikipedia","ref":"Article","chars":4}]
    assert "[SOURCE wikipedia: Article]" in result["context"]

def test_context_rejects_invalid_budget():
    with pytest.raises(ValueError):
        assemble_context([],0)
