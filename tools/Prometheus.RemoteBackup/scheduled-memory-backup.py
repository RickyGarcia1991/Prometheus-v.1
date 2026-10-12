"""Self-contained, verified SQLite memory snapshots. Standard library only."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import uuid

FORMAT = 'prometheus-memory-auto-v2'
PREFIX = 'prometheus-memory-auto-'
MANAGED_NAME = re.compile(r'^prometheus-memory-auto-\d{8}T\d{12}Z-[a-f0-9]{12}\.sqlite3$')
MAX_BYTES = 2 * 1024**3


class BackupDeferred(RuntimeError):
    pass


def plain(path):
    path = Path(path).expanduser().absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked backup paths are refused.')
    return path.resolve(strict=False)


def check_time(deadline, eject_marker=None):
    if eject_marker is not None and Path(eject_marker).exists():
        raise BackupDeferred('SSD eject is in progress; existing memory and backups were preserved.')
    if time.monotonic() > deadline:
        raise BackupDeferred('Backup time budget exceeded; retry after other disk activity finishes.')


@contextmanager
def destination_lock(directory):
    lock = plain(directory / '.memory-auto.lock')
    handle = lock.open('a+b')
    try:
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise BackupDeferred('Another backup owns this destination; no retention work was performed.') from error
        yield
    finally:
        handle.close()  # Keep the lock filename so concurrent workers share one file.


def validate_database(path, deadline):
    path = plain(path)
    if not path.is_file():
        raise FileNotFoundError('Memory database is missing.')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Memory database integrity check failed.')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Memory database relationships are invalid.')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (1, 2):
            raise ValueError('Unsupported memory schema version.')
        names = ('sessions', 'turns') if version == 1 else ('sessions', 'turns', 'knowledge', 'research_notes')
        return {'schema_version': version, **{name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in names}}


def snapshot(source, target, deadline, eject_marker=None):
    # SQLite's online backup API copies one consistent snapshot, including WAL
    # commits. Comparing it to separate live counts would race an active writer.
    def progress(status, remaining, total):
        check_time(deadline, eject_marker)
        if total * page_size > MAX_BYTES:
            raise ValueError('Memory backup exceeds its page budget.')
    with target.open('xb'):
        pass
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=1)) as src:
        page_size = src.execute('PRAGMA page_size').fetchone()[0]
        page_count = src.execute('PRAGMA page_count').fetchone()[0]
        if page_size * page_count > MAX_BYTES:
            raise ValueError('Memory backup exceeds the 2 GiB size limit.')
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=128, progress=progress, sleep=.05)
    if target.stat().st_size > MAX_BYTES:
        raise ValueError('Memory backup exceeds the 2 GiB size limit.')


def digest(path, deadline):
    result = hashlib.sha256()
    with plain(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            check_time(deadline)
            result.update(block)
    return result.hexdigest()


def prune(directory, keep, deadline, eject_marker):
    verified, retained = [], []
    entries = sorted(directory.glob(PREFIX + '*.sqlite3'), reverse=True)
    if len(entries) > 1000:
        return [], ['Retention deferred: more than 1,000 candidate files.']
    for file in entries:
        check_time(deadline, eject_marker)
        if not MANAGED_NAME.fullmatch(file.name):
            continue  # Legacy backups and unrelated files are never adopted.
        try:
            file = plain(file)
            manifest = plain(file.with_suffix('.sqlite3.json'))
            if manifest.stat().st_size > 65536:
                raise ValueError('Oversized backup metadata')
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            if (not isinstance(metadata, dict) or metadata.get('format') != FORMAT
                    or metadata.get('file') != file.name or metadata.get('size_bytes') != file.stat().st_size
                    or metadata.get('sha256') != digest(file, deadline)
                    or metadata.get('records') != validate_database(file, deadline)):
                raise ValueError('Unverified retained backup')
            verified.append((file, manifest))
        except (OSError, ValueError, sqlite3.DatabaseError):
            retained.append(file.name)
    removed = []
    for file, manifest in verified[keep:]:
        check_time(deadline, eject_marker)
        try:
            plain(file).unlink()
            plain(manifest).unlink()
            removed.append(file.name)
        except OSError:
            retained.append(file.name)
    return removed, retained


def run(source, destination, keep=14, *, eject_marker=None, timeout_seconds=120):
    if type(keep) is not int or not 2 <= keep <= 365:
        raise ValueError('Retention must be between 2 and 365 verified snapshots.')
    if not 1 <= timeout_seconds <= 600:
        raise ValueError('Backup timeout must be between 1 and 600 seconds.')
    deadline = time.monotonic() + timeout_seconds
    source, directory = plain(source), plain(destination)
    if not source.is_file():
        return {'result': 'SKIP', 'reason': 'Source memory is absent; no database or backup was created.'}
    check_time(deadline, eject_marker)
    if source.stat().st_size > MAX_BYTES:
        raise ValueError('Memory backup exceeds the 2 GiB size limit.')
    if directory == source.parent or directory == source:
        raise ValueError('Choose a backup directory separate from the live database.')
    directory.mkdir(parents=True, exist_ok=True)
    with destination_lock(directory):
        check_time(deadline, eject_marker)
        with tempfile.TemporaryDirectory(prefix='.prometheus-backup-', dir=directory) as temporary:
            staged = Path(temporary) / 'snapshot.sqlite3'
            snapshot(source, staged, deadline, eject_marker)
            counts = validate_database(staged, deadline)
            restored = Path(temporary) / 'restore-check.sqlite3'
            snapshot(staged, restored, deadline, eject_marker)
            if validate_database(restored, deadline) != counts:
                raise ValueError('Restore verification did not reproduce the snapshot records.')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            name = f'{PREFIX}{stamp}-{uuid.uuid4().hex[:12]}.sqlite3'
            final = directory / name
            metadata = {'format': FORMAT, 'file': name, 'created_utc': datetime.now(timezone.utc).isoformat(),
                        'records': counts, 'size_bytes': staged.stat().st_size, 'sha256': digest(staged, deadline)}
            check_time(deadline, eject_marker)
            # Exclusive creation supports NTFS and exFAT without replacing an
            # existing file. The verified manifest is published only after bytes.
            with staged.open('rb') as src, final.open('xb') as dst:
                while block := src.read(1024 * 1024):
                    check_time(deadline, eject_marker)
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
            if digest(final, deadline) != metadata['sha256']:
                raise ValueError('Published backup checksum does not match the snapshot.')
            check_time(deadline, eject_marker)
            with final.with_suffix('.sqlite3.json').open('x', encoding='utf-8') as stream:
                json.dump(metadata, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        try:
            removed, retained = prune(directory, keep, deadline, eject_marker)
            retention_complete = True
        except BackupDeferred:
            removed, retained, retention_complete = [], [], False
        return {'result': 'PASS', 'backup': str(final), 'records': counts, 'sha256': metadata['sha256'],
                'retention': keep, 'pruned': removed, 'unverified_or_busy_retained': retained,
                'retention_complete': retention_complete, 'restore_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--keep', type=int, default=14)
    parser.add_argument('--eject-marker', type=Path)
    args = parser.parse_args()
    try:
        result = run(args.source, args.destination, args.keep, eject_marker=args.eject_marker)
    except BackupDeferred as error:
        result = {'result': 'SKIP', 'reason': str(error)}
    except (OSError, ValueError, sqlite3.DatabaseError):
        print(json.dumps({'result': 'FAIL', 'reason': 'Backup or verification failed. Live memory and existing verified backups were preserved.'}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
