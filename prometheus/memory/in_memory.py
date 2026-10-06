"""Small deterministic memory backend for development and tests."""
from uuid import uuid4

class InMemoryStore:
    def __init__(self) -> None:
        self._items: list[tuple[str, str, dict]] = []

    def remember(self, text: str, *, metadata: dict | None = None) -> str:
        memory_id = str(uuid4())
        self._items.append((memory_id, text, metadata or {}))
        return memory_id

    def recall(self, query: str, *, limit: int = 5) -> list[str]:
        terms = query.lower().split()
        matches = [text for _, text, _ in reversed(self._items) if any(t in text.lower() for t in terms)]
        return matches[:limit]
