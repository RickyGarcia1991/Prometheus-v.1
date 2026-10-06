from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_MODEL = "llama3.2:1b"
MAX_RESPONSE_BYTES = 2_000_000


class LocalModelError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OllamaClient:
    """Local-only Ollama boundary; no proxy, redirects, tools, or cloud fallback."""

    def __init__(self, base_url="http://127.0.0.1:11434", model=DEFAULT_MODEL, timeout=120):
        parsed = urlparse(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("Only local loopback Ollama URLs are allowed.")
        if not parsed.port or not 1 <= parsed.port <= 65535:
            raise ValueError("A valid loopback Ollama port is required.")
        if not model.strip() or "cloud" in model.lower():
            raise ValueError("Cloud model selections are disabled; choose an installed local model.")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise LocalModelError(f"Ollama HTTP error {error.code}; no cloud fallback was used.") from error
        except (URLError, OSError, TimeoutError) as error:
            raise LocalModelError("Cannot reach local Ollama. Open Ollama and retry; no cloud fallback was used.") from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise LocalModelError("Ollama response exceeded the safe size limit.")
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError) as error:
            raise LocalModelError("Ollama returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise LocalModelError("Ollama returned an invalid response object.")
        return result

    def ensure_local_model(self):
        models = self._request("/api/tags").get("models")
        if not isinstance(models, list):
            raise LocalModelError("Ollama returned an invalid local model inventory.")
        selected = next((entry for entry in models if isinstance(entry, dict)
                         and entry.get("name") == self.model), None)
        if selected is None:
            raise LocalModelError(f"Model {self.model} is not installed locally. Download it with Ollama before offline use.")
        if selected.get("remote_host") or selected.get("remote_model") or selected.get("cloud"):
            raise LocalModelError("The selected model is remote, not local. Cloud access is disabled.")
        return selected

    def chat(self, messages):
        result = self._request("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "5m",
            "options": {"num_ctx": 2048, "num_predict": 256, "temperature": 0.2},
        })
        message = result.get("message")
        if (result.get("done") is not True or not isinstance(message, dict)
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise LocalModelError("Ollama did not return a valid completed answer; no turn was saved.")
        if message.get("role", "assistant") != "assistant":
            raise LocalModelError("Ollama returned an unexpected message role.")
        return message["content"].strip(), result.get("eval_count")
