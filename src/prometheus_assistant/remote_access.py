"""Explicit, revocable access to paired computers through Remote Desktop Commander.

The local UI remains loopback-only. OAuth is handled by a pinned stdio bridge;
provider credentials stay in the current Windows profile, never on the SSD.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time

from .hardware import resource_root
from .remote_transport import MCPBridge

ENDPOINT = 'https://mcp.desktopcommander.app/mcp'
RUNTIME_SHA256 = '56ac063b006b9569957a30eee2df510eac006a7cde4625e0eaa7a58775ec55d1'
CLIENT_SHA256 = '29c668867838b2b3b56b45676ce967d42ca8e65a68d8c17a775fecd9a2eacfc3'
MAX_ARGUMENTS = 128_000
DEVICE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
TOOL_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,160}$')
HEALTH_TTL_SECONDS = 30
MAX_HEALTH_TIMESTAMP = 253402300769  # Leave room for the TTL before year 10000.


def plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked remote-access paths are not accepted.')
    return path


def host_root():
    profile = os.environ.get('LOCALAPPDATA')
    if os.name != 'nt' or not profile or not Path(profile).is_absolute():
        raise ValueError('Remote access requires a Windows user profile on this computer.')
    return plain(Path(profile)/'PrometheusRemote')


def verify_runtime(runtime, manifest, *, expected=RUNTIME_SHA256):
    runtime, manifest = plain(runtime), plain(manifest)
    raw = manifest.read_bytes()
    if len(raw) > 16_000_000 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Remote runtime does not match this reviewed release. Run its setup package.')
    entries = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(entries, list) or not 1 <= len(entries) <= 50000:
        raise ValueError('Invalid remote runtime manifest.')
    # Walk each directory once. Rechecking every ancestor for every module made
    # a portable runtime need hundreds of thousands of filesystem calls.
    found = {}
    folders = [runtime]
    directory_count = 0
    while folders:
        directory_count += 1
        if directory_count > 50000:
            raise ValueError('Remote runtime contains too many directories.')
        with os.scandir(folders.pop()) as children:
            for child in children:
                path = Path(child.path)
                if child.is_symlink() or getattr(child, 'is_junction', lambda: False)():
                    raise ValueError('Linked remote-access paths are not accepted.')
                if child.is_dir(follow_symlinks=False):
                    folders.append(path)
                elif child.is_file(follow_symlinks=False):
                    key = path.relative_to(runtime).as_posix().casefold()
                    if key in found or len(found) >= 50000:
                        raise ValueError('Remote runtime contains unexpected files.')
                    found[key] = (path, child.stat(follow_symlinks=False).st_size)
                else:
                    raise ValueError('Remote runtime contains an unsupported entry.')
    seen = set()
    for entry in entries:
        name = entry.get('path', '')
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or name.startswith('/') or any(x in {'', '.', '..'} for x in name.split('/'))):
            raise ValueError('Invalid remote runtime path.')
        key = name.casefold()
        if key in seen:
            raise ValueError('Duplicate remote runtime entry.')
        seen.add(key)
        target, size = found.get(key, (None, None))
        if target is None or size != entry['size_bytes']:
            raise ValueError('Remote runtime is incomplete. Run its setup package.')
        with target.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != entry['sha256']:
            raise ValueError('Remote runtime checksum failed. Run its setup package.')
    # Reject unlisted executable/module files as well as missing listed files.
    if set(found) != seen:
        raise ValueError('Remote runtime contains unlisted files.')
    proxy = runtime/'node_modules/mcp-remote/dist/proxy.js'
    node = runtime/'node.exe'
    if not node.is_file() or not proxy.is_file():
        raise ValueError('Remote connection components are missing.')
    return node, proxy


def launch_bridge():
    local = host_root()
    # The pointer is only a locator. The core's pinned digest supplies trust.
    pointer = local/'active-runtime.json'
    runtime = manifest = None
    if pointer.is_file():
        info = json.loads(pointer.read_text(encoding='utf-8-sig'))
        if info.get('manifest_sha256') == RUNTIME_SHA256:
            candidate = plain(info.get('runtime_path', ''))
            if candidate.parent == local and candidate.name == 'runtime-'+RUNTIME_SHA256[:16]:
                runtime = candidate
                manifest = local/('RUNTIME-MANIFEST-'+RUNTIME_SHA256[:16]+'.json')
    if runtime is None:
        resources = resource_root()
        if not resources:
            raise ValueError('Start Prometheus from its SSD launcher or run remote setup first.')
        base = Path(resources)/'RemoteAccess'
        runtime, manifest = base/'runtime', base/'RUNTIME-MANIFEST.json'
    # The signed core pins a small, self-contained client tree independently.
    # Target-side file/PDF/Excel tools are checked during setup, not every connect.
    node, proxy = verify_runtime(runtime/'client', runtime/'CLIENT-MANIFEST.json', expected=CLIENT_SHA256)
    auth = plain(local/'ClientAuth')
    auth.mkdir(parents=True, exist_ok=True)
    permitted = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'USERPROFILE',
                 'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'PATHEXT'}
    env = {k: v for k, v in os.environ.items() if k.upper() in permitted}
    system = os.environ.get('SYSTEMROOT', r'C:\Windows')
    env['PATH'] = str(node.parent)+os.pathsep+str(Path(system)/'System32')+os.pathsep+system
    env['MCP_REMOTE_CONFIG_DIR'] = str(auth)
    command = [str(node), str(proxy), ENDPOINT, '--transport', 'http-only',
               '--static-oauth-client-metadata', '{"client_name":"Prometheus Remote Access"}']
    return MCPBridge(command, env=env, cwd=local, timeout=30)


def validate_arguments(value, schema, depth=0):
    """Validate provider JSON schemas without evaluating code or external references."""
    if depth > 20 or not isinstance(schema, dict):
        raise ValueError('Remote argument schema exceeds supported limits.')
    if '$ref' in schema:
        raise ValueError('This tool uses an unsupported argument reference.')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Choose one of the tool\'s permitted values.')
    if 'const' in schema and value != schema['const']:
        raise ValueError('A fixed tool argument has changed.')
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            valid = 0
            for branch in schema[key]:
                try:
                    validate_arguments(value, branch, depth+1)
                    valid += 1
                except ValueError:
                    pass
            if not valid or key == 'oneOf' and valid != 1:
                raise ValueError('Tool arguments do not match its schema.')
    for branch in schema.get('allOf', []):
        validate_arguments(value, branch, depth+1)
    kinds = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'boolean': type(value) is bool,
             'integer': type(value) is int,
             'number': type(value) in {int, float} and math.isfinite(value), 'null': value is None}
    expected = schema.get('type')
    if expected is not None and not any(kinds.get(x, False) for x in ([expected] if isinstance(expected, str) else expected)):
        raise ValueError('Tool argument has the wrong type.')
    if isinstance(value, dict):
        if len(value) > 100 or any(not isinstance(k, str) for k in value):
            raise ValueError('Too many remote arguments.')
        props = schema.get('properties', {})
        if any(k not in value for k in schema.get('required', [])):
            raise ValueError('A required remote argument is missing.')
        for key, item in value.items():
            if key in props:
                validate_arguments(item, props[key], depth+1)
            elif schema.get('additionalProperties') is False:
                raise ValueError('Unknown remote argument: '+key[:80])
            elif isinstance(schema.get('additionalProperties'), dict):
                validate_arguments(item, schema['additionalProperties'], depth+1)
    elif isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= min(schema.get('maxItems', 1000), 1000):
            raise ValueError('Remote argument list exceeds its limits.')
        for item in value:
            validate_arguments(item, schema.get('items', {}), depth+1)
    elif isinstance(value, str):
        if '\x00' in value or not schema.get('minLength', 0) <= len(value) <= min(schema.get('maxLength', MAX_ARGUMENTS), MAX_ARGUMENTS):
            raise ValueError('Remote text exceeds its limits.')
    elif type(value) in {int, float}:
        if not math.isfinite(value) or not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
            raise ValueError('Remote number exceeds its limits.')


def parse_devices(result):
    if not isinstance(result, dict) or result.get('isError'):
        raise ValueError('Could not list paired computers.')
    structured = result.get('structuredContent')
    rows = structured.get('devices') if isinstance(structured, dict) else None
    devices = []
    if isinstance(rows, list):
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            identity = row.get('id', row.get('deviceId', ''))
            if not isinstance(identity, str) or not DEVICE_ID.fullmatch(identity):
                continue
            online = row.get('online') is True or str(row.get('status', '')).lower() == 'online'
            devices.append({'id': identity, 'name': str(row.get('name', identity))[:160],
                            'status': 'online' if online else 'offline'})
    else:
        # Official human-readable list. Only explicit ID+status records become targets.
        text = '\n'.join(x.get('text', '') for x in result.get('content', []) if x.get('type') == 'text')[:64000]
        pattern = r'(?m)^\s*\d+\.\s+([^\r\n]+)\r?\n\s*Status:\s*(Online|Offline)\s*\r?\n\s*ID:\s*([A-Za-z0-9_.:-]+)\s*$'
        for match in re.finditer(pattern, text, flags=re.I):
            identity = match[3]
            if DEVICE_ID.fullmatch(identity):
                devices.append({'id': identity, 'name': match[1].strip()[:160], 'status': match[2].lower()})
    ids = [x['id'] for x in devices]
    if len(ids) != len(set(ids)):
        raise ValueError('The provider returned ambiguous computer identities.')
    return devices[:100]


def _device_health_rows(result, devices):
    """Keep legacy dispatch parsing, but require explicit status for health claims."""
    structured = result.get('structuredContent')
    rows = structured.get('devices') if isinstance(structured, dict) else None
    if not isinstance(rows, list):
        # The text parser accepts only explicit ID + Online/Offline records.
        return [{'id': row['id'], 'status': row['status']} for row in devices]
    statuses = {}
    accepted = {row['id'] for row in devices}
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        identity = row.get('id', row.get('deviceId', ''))
        if not isinstance(identity, str) or identity not in accepted:
            continue
        signals = []
        if 'online' in row:
            signals.append(('online' if row['online'] else 'offline')
                           if type(row['online']) is bool else 'unknown')
        if 'status' in row:
            status = row['status'].lower() if isinstance(row['status'], str) else 'unknown'
            signals.append(status if status in {'online', 'offline'} else 'unknown')
        statuses[identity] = signals[0] if signals and len(set(signals)) == 1 else 'unknown'
    return [{'id': row['id'], 'status': statuses.get(row['id'], 'unknown')} for row in devices]


def account_id(result):
    if not isinstance(result, dict) or result.get('isError'):
        raise ValueError('Could not verify the connected account.')
    data = result.get('structuredContent')
    if not isinstance(data, dict) or 'id' not in data:
        content = result.get('content', [])
        texts = [x['text'] for x in content if x.get('type') == 'text' and isinstance(x.get('text'), str)]
        if len(texts) != 1 or len(texts[0]) > 64000:
            raise ValueError('Could not verify the connected account.')
        data = json.loads(texts[0])
    identity = data.get('id') if isinstance(data, dict) else None
    if not isinstance(identity, str) or not DEVICE_ID.fullmatch(identity):
        raise ValueError('Could not verify the connected account.')
    return identity


class RemoteAccess:
    def __init__(self, *, bridge_factory=launch_bridge, audit_root=None, clock=time.time,
                 monotonic=time.monotonic):
        self.factory, self.audit_root, self.clock = bridge_factory, audit_root, clock
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.generation = 0
        self.client = None
        self.phase = 'disconnected'
        self.message = 'Connect your account, then choose a paired computer.'
        self.devices, self.tools = [], []
        self.selected = None
        self.pending = None
        self.account_id = None
        self.result = None
        self.threads = []
        self._health_observation = None
        self._health_valid = False

    def health_snapshot(self):
        """Return only cached, time-qualified counts without probing or expiring reviews.

        This dashboard seam deliberately does not use state(), which can expire
        pending approval. Stale observations retain their time and row count, but
        every device becomes unknown rather than implying it is still online.
        """
        with self.lock:
            phase = self.phase if self.phase in {'disconnected', 'connecting', 'ready', 'working', 'error'} else 'unknown'
            observation = self._health_observation
            if observation and observation['generation'] != self.generation:
                observation = None
            current = False
            if observation and self._health_valid and self.client is not None and phase in {'ready', 'working'}:
                try:
                    wall_age = self.clock() - observation['observed_at']
                    mono_age = self.monotonic() - observation['observed_monotonic']
                    current = (math.isfinite(wall_age) and math.isfinite(mono_age)
                               and 0 <= wall_age < HEALTH_TTL_SECONDS
                               and 0 <= mono_age < HEALTH_TTL_SECONDS)
                except (TypeError, ValueError, OverflowError):
                    current = False
            rows = observation['devices'] if observation else ()
            counts = {'online': 0, 'offline': 0, 'unknown': 0, 'total': len(rows)}
            for _, status in rows:
                counts[status if current else 'unknown'] += 1
            selected_state = 'unselected' if self.selected is None else 'unknown'
            if current and self.selected is not None:
                selected_state = next((status for identity, status in rows if identity == self.selected), 'missing')
            observed_at = observation['observed_at'] if observation else None
            return {'schema_version': 1, 'phase': phase,
                    'generation': min(self.generation, 2**31 - 1),
                    'freshness': 'current' if current else 'stale' if observation else 'unavailable',
                    'observed_at': observed_at,
                    'expires_at': observed_at + HEALTH_TTL_SECONDS if observation else None,
                    'ttl_seconds': HEALTH_TTL_SECONDS, 'counts': counts,
                    'selected_state': selected_state}

    def _record_health(self, generation, devices, *, observed_times=None):
        """Publish an observation under the caller's lock, only for this connection."""
        if generation != self.generation:
            return
        self._health_valid = False
        wall, mono = observed_times if observed_times is not None else (self.clock(), self.monotonic())
        if (type(wall) not in {int, float} or not math.isfinite(wall)
                or not 0 <= wall <= MAX_HEALTH_TIMESTAMP
                or type(mono) not in {int, float} or not math.isfinite(mono) or mono < 0):
            return
        self._health_observation = {'generation': generation, 'observed_at': wall,
            'observed_monotonic': mono,
            'devices': tuple((row['id'], row['status'] if row['status'] in {'online', 'offline'} else 'unknown')
                             for row in devices[:100])}
        self._health_valid = True

    def state(self):
        with self.lock:
            if self.pending and self.pending['expires_at'] <= self.clock():
                self.pending = None
            return copy.deepcopy({'phase': self.phase, 'message': self.message,
                'devices': self.devices, 'tools': self.tools, 'selected_device_id': self.selected,
                'pending': self.pending, 'result': self.result,
                'capabilities': 'Files, terminal commands, processes and diagnostics supplied by the connected provider. Screen/mouse/keyboard tools are not provided.'})

    def _audit(self, kind, **values):
        root = self.audit_root if self.audit_root is not None else host_root()
        path = plain(Path(root)/'remote-actions.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never store OAuth values, argument text, command output or file contents.
        entry = {'time': self.clock(), 'event': kind, **values}
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=True)+'\n')

    def _start(self, function, *, phase, message):
        with self.lock:
            if self.phase in {'connecting', 'working'}:
                raise RuntimeError('Wait for the current connection action to finish, or disconnect.')
            self.phase, self.message = phase, message
            self._health_valid = False
            generation = self.generation
            self.result = None
            thread = threading.Thread(target=function, args=(generation,), daemon=True)
            self.threads = [t for t in self.threads if t.is_alive()]
            self.threads.append(thread)
            thread.start()

    def _finish(self, generation, *, phase='ready', message='', result=None):
        with self.lock:
            if generation != self.generation:
                return
            self.phase, self.message, self.result = phase, message, result
            if phase == 'error':
                self._health_valid = False

    def _catalog(self, client):
        tools, cursor = [], None
        for _ in range(8):
            result = client.request('tools/list', {'cursor': cursor} if cursor else {})
            rows = result.get('tools', [])
            if not isinstance(rows, list):
                raise ValueError('Invalid remote tool catalog.')
            for item in rows:
                if not isinstance(item, dict) or not TOOL_NAME.fullmatch(item.get('name', '')):
                    raise ValueError('Invalid remote tool name.')
                tools.append(item)
                if len(tools) > 250:
                    raise ValueError('Remote tool catalog exceeds its limit.')
            cursor = result.get('nextCursor')
            if not cursor:
                break
        else:
            raise ValueError('Remote tool catalog has too many pages.')
        if len({t['name'] for t in tools}) != len(tools) or not {'list_devices', 'who_am_i'} <= {t['name'] for t in tools}:
            raise ValueError('The remote provider does not expose a valid device list.')
        return tools

    def _devices(self, client, *, with_health=False):
        result = client.request('tools/call', {'name': 'list_devices', 'arguments': {}})
        observed_times = self.clock(), self.monotonic()
        devices = parse_devices(result)
        if with_health:
            return devices, _device_health_rows(result, devices), observed_times
        return devices

    def _connect(self, generation):
        client = None
        try:
            client = self.factory()
            with self.lock:
                if generation != self.generation:
                    client.close(); return
                self.client = client
                self.message = 'Complete account sign-in in your browser. Waiting for the connection…'
            client.initialize(timeout=180)
            catalog = self._catalog(client)
            identity = account_id(client.request('tools/call', {'name': 'who_am_i', 'arguments': {}}))
            devices, health_rows, observed_times = self._devices(client, with_health=True)
            allowed = []
            for tool in catalog:
                schema = tool.get('inputSchema', {})
                if isinstance(schema, dict) and 'deviceId' in schema.get('properties', {}):
                    allowed.append({'name': tool['name'], 'description': str(tool.get('description', ''))[:4000],
                                    'inputSchema': schema})
            with self.lock:
                if generation != self.generation:
                    return
                self.tools, self.devices, self.selected, self.pending = allowed, devices, None, None
                self.account_id = identity
                self.has_result_tool = any(x['name'] == 'get_tool_result' for x in catalog)
                self._record_health(generation, health_rows, observed_times=observed_times)
            self._audit('connected')
            self._finish(generation, message='Account connected. Choose a computer by its name and device ID.')
        except Exception:
            if client:
                client.close()
            self._finish(generation, phase='error', message='Connection did not complete. Check the setup package and finish account sign-in, then reconnect.')

    def _refresh(self, generation):
        try:
            devices, health_rows, observed_times = self._devices(self.client, with_health=True)
            with self.lock:
                if generation != self.generation:
                    return
                self.devices, self.pending = devices, None
                if not any(d['id'] == self.selected and d['status'] == 'online' for d in devices):
                    self.selected = None
                self._record_health(generation, health_rows, observed_times=observed_times)
            self._finish(generation, message='Computer list refreshed. No remote action was run.')
        except Exception:
            self._finish(generation, phase='error', message='Could not refresh computers. Reconnect before running another action.')

    def _selected_device(self):
        if self.phase != 'ready' or self.client is None:
            raise ValueError('Connect to the provider before choosing a remote action.')
        found = [d for d in self.devices if d['id'] == self.selected and d['status'] == 'online']
        if len(found) != 1:
            raise ValueError('Choose an online computer explicitly.')
        return found[0]

    def prepare(self, tool_name, arguments):
        with self.lock:
            device = self._selected_device()
            spec = next((t for t in self.tools if t['name'] == tool_name), None)
            if spec is None or not isinstance(arguments, dict):
                raise ValueError('Choose a tool advertised by this connection.')
            if 'deviceId' in arguments and arguments['deviceId'] != device['id']:
                raise ValueError('Arguments name a different computer. Select it explicitly first.')
            payload = copy.deepcopy(arguments)
            payload['deviceId'] = device['id']
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(raw.encode('utf-8')) > MAX_ARGUMENTS:
                raise ValueError('Remote arguments exceed the size limit.')
            validate_arguments(payload, spec['inputSchema'])
            self.pending = {'id': secrets.token_hex(24), 'device_id': device['id'], 'device_name': device['name'],
                'tool': tool_name, 'arguments': payload, 'expires_at': self.clock()+300}
            self.pending_generation = self.generation
            self.pending_account_id = self.account_id
            self.message = 'Review the exact computer and action, then choose Run reviewed action.'
            self.result = None

    def _execute(self, generation, pending):
        from .application_policy import require_dispatch
        dispatched = False
        try:
            client = self.client
            # Read-only online check immediately before dispatch; never silently retarget.
            devices, health_rows, observed_times = self._devices(client, with_health=True)
            identity = account_id(client.request('tools/call', {'name': 'who_am_i', 'arguments': {}}))
            if identity != self.pending_account_id:
                raise ValueError('The connected account changed. Reconnect and review again.')
            if not any(d['id'] == pending['device_id'] and d['status'] == 'online' for d in devices):
                raise ValueError('Selected computer is no longer online.')
            digest = hashlib.sha256(json.dumps(pending['arguments'], sort_keys=True).encode()).hexdigest()
            self._audit('dispatch', device_id=pending['device_id'], tool=pending['tool'], arguments_sha256=digest)
            with self.lock:
                if generation != self.generation:
                    return
                self._record_health(generation, health_rows, observed_times=observed_times)
                if pending['expires_at'] <= self.clock():
                    raise ValueError('Reviewed action expired before dispatch.')
                require_dispatch('remote.dispatch', scope={'account': self.pending_account_id,
                    'device': pending['device_id'], 'tool': pending['tool'], 'arguments_sha256': digest})
            dispatched = True
            result = client.request('tools/call', {'name': pending['tool'], 'arguments': pending['arguments']}, timeout=60)
            # Only a structured control envelope identifies an owned background call.
            envelope = result.get('structuredContent', {}) if isinstance(result, dict) else {}
            deadline = time.monotonic()+600
            owned_call_id = None
            while isinstance(envelope, dict) and envelope.get('status') in {'pending', 'running', 'executing'}:
                call_id = envelope.get('call_id')
                if not self.has_result_tool or not isinstance(call_id, str) or not 1 <= len(call_id) <= 200:
                    raise RuntimeError('Background action cannot be observed by this connection.')
                if owned_call_id is None:
                    owned_call_id = call_id
                if call_id != owned_call_id or any(envelope.get(key, pending['device_id']) != pending['device_id'] for key in ('deviceId', 'device_id')):
                    raise RuntimeError('Background action identity changed.')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Background action is still running.')
                with self.lock:
                    if generation != self.generation:
                        return
                time.sleep(1)
                result = client.request('tools/call', {'name': 'get_tool_result', 'arguments': {'call_id': owned_call_id}}, timeout=30)
                envelope = result.get('structuredContent', {}) if isinstance(result, dict) else {}
            self._audit('response', device_id=pending['device_id'], tool=pending['tool'],
                        provider_error=bool(result.get('isError')) if isinstance(result, dict) else False)
            self._finish(generation, message='Remote tool returned a result. Review its output for completion or errors.', result=result)
        except Exception:
            message = ('The outcome is unconfirmed; work may still be running on the selected computer. '
                       'Inspect its state before trying again. This action was not retried.' if dispatched else
                       'The selected computer or connection could not be verified. The reviewed action was not sent.')
            self._finish(generation, phase='error', message=message)

    def action(self, body):
        from .application_policy import require_dispatch
        require_dispatch('remote.action')
        if not isinstance(body, dict):
            raise ValueError('Invalid remote action.')
        action = body.get('action')
        fields = {'connect': {'action'}, 'disconnect': {'action'}, 'refresh': {'action'},
                  'select': {'action', 'device_id'}, 'prepare': {'action', 'tool', 'arguments'},
                  'execute': {'action', 'pending_id'}, 'cancel': {'action'}}
        if action not in fields or set(body) != fields[action]:
            raise ValueError('Invalid remote action fields.')
        if action == 'disconnect':
            self.close()
        elif action == 'connect':
            self.close()
            self._start(self._connect, phase='connecting', message='Checking the bundled remote connection files…')
        elif action == 'refresh':
            with self.lock:
                if self.phase != 'ready':
                    raise ValueError('Connect before refreshing computers.')
                self.pending = None
            self._start(self._refresh, phase='working', message='Refreshing paired computers…')
        elif action == 'select':
            with self.lock:
                if self.phase != 'ready' or not any(d['id'] == body['device_id'] and d['status'] == 'online' for d in self.devices):
                    raise ValueError('Choose an online computer from this connection.')
                self.selected, self.pending, self.result = body['device_id'], None, None
        elif action == 'prepare':
            self.prepare(body['tool'], body['arguments'])
        elif action == 'cancel':
            with self.lock:
                self.pending = None
        elif action == 'execute':
            with self.lock:
                self._selected_device()
                pending = self.pending
                if (not pending or not isinstance(body['pending_id'], str)
                        or not secrets.compare_digest(pending['id'], body['pending_id'])
                        or pending['device_id'] != self.selected or self.pending_generation != self.generation
                        or self.pending_account_id != self.account_id
                        or pending['expires_at'] <= self.clock()):
                    raise ValueError('This review expired or changed. Prepare the action again.')
                self.pending = None
                self._start(lambda generation: self._execute(generation, pending), phase='working',
                            message='Running the reviewed action on '+pending['device_name']+'…')
        return self.state()

    def close(self):
        with self.lock:
            was_working = self.phase == 'working'
            self.generation += 1
            client, self.client = self.client, None
            self.phase = 'disconnected'
            self.pending, self.selected = None, None
            self.result = None
            self.account_id = None
            self.devices, self.tools = [], []
            self._health_observation = None
            self._health_valid = False
            self.message = ('Disconnected. Work already started on the other computer may continue.' if was_working else
                            'Disconnected. Revoke account access in the provider dashboard when needed.')
        if client:
            client.close()
