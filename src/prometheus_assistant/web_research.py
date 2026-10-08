"""Credential-free live research across web, encyclopedia, and scholarly APIs."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from .research import ResearchResult, ResearchSource

PROVIDERS = ("federated", "mwmbl", "wikipedia", "crossref")
MAX_BYTES = 2_000_000
USER_AGENT = "PrometheusResearch/0.8 (+https://github.com/RickyGarcia1991/Prometheus-v.1)"

class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname or new.username or new.password:
            raise RuntimeError("Research provider attempted an unexpected redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_json(url, *, timeout=12):
    opener = build_opener(ProxyHandler({}), _SameHostRedirect())
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"Research provider HTTP status {response.status}.")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise RuntimeError("Research response exceeded the size limit.")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("Research provider returned invalid JSON.") from error

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data): self.parts.append(data)

def plain_text(value):
    if not isinstance(value, str): return ""
    parser = _Text()
    parser.feed(value)
    return " ".join(unescape(" ".join(parser.parts)).split())

def normalize_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise ValueError("Research query must contain 1 to 4000 characters.")
    query = " ".join(query.split())
    query = re.sub(r"^(?:please\s+)?(?:search (?:the web|online)|look (?:it up )?online|research online)(?:\s+for|\s+about)?\s*[:,-]?\s*", "", query, flags=re.I)
    if not query.strip(" ?"):
        raise ValueError("Provide a topic after the web-search request.")
    return query.strip(" ?")[:500]

def _parts(value):
    if isinstance(value, str): return plain_text(value)
    if isinstance(value, list):
        return plain_text("".join(item.get("value", "") for item in value if isinstance(item, dict) and isinstance(item.get("value"), str)))
    return ""

def _source(url, title, body, label):
    if not all(isinstance(value, str) and value.strip() for value in (url, title, body)):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        excerpt = label + " " + body[:700]
        return ResearchSource.from_content(url=url, title=title[:180], content=body, excerpt=excerpt)
    except ValueError:
        return None

class PublicResearchProvider:
    """Three bounded API requests, no model execution and no paid API keys."""
    def __init__(self, provider="federated", *, fetch=None):
        if provider not in PROVIDERS: raise ValueError("Unknown public research provider.")
        self.provider = provider
        self.fetch = fetch or fetch_json

    def _search_one(self, name, query):
        if name == "mwmbl":
            payload = self.fetch("https://api.mwmbl.org/search?" + urlencode({"s": query}))
            if not isinstance(payload, list): raise RuntimeError("Invalid Mwmbl result schema.")
            rows = [_source(row.get("url"), _parts(row.get("title")), _parts(row.get("extract")),
                            "Mwmbl search snippet; full page not fetched:")
                    for row in payload[:8] if isinstance(row, dict)]
        elif name == "wikipedia":
            payload = self.fetch("https://en.wikipedia.org/w/api.php?" + urlencode({
                "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 3,
                "prop": "extracts|info", "inprop": "url", "exintro": 1, "explaintext": 1,
                "exchars": 700, "exlimit": "max", "format": "json"}))
            if not isinstance(payload, dict) or "error" in payload: raise RuntimeError("Invalid Wikipedia result schema.")
            pages = payload.get("query", {}).get("pages", {})
            if not isinstance(pages, dict): raise RuntimeError("Invalid Wikipedia pages.")
            rows = [_source(row.get("fullurl"), row.get("title"), row.get("extract"),
                            "Wikipedia introductory excerpt; tertiary reference:")
                    for row in sorted((p for p in pages.values() if isinstance(p, dict)),
                                      key=lambda p: p.get("index", 99))[:3]]
        else:
            payload = self.fetch("https://api.crossref.org/works?" + urlencode({"query.bibliographic": query, "rows": 3}))
            items = payload.get("message", {}).get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list): raise RuntimeError("Invalid Crossref result schema.")
            rows = []
            for row in items[:3]:
                if not isinstance(row, dict): continue
                titles = row.get("title", [])
                title = plain_text(titles[0]) if isinstance(titles, list) and titles else ""
                abstract = plain_text(row.get("abstract"))
                publisher = plain_text(row.get("publisher"))
                date = row.get("published", {}).get("date-parts", [[]])
                body = f"Title: {title}. Publisher: {publisher}. Publication date: {date}. "
                body += ("Abstract: " + abstract) if abstract else "No abstract supplied; this is bibliographic metadata only."
                rows.append(_source(row.get("URL"), title, body, "Crossref scholarly metadata; full paper not fetched:"))
        return tuple(row for row in rows if row is not None)

    def search(self, query):
        search_query = normalize_query(query)
        names = ("wikipedia", "mwmbl", "crossref") if self.provider == "federated" else (self.provider,)
        groups, diagnostics = [], []
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = [(name, pool.submit(self._search_one, name, search_query)) for name in names]
            for name, future in futures:
                try:
                    rows = future.result()
                    groups.append(rows)
                    diagnostics.append(f"{name}: {len(rows)} usable results")
                except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as error:
                    groups.append(())
                    diagnostics.append(f"{name}: unavailable ({type(error).__name__})")
        sources, seen = [], set()
        for index in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if index >= len(group): continue
                source = group[index]
                parts = urlsplit(source.url)
                identity = (parts.hostname.casefold(), parts.path.rstrip("/"), parts.query)
                if identity in seen: continue
                seen.add(identity); sources.append(source)
                if len(sources) == 6: break
            if len(sources) == 6: break
        if not sources:
            raise RuntimeError("Live research returned no usable evidence. " + "; ".join(diagnostics))
        return ResearchResult(search_query, tuple(sources), tuple(diagnostics))
