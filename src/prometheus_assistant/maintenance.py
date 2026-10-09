"""Bounded source checks, verified data backups and passive portable readiness.

Call maintenance on the interface's single worker, between foreground jobs.
Nothing here creates a background thread, records audio, or executes source data.
"""
from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sqlite3
import sys
import time
import uuid

from .hardware import detect_hardware
from .public_history import save_snapshot
from .public_resources import PROVIDERS, public_search

MAX_WATCHES = 100
MAX_BACKUP_FILES = 10_000
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
_PRIVATE_NAMES = {'.env', '.git', '.aws', '.ssh', '.gnupg', 'credentials',
                  'credentials.json', 'auth.json', 'token.json', 'tokens.json',
                  'secrets.json', 'secrets', 'models', 'node_modules', '__pycache__'}
_EXCLUDED_SUFFIXES = {'.pem', '.key', '.pfx', '.p12', '.gguf', '.safetensors', '.pt', '.pth', '.pyc'}


def _utc(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat()


def _plain_path(path):
    """Refuse symlinks/junctions before resolving, including linked ancestors."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked maintenance paths are refused.')
    return path


def _digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


class FreshnessSchedule:
    """Only explicitly selected public queries are watched; due state is persistent.

    ``online=True`` is the caller's assertion that the user has enabled sending
    these displayed queries to their publishers. Chats and imported documents
    are never selected or transmitted by this class.
    """
    def __init__(self, state_path, history_root):
        self.path = _plain_path(state_path)
        self.history_root = _plain_path(history_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS watches (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, query TEXT NOT NULL,
                interval_days INTEGER NOT NULL, enabled INTEGER NOT NULL,
                next_due REAL NOT NULL, last_checked REAL, last_success REAL,
                results_sha256 TEXT, changed INTEGER, error TEXT,
                lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
                last_snapshot TEXT, UNIQUE(provider, query))''')

    @contextmanager
    def _db(self):
        _plain_path(self.path)
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def watch(self, provider, query, *, interval_days=7, enabled=True, now=None):
        if provider not in PROVIDERS:
            raise ValueError('Unknown public publisher.')
        if not isinstance(query, str) or not query.strip() or len(query) > 400 or any(ord(c) < 32 for c in query):
            raise ValueError('Use a public query of 1–400 characters.')
        if type(interval_days) is not int or not 1 <= interval_days <= 365 or type(enabled) is not bool:
            raise ValueError('Choose a 1–365 day interval and a boolean enabled state.')
        query = query.strip()
        identity = hashlib.sha256(json.dumps([provider, query], ensure_ascii=False).encode()).hexdigest()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM watches WHERE id=?', (identity,)).fetchone()
            if existing:
                db.execute('UPDATE watches SET interval_days=?, enabled=? WHERE id=?',
                           (interval_days, int(enabled), identity))
            else:
                if db.execute('SELECT count(*) FROM watches').fetchone()[0] >= MAX_WATCHES:
                    raise ValueError('The 100-watch limit has been reached.')
                db.execute('INSERT INTO watches (id,provider,query,interval_days,enabled,next_due) VALUES (?,?,?,?,?,?)',
                           (identity, provider, query, interval_days, int(enabled), time.time() if now is None else now))
        return {'id': identity, 'provider': provider, 'query': query, 'enabled': enabled,
                'interval_days': interval_days, 'network_used': False}

    def status(self, *, now=None):
        now = time.time() if now is None else now
        with self._db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM watches ORDER BY provider,query')]
        for row in rows:
            row.pop('lease_token', None)
            row['enabled'] = bool(row['enabled'])
            row['due'] = row['enabled'] and row['next_due'] <= now and row['lease_until'] <= now
            row['changed'] = None if row['changed'] is None else bool(row['changed'])
            for field in ('next_due', 'last_checked', 'last_success'):
                row[field + '_utc'] = _utc(row[field]) if row[field] is not None else None
        return {'watches': rows, 'due_count': sum(row['due'] for row in rows), 'network_used': False,
                'disclosure': 'Enabled watches send only the displayed public query to its named publisher while Prometheus is open. Every returned version is retained.',
                'freshness_scope': 'Watched search results only; this does not certify that all sources, laws or deadlines are current.'}

    def run_due_checks(self, *, online=False, max_checks=1, now=None, search=None):
        if not online:
            return {'checks': [], 'network_used': False, 'reason': 'Online source checks are disabled.'}
        if type(max_checks) is not int or not 1 <= max_checks <= 3:
            raise ValueError('Run 1–3 bounded source checks per maintenance job.')
        checks = []
        for _ in range(max_checks):
            stamp = time.time() if now is None else now
            token = uuid.uuid4().hex
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM watches WHERE enabled=1 AND next_due<=? AND lease_until<=? ORDER BY next_due,id LIMIT 1', (stamp, stamp)).fetchone()
                if row is None:
                    break
                db.execute('UPDATE watches SET lease_until=?,lease_token=? WHERE id=?', (stamp + 300, token, row['id']))
            result = {'id': row['id'], 'provider': row['provider'], 'query': row['query']}
            try:
                response = (search or public_search)(row['query'], provider=row['provider'], online=True, limit=5)
                if response.get('provider') != row['provider'] or response.get('query', '').strip() != row['query']:
                    raise ValueError('Publisher response does not match this watch.')
                snapshot = save_snapshot(self.history_root, response)
                stable_hash = hashlib.sha256(json.dumps(response['results'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                changed = None if row['results_sha256'] is None else stable_hash != row['results_sha256']
                result.update(ok=True, changed=changed, baseline=changed is None, snapshot=snapshot)
                with self._db() as db:
                    db.execute('''UPDATE watches SET last_checked=?,last_success=?,next_due=?,results_sha256=?,
                        changed=?,error=NULL,last_snapshot=?,lease_until=0,lease_token=NULL WHERE id=? AND lease_token=?''',
                        (stamp, stamp, stamp + row['interval_days'] * 86400, stable_hash, changed,
                         snapshot['path'], row['id'], token))
            except Exception as error:
                message = str(error)[:500]
                result.update(ok=False, error=message)
                with self._db() as db:
                    # Errors back off for six hours. The previous successful version
                    # and its timestamp remain available and are never overwritten.
                    db.execute('''UPDATE watches SET last_checked=?,next_due=?,error=?,lease_until=0,lease_token=NULL
                        WHERE id=? AND lease_token=?''', (stamp, stamp + 21600, message, row['id'], token))
            checks.append(result)
        return {'checks': checks, 'network_used': bool(checks), 'history_preserved': True}


def _sqlite_copy(source, destination):
    deadline = time.monotonic() + 30
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite backup exceeded 30 seconds; source was not modified.')
        if total * 4096 > MAX_FILE_BYTES * 16:
            raise ValueError('SQLite backup exceeded its page budget.')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as src:
        if src.execute('PRAGMA page_size').fetchone()[0] * src.execute('PRAGMA page_count').fetchone()[0] > MAX_FILE_BYTES:
            raise ValueError('SQLite database exceeds the 64 MiB backup limit.')
        with closing(sqlite3.connect(str(destination))) as dst:
            src.backup(dst, pages=128, progress=progress, sleep=.05)
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Copied SQLite database failed quick_check.')


def _copy_data(source, destination):
    with source.open('rb') as stream:
        sqlite = stream.read(16) == b'SQLite format 3\x00'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if sqlite:
        _sqlite_copy(source, destination)
    else:
        before = source.stat()
        with source.open('rb') as reader, destination.open('xb') as writer:
            size = 0
            while chunk := reader.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError('A source file grew beyond the backup limit.')
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('A source file changed during backup; retry when its job has finished.')
    if destination.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('Copied file exceeds the 64 MiB limit.')


def _allowed_part(name):
    name = name.casefold()
    return name not in _PRIVATE_NAMES and not name.startswith('.env.') and not name.endswith(('-wal', '-shm', '-journal')) and Path(name).suffix not in _EXCLUDED_SUFFIXES


def create_backup(memory_path, destination_root, *, data_dirs=None, require_separate_volume=True):
    """Snapshot selected user data, excluding credentials, caches and model weights.

    Call between jobs so related document/index files are quiescent. SQLite's
    online backup API includes committed WAL contents without copying live WALs.
    The destination is normally a host-local folder on a different volume.
    """
    source = _plain_path(memory_path)
    destination_root = _plain_path(destination_root)
    if not source.is_file():
        raise ValueError('Conversation memory was not found.')
    same_volume = source.anchor.casefold() == destination_root.anchor.casefold() if os.name == 'nt' else source.stat().st_dev == next(p for p in (destination_root, *destination_root.parents) if p.exists()).stat().st_dev
    if require_separate_volume and same_volume:
        raise ValueError('Choose a backup destination on a separate volume from Prometheus data.')
    directories = {}
    for name, path in (data_dirs or {}).items():
        if name not in {'Library', 'Coding', 'Maintenance', 'PublicHistory', 'Routing'}:
            raise ValueError('Only Library, Coding, Maintenance, PublicHistory and Routing data may be added.')
        directory = _plain_path(path)
        if directory == destination_root or directory in destination_root.parents:
            raise ValueError('Backup output cannot be inside a source data directory.')
        if directory.exists() and not directory.is_dir():
            raise ValueError('Additional backup data must be a directory.')
        directories[name] = directory
    with source.open('rb') as stream:
        if stream.read(16) != b'SQLite format 3\x00':
            raise ValueError('Conversation memory must be a SQLite database.')
    files = [('memory.sqlite3', source)]
    skipped = []
    scanned = 0
    for label, directory in directories.items():
        if not directory.exists():
            continue
        # Walk without following links, checking each directory before traversal.
        for folder, subdirs, names in os.walk(directory, followlinks=False):
            scanned += len(subdirs) + len(names)
            if scanned > MAX_BACKUP_FILES * 2:
                raise ValueError('Selected data tree exceeds the bounded backup scan limit.')
            folder = _plain_path(folder)
            allowed_dirs = []
            for name in sorted(subdirs):
                candidate = folder / name
                if not _allowed_part(name) or candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
                    skipped.append(str(candidate.relative_to(directory)))
                else:
                    allowed_dirs.append(name)
            subdirs[:] = allowed_dirs
            for name in sorted(names):
                path = folder / name
                relative = label + '/' + path.relative_to(directory).as_posix()
                if not _allowed_part(name) or path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    skipped.append(relative)
                    continue
                files.append((relative, _plain_path(path)))
                if len(files) > MAX_BACKUP_FILES:
                    raise ValueError('Backup exceeds the 10,000-file limit; no backup was published.')
    total = sum(path.stat().st_size for _, path in files)
    if total > MAX_BACKUP_BYTES or any(path.stat().st_size > MAX_FILE_BYTES for _, path in files):
        raise ValueError('Selected backup exceeds 512 MiB total or 64 MiB per file; no backup was published.')
    destination_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination_root).free < total * 2 + 32 * 1024 * 1024:
        raise ValueError('Insufficient free space for a verified backup and restore check.')
    identity = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    staging = destination_root / ('.partial-' + identity)
    published = destination_root / ('Prometheus-backup-' + identity)
    staging.mkdir()
    try:
        entries = []
        for relative, path in files:
            target = staging / relative
            _copy_data(path, target)
            entries.append({'path': relative, 'bytes': target.stat().st_size, 'sha256': _digest(target)})
        if sum(row['bytes'] for row in entries) > MAX_BACKUP_BYTES:
            raise ValueError('Backup grew beyond its total size limit.')
        manifest = {'version': 1, 'created_utc': _utc(), 'files': entries, 'skipped': skipped,
                    'separate_volume': not same_volume, 'encrypted': False,
                    'scope': 'Conversation memory and explicitly selected app data; excludes models, runtimes and credential filenames. Store this plaintext backup privately.'}
        with (staging / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        verification = verify_backup(staging)
        staging.rename(published)
        return {'path': str(published), **verification, 'separate_volume': not same_volume,
                'skipped': skipped, 'source_unchanged': True, 'encrypted': False}
    except Exception:
        # Only this call's newly created UUID staging directory can be removed.
        if staging.parent == destination_root and staging.name == '.partial-' + identity:
            shutil.rmtree(staging)
        raise


def verify_backup(folder):
    folder = _plain_path(folder)
    manifest_path = _plain_path(folder / 'manifest.json')
    if manifest_path.stat().st_size > 5_000_000:
        raise ValueError('Backup manifest exceeds its size limit.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    rows = manifest.get('files')
    if manifest.get('version') != 1 or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_BACKUP_FILES:
        raise ValueError('Invalid backup manifest.')
    seen = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise ValueError('Invalid backup file entry.')
        raw = row['path']
        relative = PurePosixPath(raw)
        parts = raw.split('/')
        reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
        if (not raw or relative.is_absolute() or '\\' in raw or ':' in raw
                or any(not p or p in ('..', '.') or p.endswith(('.', ' ')) or p.split('.')[0].casefold() in reserved for p in parts)
                or any(ord(c) < 32 for c in raw) or raw.casefold() in seen or raw.casefold() == 'manifest.json'):
            raise ValueError('Unsafe or duplicate backup path.')
        seen.add(raw.casefold())
        path = _plain_path(folder.joinpath(*relative.parts))
        if folder not in path.parents or not path.is_file():
            raise ValueError('Backup file is missing or outside the backup directory.')
        size = path.stat().st_size
        if size > MAX_FILE_BYTES or size != row.get('bytes') or _digest(path) != row.get('sha256'):
            raise ValueError('Backup checksum or size verification failed: ' + raw)
        total += size
        if total > MAX_BACKUP_BYTES:
            raise ValueError('Backup exceeds the total size limit.')
    if 'memory.sqlite3' not in seen:
        raise ValueError('Backup does not contain conversation memory.')
    return {'verified': True, 'files': len(rows), 'bytes': total, 'manifest_sha256': _digest(manifest_path)}


def restore_backup(folder, destination):
    """Restore to a new folder only, never over the live SSD or an existing tree."""
    folder = _plain_path(folder)
    verification = verify_backup(folder)
    destination = _plain_path(destination)
    if destination.exists():
        raise ValueError('Restore destination must be a new, nonexistent folder.')
    if destination == folder or folder in destination.parents:
        raise ValueError('Restore destination must be outside the backup.')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < verification['bytes'] + 16 * 1024 * 1024:
        raise ValueError('Insufficient free space for restoration.')
    staging = destination.parent / ('.restore-' + uuid.uuid4().hex)
    staging.mkdir()
    try:
        for row in manifest['files']:
            source = _plain_path(folder / row['path'])
            target = staging / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copyfile(folder / 'manifest.json', staging / 'manifest.json')
        restored = verify_backup(staging)
        staging.rename(destination)
        return {'path': str(destination), **restored, 'live_data_overwritten': False}
    except Exception:
        if staging.parent == destination.parent and staging.name.startswith('.restore-'):
            shutil.rmtree(staging)
        raise


def readiness_report(data_root, resources_root=None):
    """Passive observations; no capture, eject, removable-device write or network."""
    data_root = _plain_path(data_root)
    disk = shutil.disk_usage(data_root)
    devices = {'microphone_input_devices': None, 'speaker_output_devices': None,
               'camera': 'Not probed; no camera was opened.', 'capture_performed': False}
    if os.name == 'nt':
        try:
            import ctypes
            devices.update(microphone_input_devices=int(ctypes.windll.winmm.waveInGetNumDevs()),
                           speaker_output_devices=int(ctypes.windll.winmm.waveOutGetNumDevs()))
        except (AttributeError, OSError):
            devices['audio_enumeration'] = 'Unavailable'
    root = _plain_path(resources_root) if resources_root else None
    return {'checked_utc': _utc(), 'hardware': asdict(detect_hardware()),
            'data_disk': {'path': str(data_root), 'free_gib': round(disk.free / 1024 ** 3, 2),
                          'total_gib': round(disk.total / 1024 ** 3, 2)},
            'runtime': {'python': platform.python_version(), 'executable': sys.executable,
                        'portable_python_present': bool(root and (root / 'Python/python.exe').is_file()),
                        'ollama_present': bool(root and (root / 'Ollama/runtime/ollama.exe').is_file())},
            'devices': devices, 'network_used': False,
            'manual_checks_remaining': [
                'Record an intentionally spoken microphone sample and review its transcript.',
                'Play an answer and confirm the correct speaker and volume.',
                'Close Prometheus, safely eject/reconnect the SSD, then reopen a saved conversation.',
                'Repeat startup and a representative model task on another computer.',
                'Measure frame rate and responsiveness on a higher-memory computer.'],
            'scope': 'Device presence is not a successful microphone, speaker, portability or performance test.'}
