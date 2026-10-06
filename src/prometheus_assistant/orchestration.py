from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Route(str, Enum):
    LOCAL = "local"
    RESEARCH = "research"


@dataclass(frozen=True)
class OrchestrationDecision:
    route: Route
    reason: str
    requires_network: bool
    requires_confirmation: bool = False


RESEARCH_PHRASES = (
    "search the web", "search online", "look online", "look it up online",
    "latest", "current news", "today's", "on the internet", "research online",
)


def choose_route(prompt: str, *, online_enabled: bool = False) -> OrchestrationDecision:
    """Choose a transparent route without silently enabling network access."""
    normalized = " ".join(prompt.lower().split())
    wants_research = any(phrase in normalized for phrase in RESEARCH_PHRASES)
    if wants_research and online_enabled:
        return OrchestrationDecision(Route.RESEARCH, "User requested current/online research.", True)
    if wants_research:
        return OrchestrationDecision(
            Route.LOCAL,
            "Online research was requested but network research is disabled.",
            False,
        )
    return OrchestrationDecision(Route.LOCAL, "Local reasoning is sufficient by policy.", False)
