"""Bounded stdio MCP transport for an explicitly started, owned bridge process.

The caller supplies the exact command and a sanitized environment. This module
does not discover executables, log protocol data, open URLs, or retry requests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


MAX_MESSAGE_BYTES = 12_000_000
MAX_PENDING_REQUESTS = 16
MAX_QUEUED_BYTES = 24_000_000


class MCPBridgeError(RuntimeError):
    """A bridge or protocol failure; messages never contain remote payloads."""


class MCPBridgeTimeout(MCPBridgeError):
    """The outcome may be unknown. The original request must not be retried."""


@dataclass
class _Pending:
    ready: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Exception | None = None


def _duration(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError('Use a positive finite MCP timeout.')
    return float(value)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON member.')
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError('Non-finite JSON number.')


class MCPBridge:
    """One JSONL connection, bounded concurrent requests, and no automatic retry."""

    def __init__(self, command: list[str], *, env: dict, cwd: Path, timeout=30):
        self.timeout = _duration(timeout)
        if (not isinstance(command, list) or not command or len(command) > 64
                or any(not isinstance(part, str) or not part or '\0' in part for part in command)):
            raise ValueError('Provide an explicit MCP bridge argument list.')
        if (not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                or not k or '=' in k or '\0' in k or '\0' in v for k, v in env.items())):
            raise ValueError('Provide a valid explicit MCP bridge environment.')
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError('The MCP bridge working directory must exist.')
        self._lock = threading.Lock()
        self._initialize_lock = threading.Lock()
        self._initial_result = None
        self._pending: dict[int, _Pending] = {}
        self._next_id = 0
        self._stopped = False
        self._outgoing = queue.Queue(maxsize=32)
        self._queued_bytes = 0
        options = {'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE,
                   'stderr': subprocess.DEVNULL, 'shell': False, 'cwd': str(directory),
                   'env': dict(env), 'bufsize': 0}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        try:
            self._process = subprocess.Popen(list(command), **options)
        except (OSError, ValueError):
            raise MCPBridgeError('The local MCP bridge could not start.') from None
        self._stdout = io.BufferedReader(self._process.stdout, buffer_size=65536)
        self._reader = threading.Thread(target=self._read, name='prometheus-mcp-reader', daemon=True)
        self._writer = threading.Thread(target=self._write, name='prometheus-mcp-writer', daemon=True)
        self._reader.start()
        self._writer.start()

    @property
    def alive(self):
        with self._lock:
            return not self._stopped and self._process.poll() is None

    def initialize(self, timeout=180):
        timeout = _duration(timeout)
        with self._initialize_lock:
            if self._initial_result is not None:
                if not self.alive:
                    raise MCPBridgeError('The local MCP bridge is disconnected.')
                return self._initial_result
            result = self.request('initialize', {
                'protocolVersion': '2025-11-25',
                'clientInfo': {'name': 'Prometheus', 'version': '0.8.0.dev2'},
                'capabilities': {},
            }, timeout=timeout)
            if not isinstance(result, dict) or not isinstance(result.get('protocolVersion'), str):
                self._stop(MCPBridgeError('The MCP initialization response is invalid.'))
                raise MCPBridgeError('The MCP initialization response is invalid.')
            self._enqueue({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            self._initial_result = result
            return result

    def request(self, method, params, timeout=None):
        duration = self.timeout if timeout is None else _duration(timeout)
        if not isinstance(method, str) or not method or len(method) > 256 or '\0' in method:
            raise ValueError('Use a bounded MCP method name.')
        if not isinstance(params, (dict, list)):
            raise ValueError('MCP parameters must be an object or array.')
        deadline = time.monotonic() + duration
        pending = _Pending()
        with self._lock:
            if self._stopped or self._process.poll() is not None:
                raise MCPBridgeError('The local MCP bridge is disconnected.')
            if len(self._pending) >= MAX_PENDING_REQUESTS:
                raise MCPBridgeError('Too many MCP requests are pending.')
            self._next_id += 1
            identity = self._next_id
            self._pending[identity] = pending
        try:
            self._enqueue({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params}, deadline=deadline)
            if not pending.ready.wait(max(0, deadline - time.monotonic())):
                error = MCPBridgeTimeout('The MCP request timed out; its outcome may be unknown. It was not retried.')
                self._stop(error)
                raise error
            if pending.error is not None:
                raise pending.error
            return pending.result
        finally:
            with self._lock:
                self._pending.pop(identity, None)

    def _enqueue(self, message, *, deadline=None):
        try:
            data = (json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('MCP arguments must be finite, serializable JSON.') from None
        if len(data) > MAX_MESSAGE_BYTES:
            raise ValueError('The MCP request exceeds its size limit.')
        if deadline is not None and time.monotonic() >= deadline:
            error = MCPBridgeTimeout('The MCP request deadline expired before dispatch. It was not retried.')
            self._stop(error)
            raise error
        with self._lock:
            if self._stopped:
                raise MCPBridgeError('The local MCP bridge is disconnected.')
            if self._queued_bytes + len(data) > MAX_QUEUED_BYTES:
                raise MCPBridgeError('The MCP outgoing queue is full.')
            try:
                self._outgoing.put_nowait((data, deadline))
            except queue.Full:
                raise MCPBridgeError('The MCP outgoing queue is full.') from None
            self._queued_bytes += len(data)

    def _write(self):
        try:
            while True:
                packet = self._outgoing.get()
                if packet is None:
                    return
                data, deadline = packet
                if deadline is not None and time.monotonic() >= deadline:
                    self._stop(MCPBridgeTimeout('The queued MCP request expired before dispatch. It was not retried.'))
                    return
                # Raw pipe writes can be partial. All writes share this one
                # worker; a waiting request's deadline can close the process.
                view = memoryview(data)
                while view:
                    count = self._process.stdin.write(view)
                    if not count:
                        raise OSError('Closed bridge input.')
                    view = view[count:]
                with self._lock:
                    self._queued_bytes -= len(data)
                    if self._stopped:
                        return
        except (OSError, ValueError):
            self._stop(MCPBridgeError('The local MCP bridge stopped accepting requests.'))
        finally:
            try:
                self._process.stdin.close()
            except OSError:
                pass

    def _read(self):
        try:
            while True:
                data = self._stdout.readline(MAX_MESSAGE_BYTES + 1)
                if not data:
                    raise ValueError('Bridge output ended.')
                if len(data) > MAX_MESSAGE_BYTES:
                    raise ValueError('Oversized MCP response.')
                message = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                                     parse_constant=_invalid_constant)
                self._dispatch(message)
        except (OSError, ValueError, TypeError, RecursionError, MCPBridgeError):
            self._stop(MCPBridgeError('The local MCP bridge returned invalid output or disconnected.'))
        finally:
            try:
                self._stdout.close()
            except OSError:
                pass

    def _dispatch(self, message):
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise ValueError('Invalid JSON-RPC message.')
        if 'method' in message:
            if ('result' in message or 'error' in message or not isinstance(message['method'], str)
                    or not message['method'] or len(message['method']) > 256
                    or 'params' in message and not isinstance(message['params'], (dict, list))):
                raise ValueError('Invalid server message.')
            if 'id' in message:
                identity = message['id']
                if (type(identity) is not int and not isinstance(identity, str)) or isinstance(identity, str) and len(identity) > 256:
                    raise ValueError('Invalid server request ID.')
                self._enqueue({'jsonrpc': '2.0', 'id': identity,
                               'error': {'code': -32601, 'message': 'Method not found'}})
            return  # Server notifications are never executed as instructions.
        identity = message.get('id')
        if type(identity) is not int or ('result' in message) == ('error' in message):
            raise ValueError('Invalid response envelope.')
        error = None
        if 'error' in message:
            remote = message['error']
            if (not isinstance(remote, dict) or type(remote.get('code')) is not int
                    or not isinstance(remote.get('message'), str)):
                raise ValueError('Invalid remote error envelope.')
            error = MCPBridgeError(f'The remote MCP request failed (code {remote["code"]}).')
        with self._lock:
            pending = self._pending.get(identity)
            if pending is None or pending.ready.is_set():
                raise ValueError('Unexpected or duplicate response ID.')
            pending.result = message.get('result')
            pending.error = error
            pending.ready.set()

    def _stop(self, error):
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            for pending in self._pending.values():
                if not pending.ready.is_set():
                    pending.error = error
                    pending.ready.set()
            # Release queued payloads promptly. A blocked pipe write is stopped
            # by terminating this exact owned process, outside the state lock.
            while True:
                try:
                    self._outgoing.get_nowait()
                except queue.Empty:
                    break
            self._outgoing.put_nowait(None)
        try:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=.5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def close(self):
        self._stop(MCPBridgeError('The local MCP bridge was disconnected.'))
