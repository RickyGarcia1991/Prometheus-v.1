"""Explicit, verified history backups; restoration never overwrites a database."""
from pathlib import Path
from contextlib import closing
import hashlib
import json
import sqlite3
import time


def validate_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed.')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Database relationships are invalid.')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (1, 2):
            raise ValueError('Unsupported history version.')
        tables = ('sessions', 'turns') if version == 1 else ('sessions', 'turns', 'knowledge', 'research_notes')
        return {'schema_version': version, **{
            table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            for table in tables
        }}


def backup_memory(source, destination, timeout_seconds=30):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    counts = validate_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve exclusively before SQLite opens it; no overwrite, even on a collision.
    with destination.open('xb'):
        pass
    try:
        deadline = time.monotonic() + timeout_seconds
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise ValueError('History backup timed out; close active writers and retry.')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=128, progress=progress, sleep=0.05)
        counts = validate_database(destination)
        metadata = {'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), **counts}
        with destination.with_suffix(destination.suffix + '.json').open('x', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        return metadata
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def restore_memory(source, destination):
    source = Path(source)
    metadata = json.loads(source.with_suffix(source.suffix + '.json').read_text(encoding='utf-8'))
    if hashlib.sha256(source.read_bytes()).hexdigest() != metadata['sha256']:
        raise ValueError('Backup checksum mismatch.')
    counts = validate_database(source)
    if any(counts[key] != metadata[key] for key in counts if key in metadata):
        raise ValueError('Backup record counts mismatch.')
    return backup_memory(source, destination)
