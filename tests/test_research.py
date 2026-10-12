import pytest
from dataclasses import replace

from prometheus_assistant.memory import MemoryStore
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


def test_research_context_counts_separators_and_can_be_retained(tmp_path):
    source = ResearchSource(url="https://example.com/a", title="Source A",
                            retrieved_at="2026-10-09T00:00:00+00:00",
                            content_hash="a" * 64, excerpt="")
    header_length = len(ResearchResult("query", (source,)).context())
    source = replace(source, excerpt="x" * (2000 - header_length))
    result = ResearchResult("query", (source, source))
    complete = result.context(max_chars=5000)
    assert len(complete) == 4001
    bounded = result.context(max_chars=4000)
    assert len(bounded) <= 4000
    assert "[Source 1]" in bounded and "[Source 2]" not in bounded
    assert result.context(max_chars=4001) == complete
    with MemoryStore(tmp_path / "memory.sqlite3") as memory:
        session = memory.create_session("fixture")
        memory.retain_research(session, "query", bounded)
        assert memory.research_notes("query")[0]["context"] == bounded
