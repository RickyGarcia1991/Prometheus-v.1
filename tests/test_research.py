import pytest

from prometheus_assistant.research import DisabledResearchProvider, ResearchResult, ResearchSource


def test_research_source_records_provenance_and_hash():
    source = ResearchSource.from_content(
        url="https://example.com/robotics",
        title="Robotics Notes",
        content="verified source body",
    )
    assert source.url == "https://example.com/robotics"
    assert source.retrieved_at.endswith("+00:00")
    assert len(source.content_hash) == 64
    assert source.excerpt == "verified source body"


def test_research_source_rejects_non_http_source():
    with pytest.raises(ValueError):
        ResearchSource.from_content(url="file:///secret.txt", title="Bad", content="data")


def test_research_context_preserves_source_identity():
    source = ResearchSource.from_content(
        url="https://example.com/a", title="Source A", content="evidence"
    )
    text = ResearchResult("query", (source,)).context()
    assert "Source A" in text
    assert "https://example.com/a" in text
    assert "Retrieved:" in text
    assert "evidence" in text


def test_disabled_provider_fails_closed():
    with pytest.raises(RuntimeError, match="not configured"):
        DisabledResearchProvider().search("latest robotics")
