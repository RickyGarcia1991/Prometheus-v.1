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
