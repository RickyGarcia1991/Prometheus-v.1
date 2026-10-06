"""Development backend used until a local model adapter is selected."""
from typing import Sequence

class StubModel:
    def generate(self, messages: Sequence[dict[str, str]]) -> str:
        last = messages[-1]["content"] if messages else ""
        return f"Prometheus received: {last}"
