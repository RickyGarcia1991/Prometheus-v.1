import json
import pytest

from prometheus_assistant.research_http import HttpJsonResearchProvider


def payload(value):
    return json.dumps(value).encode("utf-8")


def test_http_provider_builds_query_and_provenance():
    seen = []
    provider = HttpJsonResearchProvider(
        "https://search.example/api",
        fetch=lambda url: seen.append(url) or payload({"results": [{
            "url": "https://docs.example/robot",
            "title": "Robot",
            "content": "technical evidence",
            "excerpt": "technical evidence",
        }]}),
    )
    result = provider.search("robot hand")
    assert "q=robot+hand" in seen[0]
    assert result.query == "robot hand"
    assert result.sources[0].url == "https://docs.example/robot"
    assert len(result.sources[0].content_hash) == 64


def test_http_provider_requires_https_endpoint():
    with pytest.raises(ValueError, match="HTTPS"):
        HttpJsonResearchProvider("http://search.example/api")


def test_http_provider_rejects_invalid_json():
    provider = HttpJsonResearchProvider("https://search.example/api", fetch=lambda _: b"not-json")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        provider.search("robotics")


def test_http_provider_rejects_oversized_response():
    provider = HttpJsonResearchProvider("https://search.example/api", fetch=lambda _: b"x" * 2_000_001)
    with pytest.raises(RuntimeError, match="size limit"):
        provider.search("robotics")


@pytest.mark.parametrize("url", ["http://example.org/a", "javascript:alert(1)", "https://user:pass@example.org/a", "https:///missing-host"])
def test_http_provider_skips_unsafe_source_urls(url):
    provider = HttpJsonResearchProvider(
        "https://search.example/api",
        fetch=lambda _: payload({"results": [{"url": url, "title": "Bad", "content": "untrusted"}]}),
    )
    assert provider.search("robotics").sources == ()


def test_http_provider_skips_invalid_or_excessive_excerpts():
    provider = HttpJsonResearchProvider(
        "https://search.example/api",
        fetch=lambda _: payload({"results": [
            {"url": "https://example.org/one", "title": "One", "content": "ok", "excerpt": {"unsafe": True}},
            {"url": "https://example.org/two", "title": "Two", "content": "ok", "excerpt": "x" * 10_001},
        ]}),
    )
    assert provider.search("robotics").sources == ()


def test_http_provider_skips_oversized_source_content():
    provider = HttpJsonResearchProvider(
        "https://search.example/api",
        fetch=lambda _: payload({"results": [{"url": "https://example.org/large", "title": "Large", "content": "x" * 100_001}]}),
    )
    assert provider.search("robotics").sources == ()


def test_http_provider_skips_malformed_results():
    provider = HttpJsonResearchProvider(
        "https://search.example/api",
        fetch=lambda _: payload({"results": [{"title": "missing fields"}, 7]}),
    )
    assert provider.search("robotics").sources == ()
