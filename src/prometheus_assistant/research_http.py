from __future__ import annotations

import json
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .research import ResearchResult, ResearchSource


class HttpJsonResearchProvider:
    """Search-provider adapter that converts JSON results into provenance records."""

    def __init__(self, endpoint: str, *, fetch: Callable | None = None, timeout: int = 20):
        if not endpoint.startswith("https://"):
            raise ValueError("Research search endpoint must use HTTPS.")
        self.endpoint = endpoint
        self.fetch = fetch or self._fetch
        self.timeout = timeout

    def _fetch(self, url: str) -> bytes:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Prometheus/0.2"})
        with urlopen(request, timeout=self.timeout) as response:
            return response.read(2_000_001)

    def search(self, query: str) -> ResearchResult:
        query = query.strip()
        if not query:
            raise ValueError("Research query cannot be empty.")
        url = self.endpoint + ("&" if "?" in self.endpoint else "?") + urlencode({"q": query})
        body = self.fetch(url)
        if len(body) > 2_000_000:
            raise RuntimeError("Research response exceeded the safe size limit.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise RuntimeError("Research provider returned invalid JSON.") from error
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("Research provider response is missing a results list.")
        sources = []
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            source_url = item.get("url")
            title = item.get("title")
            content = item.get("content") or item.get("excerpt")
            if not all(isinstance(value, str) and value.strip() for value in (source_url, title, content)):
                continue
            sources.append(ResearchSource.from_content(
                url=source_url, title=title, content=content, excerpt=item.get("excerpt")
            ))
        return ResearchResult(query, tuple(sources))
