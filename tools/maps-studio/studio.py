"""Prometheus Maps & Studio: local tools and explicitly requested public research.

Standard-library-only. Never executes reviewed code, edits source files, accepts
arbitrary fetch URLs, reads browser-selected images, or stores account credentials.
"""
from __future__ import annotations
import argparse
import ast
import datetime as dt
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
MAX_BODY = 1_000_000
MAX_REMOTE = 4_000_000
ALLOWED_HOSTS = {"images-api.nasa.gov", "cmr.earthdata.nasa.gov", "download.geofabrik.de"}
PUBLIC_DEADLINE = 25


class PublisherResponseError(Exception):
    """A reachable publisher returned an unsupported response shape."""


def object_value(value):
    if not isinstance(value, dict):
        raise PublisherResponseError("Expected a publisher object.")
    return value


def array_value(value):
    if not isinstance(value, list):
        raise PublisherResponseError("Expected a publisher list.")
    return value


def text_value(value, limit=1800):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PublisherResponseError("Expected publisher text.")
    return value[:limit]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class PublisherRedirect(urllib.request.HTTPRedirectHandler):
    max_repeats = 1
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        hops = getattr(req, "_studio_redirect_hops", 0) + 1
        if hops > 3:
            raise ValueError("Publisher exceeded the three-redirect limit.")
        original = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != "https" or target.hostname != original.hostname
                or target.hostname not in ALLOWED_HOSTS or target.port not in (None, 443)
                or target.username or target.password):
            raise ValueError("Publisher redirect left its configured HTTPS origin.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected._studio_redirect_hops = hops
            if req.get_method() == "HEAD":
                redirected.method = "HEAD"
        return redirected


def public_json(url, *, method="GET"):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise ValueError("Only the configured public publishers are supported.")
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": "PrometheusLocalStudio/1.0 (user-initiated public research)",
        "Accept": "application/json", "Accept-Encoding": "identity"})
    # Publisher aliases can redirect to dated files on the same HTTPS origin.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublisherRedirect())
    deadline = time.monotonic() + PUBLIC_DEADLINE
    with opener.open(request, timeout=10) as response:
        headers = dict(response.headers)
        if method == "HEAD":
            return {}, headers
        raw = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Publisher request timed out.")
            chunk = response.read1(min(65536, MAX_REMOTE + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_REMOTE:
                raise PublisherResponseError("Publisher response exceeds the size limit.")
        try:
            return object_value(json.loads(raw)), headers
        except (ValueError, UnicodeError) as error:
            raise PublisherResponseError("Publisher did not return valid JSON.") from error


def query_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200 or any(ord(c) < 32 for c in value):
        raise ValueError("Enter a search of 1–200 printable characters.")
    return value.strip()


def nasa_media(query, media_type="image"):
    query = query_text(query)
    if media_type not in {"image", "video", "audio"}:
        raise ValueError("Choose image, video or audio.")
    url = "https://images-api.nasa.gov/search?" + urllib.parse.urlencode({"q": query, "media_type": media_type, "page_size": 12})
    data, _ = public_json(url)
    items = []
    collection = object_value(object_value(data).get("collection"))
    for entry in array_value(collection.get("items"))[:12]:
        records = array_value(object_value(entry).get("data", []))
        metadata = object_value(records[0]) if records else {}
        nasa_id = text_value(metadata.get("nasa_id", ""), 300)
        items.append({"title": text_value(metadata.get("title", "Untitled"), 500),
            "description": text_value(metadata.get("description", "")),
            "source_date": text_value(metadata.get("date_created")), "publisher": text_value(metadata.get("center", "NASA")),
            "url": "https://images.nasa.gov/details/" + urllib.parse.quote(nasa_id, safe=""),
            "id": nasa_id, "type": media_type})
    return {"source": "NASA Image and Video Library", "source_url": url, "retrieved_utc": now(),
            "results": items, "note": "Source date is the asset date, not verification of every claim. Check the asset's credit and usage terms."}


def earthdata(query):
    query = query_text(query)
    url = "https://cmr.earthdata.nasa.gov/search/collections.json?" + urllib.parse.urlencode({"keyword": query, "page_size": 12})
    data, _ = public_json(url)
    items = []
    feed = object_value(object_value(data).get("feed"))
    for item in array_value(feed.get("entry"))[:12]:
        item = object_value(item)
        ident = text_value(item.get("id", ""), 300)
        items.append({"title": text_value(item.get("title", "Untitled"), 500),
            "description": text_value(item.get("summary", "")), "id": ident,
            "source_date": text_value(item.get("updated")), "publisher": text_value(item.get("data_center", "NASA Earthdata")),
            "url": "https://search.earthdata.nasa.gov/search?" + urllib.parse.urlencode({"p": ident}),
            "type": "dataset"})
    return {"source": "NASA Common Metadata Repository", "source_url": url, "retrieved_utc": now(),
            "results": items, "note": "Searches public collection metadata. Some data downloads require a free Earthdata Login; observation dates vary by dataset."}


def map_regions():
    data, _ = public_json("https://download.geofabrik.de/index-v1-nogeom.json")
    results = []
    for feature in array_value(object_value(data).get("features")):
        props = object_value(object_value(feature).get("properties"))
        url = text_value(object_value(props.get("urls", {})).get("pbf", ""), 2000)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname == "download.geofabrik.de" and parsed.path.endswith("-latest.osm.pbf"):
            results.append({"id": text_value(props.get("id"), 300), "name": text_value(props.get("name"), 500), "url": url})
    return {"regions": sorted(results, key=lambda row: str(row["name"])), "retrieved_utc": now(),
            "source_url": "https://download.geofabrik.de/", "note": "OSM PBF extracts are map data, not ready-to-view offline maps. They require a compatible renderer."}


class Services:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}
        self.last_request = 0.0

    def request(self, action, body):
        # At most one publisher worker. A stalled DNS/socket operation cannot
        # accumulate workers or keep an HTTP client waiting without a bound.
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("A public lookup is still running. Retry shortly.")
        completed = threading.Event()
        outcome = {}

        def run():
            try:
                outcome["result"] = self._request(action, body)
            except Exception as error:
                outcome["error"] = error
            finally:
                self.lock.release()
                completed.set()

        threading.Thread(target=run, daemon=True).start()
        if not completed.wait(PUBLIC_DEADLINE):
            raise TimeoutError("Publisher lookup exceeded its time limit.")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def _request(self, action, body):
        key = json.dumps([action, body], sort_keys=True)
        existing = self.cache.get(key)
        if existing and time.monotonic() - existing[0] < 600:
            return {**existing[1], "cached": True}
        if time.monotonic() - self.last_request < 1:
            raise RuntimeError("Please wait a second before another public request.")
        self.last_request = time.monotonic()
        if action == "nasa":
            result = nasa_media(body.get("query"), body.get("type", "image"))
        elif action == "earthdata":
            result = earthdata(body.get("query"))
        elif action == "regions":
            result = map_regions()
        elif action == "map_freshness":
            catalog = next((v[1] for k, v in self.cache.items() if json.loads(k)[0] == "regions"), None)
            if not catalog:
                raise ValueError("Load the publisher's region list first.")
            region = next((r for r in catalog["regions"] if r["id"] == body.get("region")), None)
            if not region:
                raise ValueError("Choose a region from the publisher list.")
            _, headers = public_json(region["url"], method="HEAD")
            headers = {key.lower(): value for key, value in headers.items()}
            result = {**region, "checked_utc": now(), "file_modified": headers.get("last-modified"),
                "bytes": headers.get("content-length"), "source_url": region["url"],
                "note": "Publisher file-modification time is not the survey date of every road. Download only the regions you need; PBF requires a renderer."}
        else:
            raise ValueError("Unknown public service.")
        if len(self.cache) >= 64:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = (time.monotonic(), result)
        return {**result, "cached": False}


def review_source(name, source):
    """Bounded heuristic triage only; no execution, secret echoing or source storage."""
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        raise ValueError("Supply a filename.")
    if not isinstance(source, str) or len(source) > 200_000 or "\x00" in source:
        raise ValueError("Select a UTF-8 source file up to 200,000 characters.")
    findings = []
    def add(line, rule, message):
        if len(findings) < 100:
            findings.append({"line": line, "rule": rule, "message": message, "severity": "review"})
    suffix = Path(name).suffix.lower()
    if suffix == ".py":
        try:
            tree = ast.parse(source)
            aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for entry in node.names:
                        aliases[entry.asname or entry.name] = entry.name
                if isinstance(node, ast.ImportFrom):
                    for entry in node.names:
                        aliases[entry.asname or entry.name] = (node.module or "") + "." + entry.name
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called = ast.unparse(node.func)
                parts = called.split(".")
                called = aliases.get(parts[0], parts[0]) + ("." + ".".join(parts[1:]) if len(parts) > 1 else "")
                if called in {"eval", "exec", "builtins.eval", "builtins.exec"}:
                    add(node.lineno, "dynamic-execution", "Dynamic code execution: confirm the input cannot be controlled by an untrusted source.")
                if called in {"pickle.load", "pickle.loads", "dill.load", "dill.loads"}:
                    add(node.lineno, "unsafe-deserialization", "Deserialization can execute code. Do not accept untrusted serialized objects.")
                if called in {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output", "subprocess.check_call"}:
                    if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
                        add(node.lineno, "shell-command", "shell=True needs a review of command construction and untrusted inputs.")
                if called.startswith(("requests.", "httpx.")) and any(k.arg == "verify" and isinstance(k.value, ast.Constant) and k.value.value is False for k in node.keywords):
                    add(node.lineno, "tls-disabled", "Certificate verification is disabled; restore TLS verification.")
        except (SyntaxError, RecursionError, MemoryError) as error:
            add(getattr(error, "lineno", 1) or 1, "parse-error", "Python could not be parsed; fix syntax or reduce nesting before reviewing.")
    elif suffix in {".js", ".ts", ".jsx", ".tsx", ".html"}:
        for number, line in enumerate(source.splitlines(), 1):
            if re.search(r"\b(innerHTML|outerHTML)\s*=|\binsertAdjacentHTML\s*\(", line):
                add(number, "html-injection", "HTML insertion: trace the value and sanitize untrusted markup or use textContent.")
            if re.search(r"\beval\s*\(|\bnew\s+Function\s*\(", line):
                add(number, "dynamic-execution", "Dynamic JavaScript execution: inspect input trust and remove unnecessary evaluation.")
    else:
        raise ValueError("Supported review formats: Python, JavaScript, TypeScript, JSX, TSX and HTML.")
    for number, line in enumerate(source.splitlines(), 1):
        if re.search(r"(?i)\b(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}['\"]", line):
            add(number, "possible-secret", "Possible hard-coded credential. Verify locally; if genuine, remove and rotate it. Value deliberately omitted.")
    return {"file": name, "findings": findings, "checked_utc": now(), "truncated": len(findings) == 100,
            "scope": "Heuristic static triage only. No code execution, dependency scan, full data-flow analysis, or assurance of security. Findings require human review."}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    def __init__(self, address):
        self.token = secrets.token_urlsafe(32)
        self.services = Services()
        self.stop_requested = False
        super().__init__(address, Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "PrometheusStudio/1.0"
    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        pass

    def allowed(self, post=False):
        origin = self.server.origin
        if self.headers.get("Host") != origin.removeprefix("http://"):
            return False
        if self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            return False
        if self.headers.get("Origin") not in ((origin,) if post else (None, origin)):
            return False
        if post and not secrets.compare_digest(self.headers.get("X-Prometheus-Token", ""), self.server.token):
            return False
        return True

    def respond(self, status, data, content_type="application/json; charset=utf-8"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-src https://www.openstreetmap.org; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.allowed():
            self.respond(403, {"error": "Use the local Prometheus address."}); return
        assets = {"/": ("index.html", "text/html; charset=utf-8"), "/studio.js": ("studio.js", "text/javascript; charset=utf-8"),
                  "/studio.css": ("studio.css", "text/css; charset=utf-8"), "/catalog.json": ("catalog.json", "application/json; charset=utf-8")}
        if self.path == "/api/bootstrap":
            self.respond(200, {"token": self.server.token, "version": "1.0.1", "network": "Public research, maps and external website links use the network when requested. Local image editing and code checks do not."})
        elif self.path in assets:
            name, kind = assets[self.path]
            self.respond(200, (ROOT / name).read_bytes(), kind)
        else:
            self.respond(404, {"error": "Not found."})

    def do_POST(self):
        if not self.allowed(post=True):
            self.respond(403, {"error": "This action requires the local session."}); return
        if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
            self.respond(415, {"error": "Use JSON with a content length."}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                self.respond(413, {"error": "Request is too large."}); return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            schemas = {"/api/review": {"name", "source"}, "/api/nasa": {"query", "type"},
                       "/api/earthdata": {"query"}, "/api/regions": set(), "/api/map_freshness": {"region"}, "/api/close": set()}
            if self.path not in schemas:
                self.respond(404, {"error": "Unknown action."}); return
            if set(body) - schemas[self.path]:
                raise ValueError("Unknown request fields.")
            if self.path == "/api/review":
                result = review_source(body.get("name"), body.get("source"))
            elif self.path == "/api/close":
                self.server.stop_requested = True
                result = {"closed": True}
            else:
                result = self.server.services.request(self.path.rsplit("/", 1)[1], body)
            self.respond(200, result)
        except PublisherResponseError:
            self.respond(502, {"error": "The publisher returned an unsupported response. Retry later or open its website."})
        except (ValueError, TypeError, RecursionError):
            self.respond(400, {"error": "Invalid input or publisher response. Check your selections and try again."})
        except RuntimeError as error:
            self.respond(429, {"error": str(error)})
        except (OSError, urllib.error.URLError):
            self.respond(502, {"error": "The publisher could not be reached. Local image tools and code checks still work; retry later or open its website."})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    with Server(("127.0.0.1", args.port)) as server:
        print(server.origin, flush=True)
        if args.ready_file:
            # This file contains only a loopback URL, never the session token.
            args.ready_file.write_text(server.origin, encoding="utf-8")
        if not args.no_browser:
            webbrowser.open(server.origin)
        server.timeout = 0.5
        try:
            while not server.stop_requested:
                server.handle_request()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
