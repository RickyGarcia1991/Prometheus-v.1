"""Unified context planning for Prometheus."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ContextPlan:
    use_memory: bool
    use_offline: bool
    use_online: bool
    reason: str

CURRENT_HINTS = ("latest", "current ", "today", "news", "right now", "search the web", "search online", "look online")
FACTUAL_HINTS = ("who ", "what ", "when ", "where ", "define ", "meaning ", "history ", "explain ", "tell me about")

def plan_context(prompt: str, *, offline_available: bool = True, online_enabled: bool = False) -> ContextPlan:
    text = " ".join(prompt.lower().split())
    current = any(hint in text for hint in CURRENT_HINTS)
    factual = current or any(text.startswith(hint) or hint in text for hint in FACTUAL_HINTS)
    if current and online_enabled:
        return ContextPlan(True, bool(offline_available), True, "current information requested")
    if factual and offline_available:
        return ContextPlan(True, True, False, "factual prompt can use local knowledge")
    return ContextPlan(True, False, False, "memory and local reasoning are sufficient")
