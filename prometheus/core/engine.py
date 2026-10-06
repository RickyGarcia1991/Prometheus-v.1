"""Prometheus executive loop.

The engine owns orchestration. External models and databases remain replaceable adapters.
"""
from __future__ import annotations
from prometheus.contracts import MemoryBackend, ModelBackend
from prometheus.identity.profile import IdentityProfile

class PrometheusEngine:
    def __init__(self, model: ModelBackend, memory: MemoryBackend, identity: IdentityProfile | None = None) -> None:
        self.model = model
        self.memory = memory
        self.identity = identity or IdentityProfile()

    def respond(self, user_text: str) -> str:
        context = self.memory.recall(user_text)
        system = f"You are {self.identity.name}, a {self.identity.role}. Principles: " + "; ".join(self.identity.principles)
        if context:
            system += "\nRelevant memory:\n" + "\n".join(context)
        reply = self.model.generate([{"role": "system", "content": system}, {"role": "user", "content": user_text}])
        self.memory.remember(f"User: {user_text}")
        self.memory.remember(f"Prometheus: {reply}")
        return reply
