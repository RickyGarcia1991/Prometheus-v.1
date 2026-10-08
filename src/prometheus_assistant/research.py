from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ResearchSource:
    url: str
    title: str
    retrieved_at: str
    content_hash: str
    excerpt: str

    @classmethod
    def from_content(cls, *, url: str, title: str, content: str, excerpt: str | None = None):
        url, title, content = url.strip(), title.strip(), content.strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError("Research sources require an HTTP(S) URL.")
        if not title or not content:
            raise ValueError("Research source title and content are required.")
        return cls(
            url=url,
            title=title,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            excerpt=(excerpt if excerpt is not None else content[:1000]).strip(),
        )


@dataclass(frozen=True)
class ResearchResult:
    query: str
    sources: tuple[ResearchSource, ...]
    diagnostics: tuple[str, ...] = ()

    def context(self, max_chars: int = 6000) -> str:
        if max_chars < 1:
            raise ValueError("Research context limit must be positive.")
        blocks = []
        status = "Provider status: " + "; ".join(self.diagnostics) if self.diagnostics else ""
        if status and len(status) < max_chars // 3:
            blocks.append(status)
        used = sum(len(block) for block in blocks)
        for index, source in enumerate(self.sources, 1):
            block = f"[Source {index}] {source.title}\nURL: {source.url}\nRetrieved: {source.retrieved_at}\nContent SHA-256: {source.content_hash}\n{source.excerpt}\n"
            separator = 1 if blocks else 0
            if used + separator + len(block) > max_chars:
                break
            blocks.append(block)
            used += separator + len(block)
        return "\n".join(blocks)


class ResearchProvider(Protocol):
    def search(self, query: str) -> ResearchResult:
        ...


class DisabledResearchProvider:
    def search(self, query: str) -> ResearchResult:
        raise RuntimeError("Online research provider is not configured.")
