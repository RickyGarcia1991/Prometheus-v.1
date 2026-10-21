from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_MODEL = "llama3.2:1b"
MAX_RESPONSE_BYTES = 2_000_000


def output_token_budget(model, num_predict=384):
    """The actual output allowance, shared with input-context budgeting."""
    return max(num_predict, 2048) if model.split(':', 1)[0] == 'gpt-oss' else num_predict


def _idle_residency():
    from .hardware import detect_hardware
    hardware = detect_hardware()
    free = hardware.available_ram_gib
    if hardware.ram_gib < 12 or free is None or free < 1:
        return '30s'
    # Loading from portable storage can dominate reply latency. Allow a natural
    # conversation pause without keeping a model resident indefinitely.
    return '2m' if free < 2 else '15m'


class LocalModelError(RuntimeError):
    pass


class GenerationCancelled(LocalModelError):
    pass


class ModelDeferred(LocalModelError):
    """Admission/ownership deferral, never evidence of poor model quality."""
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _CancellableSocket:
    """Socket adapter with cooperative I/O on the request's owning thread.

    Cross-thread shutdown does not reliably wake Windows buffered socket reads.
    Polling a nonblocking socket lets both HTTP headers and chunked/NDJSON bodies
    honor cancellation without leaving an inference request on a background thread.
    """
    def __init__(self, sock, check):
        self.sock = sock
        self.check = check
        sock.setblocking(False)

    def wait(self, *, writing=False):
        import select
        while True:
            self.check()
            readable, writable, _ = select.select([] if writing else [self.sock],
                                                  [self.sock] if writing else [], [], .1)
            self.check()
            if readable or writable:
                return

    def sendall(self, data):
        remaining = memoryview(data)
        while remaining:
            self.wait(writing=True)
            try:
                sent = self.sock.send(remaining[:65536])
            except BlockingIOError:
                continue
            if sent == 0:
                raise ConnectionError('Local generation connection closed while sending.')
            remaining = remaining[sent:]

    def makefile(self, mode):
        import io
        if mode != 'rb':
            raise ValueError('Local stream reader requires binary read mode.')
        adapter = self
        # A real socket file retains the socket when HTTPConnection.close()
        # transfers ownership to a close-delimited HTTPResponse (HTTP/1.0).
        socket_file = self.sock.makefile('rb', buffering=0)
        class Reader(io.RawIOBase):
            def readable(self):
                return True
            def readinto(self, buffer):
                while True:
                    adapter.wait()
                    count = socket_file.readinto(buffer)
                    if count is not None:
                        return count
            def close(self):
                try:
                    socket_file.close()
                finally:
                    super().close()
        return io.BufferedReader(Reader())

    def close(self):
        self.sock.close()


class OllamaClient:
    """Local-only Ollama boundary; no proxy, redirects, tools, or cloud fallback."""

    def __init__(self, base_url="http://127.0.0.1:11434", model=DEFAULT_MODEL, timeout=120, num_ctx=2048, num_thread=None):
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
        if not isinstance(num_ctx, int) or not 512 <= num_ctx <= 32768:
            raise ValueError("Context size must be an integer from 512 to 32768.")
        self.timeout = timeout
        self.num_ctx = num_ctx
        if num_thread is not None and (type(num_thread) is not int or not 1 <= num_thread <= 16):
            raise ValueError("CPU thread limit must be an integer from 1 to 16.")
        self.num_thread = num_thread
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.last_metrics = {}
        self.last_lifecycle = {}

    def chat_stream(self, messages, *, on_token=None, cancel_event=None, num_predict=384, json_format=False):
        return self._chat_stream(messages, on_token=on_token, cancel_event=cancel_event,
                                 num_predict=num_predict, json_format=json_format)

    def prepare(self, *, cancel_event=None):
        """Load the selected options without a user message or generated answer."""
        self._chat_stream([], cancel_event=cancel_event, preparing=True)
        return dict(self.last_metrics)

    def _chat_stream(self, messages, *, on_token=None, cancel_event=None, num_predict=384,
                     json_format=False, preparing=False):
        from .model_lifecycle import governor, LifecycleError, LifecycleCancelled
        from .application_policy import require_dispatch
        require_dispatch('ollama.stream')
        self.last_metrics = {}
        entered = False
        try:
            with governor.lease(self.base_url, self.model, self.num_ctx, cancel_event=cancel_event) as lease:
                entered = True
                try:
                    result = self._chat_stream_owned(messages, on_token=on_token, cancel_event=cancel_event,
                        num_predict=num_predict, json_format=json_format, preparing=preparing, lease=lease)
                    lease.check()
                    return result
                except GenerationCancelled as error:
                    raise LifecycleCancelled(str(error)) from error
        except LifecycleCancelled as error:
            if entered:
                self.release_model()
            raise GenerationCancelled(str(error)) from error
        except LifecycleError as error:
            if entered:
                self.release_model()
            raise ModelDeferred(str(error)) from error
        except LocalModelError:
            if entered:
                self.release_model()
            raise

    def release_model(self):
        """Bounded cleanup of this client's model; report absence, never assume it."""
        import time
        started = time.monotonic()
        cleanup = OllamaClient(self.base_url, self.model, timeout=.25, num_ctx=self.num_ctx)
        try:
            response = cleanup._request('/api/generate', {'model': self.model, 'keep_alive': 0})
            if response.get('done') is not True:
                raise LocalModelError('Runtime did not confirm model release.')
            rows = cleanup._request('/api/ps').get('models')
            if not isinstance(rows, list):
                raise LocalModelError('Runtime residency was unavailable.')
            remaining = [row for row in rows if isinstance(row, dict) and row.get('name', row.get('model')) == self.model]
            self.last_lifecycle = dict(state='released' if not remaining else 'resident',
                                       model=self.model, seconds=time.monotonic()-started)
        except Exception:
            # Best-effort cleanup must never replace the original cancellation
            # or failure. Its unconfirmed outcome stays explicit.
            self.last_lifecycle = dict(state='unconfirmed', model=self.model,
                                       seconds=time.monotonic()-started)
        return dict(self.last_lifecycle)

    def _chat_stream_owned(self, messages, *, on_token=None, cancel_event=None, num_predict=384,
                           json_format=False, preparing=False, lease):
        """Bounded NDJSON streaming; disconnect the owned request on cancellation."""
        import http.client
        import socket
        import time
        # A reused client must not attribute an earlier request's measurements
        # to a cancellation, disconnect or malformed response in this attempt.
        self.last_metrics = {}
        endpoint = urlparse(self.base_url)
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=min(self.timeout, 1))
        started = time.monotonic()
        first = None
        owned_socket = response = None
        def check():
            lease.check()
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled('Request cancelled; partial text was not saved.')
            if time.monotonic()-started > self.timeout:
                raise LocalModelError('Local generation exceeded its time budget.')
        payload = {'model': self.model, 'messages': messages, 'stream': True, 'keep_alive': lease.keep_alive(default_seconds=_idle_residency()),
                   'options': {'num_ctx': self.num_ctx, 'num_predict': output_token_budget(self.model, num_predict), 'temperature': .2,
                               'num_thread': self.num_thread or 2, 'num_batch': 32 if self.num_ctx <= 2048 else 128}}
        if json_format:
            payload['format'] = json_format if isinstance(json_format, dict) else 'json'
        family = self.model.split(':')[0]
        if family in {'qwen3', 'qwen3.5'}:
            payload['think'] = False
        if family == 'gpt-oss':
            payload['think'] = 'low'
        total, parts, final = 0, [], None
        try:
            check()
            connection.connect()
            owned_socket = connection.sock
            connection.sock = _CancellableSocket(owned_socket, check)
            connection.request('POST', '/api/chat', json.dumps(payload).encode('utf-8'), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise LocalModelError(f'Local model returned HTTP {response.status}.')
            while True:
                check()
                raw = response.readline(65537)
                if not raw:
                    break
                total += len(raw)
                if len(raw) > 65536 or total > MAX_RESPONSE_BYTES:
                    raise LocalModelError('Local stream exceeded its size limit.')
                event = json.loads(raw)
                if not isinstance(event, dict) or event.get('error'):
                    raise LocalModelError('The local model could not complete this request.')
                message = event.get('message', {})
                if not isinstance(message, dict):
                    raise LocalModelError('Invalid local stream message.')
                piece = message.get('content', '')
                if not isinstance(piece, str) or message.get('role', 'assistant') != 'assistant':
                    raise LocalModelError('Invalid local stream message.')
                if piece:
                    if first is None:
                        first = time.monotonic()-started
                    parts.append(piece)
                    if on_token:
                        on_token(piece)
                if event.get('done') is True:
                    final = event
                    break
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled('Request cancelled; partial text was not saved.')
            reply = ''.join(parts).strip()
            if final is None:
                raise LocalModelError('The local stream ended without a complete answer.')
            metrics = {'first_token_seconds': first, 'seconds': time.monotonic()-started,
                       'model': self.model, 'context': self.num_ctx}
            if 'load_duration' in final:
                load = final['load_duration']
                if type(load) not in (int, float) or not 0 <= load < 1e15:
                    raise LocalModelError('Invalid local generation timing.')
                metrics['load_seconds'] = load/1e9
            for name in ('prompt_eval_duration', 'eval_duration'):
                value = final.get(name)
                if type(value) in (int,float) and 0 <= value < 1e15:
                    metrics[name.replace('_duration', '_seconds')] = value/1e9
            for name in ('prompt_eval_count', 'eval_count'):
                value = final.get(name)
                if type(value) is int and 0 <= value < 1_000_000_000:
                    metrics[name] = value
            # Valid final measurements remain useful when generation exhausted
            # its budget. Recording timings never accepts or saves partial text.
            self.last_metrics = metrics
            if final.get('done_reason') == 'length':
                raise LocalModelError('The answer reached its output limit; partial text was not saved. Try a narrower question.')
            if preparing:
                if reply or final.get('eval_count', 0) not in (None, 0):
                    raise LocalModelError('Voice preparation unexpectedly generated text; readiness was not accepted.')
                return '', 0
            if not reply:
                raise LocalModelError('The local stream ended without a complete answer.')
            return reply, final.get('eval_count')
        except (OSError, ValueError, http.client.HTTPException) as error:
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled('Request cancelled; partial text was not saved.') from error
            raise LocalModelError('Local generation was interrupted: '+str(error)[:200]) from error
        finally:
            if owned_socket is not None:
                try:
                    owned_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if response is not None:
                response.close()
            connection.close()

    def _request(self, path, payload=None):
        from .model_lifecycle import governor, LifecycleError, LifecycleCancelled
        from .application_policy import require_dispatch
        require_dispatch('ollama.request')
        inference = path in {'/api/chat', '/api/generate', '/api/embed', '/api/embeddings'} and payload is not None
        if not inference:
            return self._request_owned(path, payload)
        if not isinstance(payload, dict) or payload.get('model') != self.model:
            raise ValueError('Inference payload must use the selected local model.')
        options = payload.get('options', {})
        if not isinstance(options, dict):
            raise ValueError('Invalid model request options.')
        context = options.get('num_ctx', self.num_ctx)
        if type(context) is not int or not 512 <= context <= 32768:
            raise ValueError('Invalid effective model context.')
        # Cleanup is serialized as well: it must never unload another active request.
        cleanup = (path == '/api/generate' and payload.get('keep_alive') == 0
                   and not payload.get('prompt') and not payload.get('messages')
                   and not payload.get('images'))
        entered = False
        try:
            with governor.lease(self.base_url, self.model, context, maintenance=cleanup) as lease:
                entered = True
                body = dict(payload)
                body['keep_alive'] = 0 if cleanup else lease.keep_alive(default_seconds=body.get('keep_alive', '2m'))
                lease.check()
                result = self._request_owned(path, body)
                # Blocking requests cannot be interrupted mid-read; discard their result
                # if the task was cancelled or its admission changed before return.
                lease.check()
                return result
        except LifecycleCancelled as error:
            if entered and not cleanup:
                self.release_model()
            raise GenerationCancelled(str(error)) from error
        except LifecycleError as error:
            if entered and not cleanup:
                self.release_model()
            raise ModelDeferred(str(error)) from error
        except LocalModelError:
            if entered and not cleanup:
                self.release_model()
            raise

    def _request_owned(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise LocalModelError(f"Ollama HTTP error {error.code}; no cloud fallback was used.") from error
        except TimeoutError as error:
            raise LocalModelError("Local model request exceeded its time budget; no completed answer was returned.") from error
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise LocalModelError("Local model request exceeded its time budget; no completed answer was returned.") from error
            raise LocalModelError("Cannot reach local Ollama. Open Ollama and retry; no cloud fallback was used.") from error
        except OSError as error:
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

    def local_models(self):
        models = self._request("/api/tags").get("models")
        if not isinstance(models, list):
            raise LocalModelError("Ollama returned an invalid local model inventory.")
        return models

    def ensure_local_model(self):
        models = self.local_models()
        selected = next((entry for entry in models if isinstance(entry, dict)
                         and entry.get("name") == self.model), None)
        if selected is None:
            raise LocalModelError(f"Model {self.model} is not installed locally. Download it with Ollama before offline use.")
        if selected.get("remote_host") or selected.get("remote_model") or selected.get("cloud"):
            raise LocalModelError("The selected model is remote, not local. Cloud access is disabled.")
        return selected

    def resident_memory_gib(self):
        try:
            models = self._request("/api/ps").get("models", [])
            # One model may be reused/replaced, not the sum of concurrent requests.
            return max((max(0, float(m.get("size", 0)) - float(m.get("size_vram", 0)))
                        for m in models if isinstance(m, dict)), default=0) / 1024**3
        except (LocalModelError, TypeError, ValueError, OverflowError):
            return 0

    def chat(self, messages, *, json_format=False, num_predict=256):
        # Release an idle model sooner on small or busy hosts. This changes
        # residency after a request, never interrupts an in-flight response.
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": _idle_residency(),
            "options": {"num_ctx": self.num_ctx, "num_predict": output_token_budget(self.model, num_predict), "temperature": 0.2},
        }
        if json_format:
            payload["format"] = json_format if isinstance(json_format, dict) else "json"
        if self.model.split(":", 1)[0] == "gpt-oss":
            # GPT-OSS cannot disable reasoning. Leave room for a final answer.
            payload["think"] = "low"
        elif self.model.split(":",1)[0] in {"qwen3", "qwen3.5"}:
            payload["think"] = False
        if self.num_thread is not None:
            payload["options"]["num_thread"] = self.num_thread
        result = self._request("/api/chat", payload)
        if result.get("done_reason") == "length":
            raise LocalModelError("The local model reached its output limit; the incomplete answer was not saved. Try a narrower question.")
        message = result.get("message")
        if (result.get("done") is not True or not isinstance(message, dict)
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise LocalModelError("Ollama did not return a valid completed answer; no turn was saved.")
        if message.get("role", "assistant") != "assistant":
            raise LocalModelError("Ollama returned an unexpected message role.")
        return message["content"].strip(), result.get("eval_count")
