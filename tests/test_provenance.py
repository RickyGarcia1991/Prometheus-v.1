from prometheus_assistant.context import Evidence
from prometheus_assistant.synthesis import synthesize_context
from prometheus_assistant.provenance import provenance_summary

def test_provenance_summary_is_serializable_metadata():
    packed=synthesize_context("robot",[Evidence("wikipedia","Robot","robot machine",60)])
    out=provenance_summary(packed)
    assert out["evidence_count"]==1
    assert out["evidence_sources"][0]["type"]=="wikipedia"
    assert out["evidence_sources"][0]["ref"]=="Robot"
