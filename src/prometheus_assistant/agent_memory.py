"""Pinned AgentMemory standalone MCP, called after the owner's task grant check.

This adapter does not decide consent, read Prometheus's existing memories, install
hooks, or contact an AgentMemory server. Its reduced local mode uses keyword
matching and one separate JSON store per project. Each call owns a short-lived
process and a private transaction; upstream's best-effort persistence is never
treated as proof that a write succeeded.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

from .application_policy import require_dispatch
from .remote_transport import MCPBridge, MCPBridgeError

VERSION = '0.9.30'
BUNDLE = Path('Integrations/AgentMemory/0.9.30')
OPERATIONS = ('remember', 'recall', 'forget')
MAX_STORE_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
CALL_TIMEOUT = 20
# These are exact files from the npm registry tarballs, not a mutable local
# manifest. Preserve the Apache-2.0 and ISC licenses beside the runtime.
PINS = {
    'package.json': '61b73591700c7b02ffbf56632b567b937a15b7e52e928dd9c1ebd7bbcf97b01d',
    'LICENSE': '76c8d49ab42216a2533f603fbafa20a1bf71b56de136a401be52da034dcc012c',
    'dist/standalone.mjs': '1f87ef245cf99ac72822adae0b262524050bd9a2cba62d70880a51b20aef4262',
    'node_modules/picocolors/package.json': 'ef2ac226c4811d312e12c64214c453878653e834482125ae475f27cea60de737',
    'node_modules/picocolors/picocolors.js': '213bb870fcaad4def0215fe34fbb0f529836cc4d2462e02f14f1a49d09781625',
    'node_modules/picocolors/LICENSE': '6582629e2979466878f6014313dcc2f3756c9616148682227ce3063dde310750',
}
NODE_RELATIVE = Path('Node/node-v24.21.0-win-x64/node.exe')
NODE_PINS = frozenset({'ba4e6d110e8c1592a1ecd390f6b05f3da124b13871a5be62b341a07a853c6c32'})
_PROJECT = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
_MEMORY_ID = re.compile(r'mem_[a-zA-Z0-9_-]{1,90}\Z')
_LOCAL_ONLY = '''// This fixed preload prevents this pinned upstream entry's HTTP autodiscovery.
// Node permissions additionally deny child processes, workers and native addons.
// This is a narrow runtime guard, not an OS security sandbox.
Object.defineProperty(globalThis, "fetch", {
  value: async () => { throw new Error("Prometheus local memory: network disabled"); },
  writable: false, configurable: false
});
'''


class AgentMemoryError(ValueError):
    """Bounded error safe to show without leaking process output or note content."""


def _checked_path(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise AgentMemoryError('AgentMemory paths must not contain links or reparse points.')
    return path


def _hash(path, max_bytes=1024 * 1024):
    path = _checked_path(path)
    if path.stat().st_size > max_bytes:
        raise AgentMemoryError('An AgentMemory runtime file exceeds its reviewed size bound.')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _runtime(resource_root):
    if not resource_root:
        raise AgentMemoryError('The portable AgentMemory resource folder is not configured.')
    bundle = _checked_path(Path(resource_root) / BUNDLE)
    for relative, expected in PINS.items():
        if _hash(bundle / relative) != expected:
            raise AgentMemoryError('The AgentMemory package differs from its reviewed runtime pins.')
    node = _checked_path(Path(resource_root) / NODE_RELATIVE)
    if _hash(node, 128 * 1024 * 1024) not in NODE_PINS:
        raise AgentMemoryError('The AgentMemory Node runtime differs from its reviewed pin.')
    return bundle, node


def status(resource_root=None):
    """Inspect executable availability only; never opens a memory store."""
    row = {'provider': 'agentmemory', 'version': VERSION, 'installed': False,
           'available': False, 'integrity': 'missing', 'operations': list(OPERATIONS),
           'mode': 'standalone-local', 'network_used': False,
           'limitations': ['Keyword matching; no embeddings or engine server.',
                           'Project stores are separate; automatic capture hooks are disabled.',
                           'An owner-approved task grant is required before use.']}
    if not resource_root:
        return row
    row['installed'] = (Path(resource_root) / BUNDLE / 'dist/standalone.mjs').is_file()
    try:
        _runtime(resource_root)
    except (OSError, ValueError):
        row['integrity'] = 'failed' if row['installed'] else 'missing'
        return row
    row.update(available=True, integrity='verified')
    return row


def _text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise AgentMemoryError(f'Use a nonempty, bounded {label}.')
    return value


def canonical_arguments(action, arguments):
    """The approval layer may use this same validation before reviewing a call."""
    if action not in OPERATIONS or not isinstance(arguments, dict):
        raise AgentMemoryError('Use a supported AgentMemory operation and argument object.')
    required = {'remember': {'project', 'content'}, 'recall': {'project', 'query'},
                'forget': {'project', 'memory_ids', 'reason'}}[action]
    optional = {'limit'} if action == 'recall' else set()
    if set(arguments) - required - optional or required - set(arguments):
        raise AgentMemoryError('AgentMemory arguments do not match the selected operation.')
    project = arguments['project']
    if not isinstance(project, str) or not _PROJECT.fullmatch(project):
        raise AgentMemoryError('Use a project ID of 1 to 64 lowercase letters, digits, underscores or hyphens.')
    value = {'project': project}
    if action == 'remember':
        value['content'] = _text(arguments['content'], 8000, 'memory note')
    elif action == 'recall':
        value['query'] = _text(arguments['query'], 512, 'search query')
        limit = arguments.get('limit', 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise AgentMemoryError('The memory result limit must be between 1 and 20.')
        value['limit'] = limit
    else:
        ids = arguments['memory_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                or any(not isinstance(x, str) or not _MEMORY_ID.fullmatch(x) for x in ids)
                or len(set(ids)) != len(ids)):
            raise AgentMemoryError('Select 1 to 20 distinct memory IDs to forget.')
        value['memory_ids'] = list(ids)
        value['reason'] = _text(arguments['reason'], 512, 'deletion reason')
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON member.')
        result[key] = value
    return result


def _json(raw):
    def invalid(_):
        raise ValueError('Non-finite JSON.')
    return json.loads(raw, object_pairs_hook=_object, parse_constant=invalid)


def _store(path):
    path = _checked_path(path)
    if not path.exists():
        return b'', {}
    with path.open('rb') as stream:
        raw = stream.read(MAX_STORE_BYTES + 1)
    if len(raw) > MAX_STORE_BYTES:
        raise AgentMemoryError('The project memory store reached its 8 MiB limit.')
    try:
        value = _json(raw)
        if not isinstance(value, dict) or set(value) - {'mem:memories'}:
            raise ValueError('Invalid store.')
        notes = value.get('mem:memories', {})
        if not isinstance(notes, dict):
            raise ValueError('Invalid memories.')
        for key, note in notes.items():
            if (not _MEMORY_ID.fullmatch(key) or not isinstance(note, dict)
                    or note.get('id') != key or not isinstance(note.get('content'), str)
                    or len(note['content']) > 8000):
                raise ValueError('Invalid memory.')
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise AgentMemoryError('The project memory store is invalid; it was preserved for recovery.') from None
    return raw, value


@contextmanager
def _lock(directory):
    path = _checked_path(directory / 'store.lock')
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AgentMemoryError('Another memory operation is using this project; try again when it finishes.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _cancelled(event):
    if event is not None and event.is_set():
        raise AgentMemoryError('The AgentMemory operation was cancelled; the project store was not changed.')


def _invoke(bundle, node, scratch, action, args, cancel_event):
    preload = scratch / 'local-only.mjs'
    preload.write_text(_LOCAL_ONLY, encoding='utf-8')
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}
    env.update({key: str(scratch) for key in ('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP')})
    env['STANDALONE_PERSIST_PATH'] = str(scratch / 'store.json')
    # Node permissions forbid children, workers and native addons. Its pinned
    # single process needs no general process-tree kill or persistent service.
    command = [str(node), '--max-old-space-size=256', '--permission',
               '--allow-fs-read=' + str(bundle), '--allow-fs-read=' + str(scratch),
               '--allow-fs-write=' + str(scratch), '--import', preload.as_uri(),
               str(bundle / 'dist/standalone.mjs')]
    name = {'remember': 'memory_save', 'recall': 'memory_recall', 'forget': 'memory_governance_delete'}[action]
    selected = {'remember': {'content': args.get('content')},
                'recall': {'query': args.get('query'), 'limit': args.get('limit')},
                'forget': {'memoryIds': args.get('memory_ids'), 'reason': args.get('reason')}}[action]
    require_dispatch('integrations.agentmemory')
    _cancelled(cancel_event)
    deadline = time.monotonic() + CALL_TIMEOUT
    bridge = MCPBridge(command, env=env, cwd=scratch, timeout=CALL_TIMEOUT)
    done = threading.Event()

    def watch_cancel():
        while not done.wait(.05):
            if cancel_event is not None and cancel_event.is_set():
                bridge.close()
                return

    watcher = threading.Thread(target=watch_cancel, name='agentmemory-cancel', daemon=True)
    watcher.start()
    try:
        initialized = bridge.initialize(timeout=max(.01, deadline - time.monotonic()))
        if initialized.get('serverInfo') != {'name': 'agentmemory', 'version': VERSION}:
            raise AgentMemoryError('The AgentMemory server returned an unexpected identity.')
        response = bridge.request('tools/call', {'name': name, 'arguments': selected},
                                  timeout=max(.01, deadline - time.monotonic()))
        if (not isinstance(response, dict) or response.get('isError')
                or not isinstance(response.get('content'), list) or len(response['content']) != 1):
            raise AgentMemoryError('AgentMemory did not confirm the selected operation.')
        block = response['content'][0]
        if not isinstance(block, dict) or block.get('type') != 'text' or not isinstance(block.get('text'), str):
            raise AgentMemoryError('AgentMemory returned an invalid result.')
        raw = block['text'].encode('utf-8')
        if len(raw) > MAX_RESULT_BYTES:
            raise AgentMemoryError('The AgentMemory result exceeded its size limit.')
        try:
            result = _json(raw)
        except (ValueError, TypeError, RecursionError):
            raise AgentMemoryError('AgentMemory returned invalid result data.') from None
        if not isinstance(result, dict):
            raise AgentMemoryError('AgentMemory returned invalid result data.')
        return result
    except MCPBridgeError:
        _cancelled(cancel_event)
        raise AgentMemoryError('AgentMemory stopped or timed out. The project store was not changed; the call was not retried.') from None
    finally:
        done.set()
        bridge.close()
        watcher.join(timeout=1)


def _verify_effect(action, args, before, after, result):
    old, new = before.get('mem:memories', {}), after.get('mem:memories', {})
    valid = False
    if action == 'remember':
        identity = result.get('saved')
        if isinstance(identity, str) and _MEMORY_ID.fullmatch(identity):
            note = new.get(identity, {})
            valid = (identity not in old and set(new) == set(old) | {identity}
                     and all(new[key] == value for key, value in old.items())
                     and note.get('content') == args['content'] and note.get('type') == 'fact')
    elif action == 'forget':
        expected = {key: value for key, value in old.items() if key not in args['memory_ids']}
        valid = (new == expected and result.get('deleted') == len(old) - len(expected)
                 and result.get('requested') == len(args['memory_ids']) and result.get('reason') == args['reason'])
    else:
        rows = result.get('results')
        valid = (before == after and isinstance(rows, list) and len(rows) <= args['limit']
                 and all(isinstance(row, dict) and row == old.get(row.get('id')) for row in rows))
    if not valid:
        raise AgentMemoryError('AgentMemory did not durably confirm the expected result; the project store was not changed.')


def execute(action, arguments, *, data_root, resource_root, cancel_event=None):
    """Execute only after the trusted caller validates current owner/task scope."""
    args = canonical_arguments(action, arguments)
    require_dispatch('integrations.agentmemory')
    _cancelled(cancel_event)
    try:
        bundle, node = _runtime(resource_root)
        directory = _checked_path(Path(data_root) / 'Integrations' / 'AgentMemory' / ('project-' + args['project']))
        if directory == bundle or bundle in directory.parents:
            raise AgentMemoryError('Memory data must be separate from the pinned runtime.')
        directory.mkdir(parents=True, exist_ok=True)
        store = directory / 'store.json'
        with _lock(directory):
            original, before = _store(store)
            with tempfile.TemporaryDirectory(prefix='transaction-', dir=directory) as temporary:
                scratch = _checked_path(temporary)
                staged = scratch / 'store.json'
                staged.write_bytes(original or b'{}')
                result = _invoke(bundle, node, scratch, action, args, cancel_event)
                _cancelled(cancel_event)
                _, after = _store(staged)
                _verify_effect(action, args, before, after, result)
                if _store(store)[0] != original:
                    raise AgentMemoryError('The project store changed during this operation; no memory update was applied.')
                if action != 'recall':
                    require_dispatch('integrations.agentmemory')
                    _cancelled(cancel_event)
                    with staged.open('r+b') as stream:
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(staged, _checked_path(store))
        return {'provider': 'agentmemory', 'version': VERSION, 'action': action,
                'project': args['project'], 'result': result, 'network_used': False,
                'generated_answer': False}
    except AgentMemoryError:
        raise
    except (OSError, ValueError, TypeError):
        raise AgentMemoryError('The AgentMemory runtime or project store is unavailable; no operation was automatically retried.') from None
