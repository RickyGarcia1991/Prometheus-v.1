import json

import pytest

from prometheus_assistant.activity import ActivityLog
from prometheus_assistant.orchestration import Route, choose_route


def test_activity_log_is_append_only_jsonl(tmp_path):
    log = ActivityLog(tmp_path / "activity.jsonl")
    event = log.record("research", "started", "Searching approved sources.", query="robotics")
    log.record("research", "completed", "Research complete.", sources=3)
    rows = log.recent()
    assert len(rows) == 2
    assert rows[0]["message"] == event.message
    assert rows[0]["metadata"]["query"] == "robotics"
    assert json.loads((tmp_path / "activity.jsonl").read_text().splitlines()[1])["metadata"]["sources"] == 3


def test_activity_log_rejects_empty_fields(tmp_path):
    with pytest.raises(ValueError):
        ActivityLog(tmp_path / "activity.jsonl").record("", "started", "message")


def test_online_request_routes_to_research_only_when_enabled():
    disabled = choose_route("Search the web for the latest robotics news")
    enabled = choose_route("Search the web for the latest robotics news", online_enabled=True)
    assert disabled.route is Route.LOCAL
    assert disabled.requires_network is False
    assert enabled.route is Route.RESEARCH
    assert enabled.requires_network is True


def test_normal_prompt_stays_local_even_when_online_is_enabled():
    decision = choose_route("Explain how a differential works", online_enabled=True)
    assert decision.route is Route.LOCAL
    assert decision.requires_network is False


def test_interactive_research_context_routes_each_turn(monkeypatch):
    import prometheus_assistant.cli as cli
    from prometheus_assistant.research import ResearchResult, ResearchSource

    class Provider:
        def __init__(self, endpoint):
            assert endpoint == "https://search.example/api"

        def search(self, query):
            assert query == "Search the web for current robotics news"
            source = ResearchSource.from_content(
                url="https://example.test/robotics", title="Robotics",
                content="current robotics evidence")
            return ResearchResult(query=query, sources=(source,))

    monkeypatch.setattr(cli, "HttpJsonResearchProvider", Provider)
    context = cli._research_context_for_prompt(
        "Search the web for current robotics news", online_enabled=True,
        research_endpoint="https://search.example/api")
    assert "current robotics evidence" in context
    assert "https://example.test/robotics" in context


def test_interactive_research_context_stays_off_without_opt_in(monkeypatch):
    import prometheus_assistant.cli as cli

    class Provider:
        def __init__(self, endpoint):
            raise AssertionError("network provider must not be created")

    monkeypatch.setattr(cli, "HttpJsonResearchProvider", Provider)
    assert cli._research_context_for_prompt(
        "Search the web for current robotics news", online_enabled=False,
        research_endpoint="https://search.example/api") is None
