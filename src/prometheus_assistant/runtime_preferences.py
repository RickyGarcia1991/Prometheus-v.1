"""Host-local runtime backend preference; callers own exact owner approval.

No runtime is started and no global environment is changed. Revisions protect
against stale reviews, not same-account replacement of the entire preference DB.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from .coding_workspace import _no_links

_CPU = dict(OLLAMA_LLM_LIBRARY='cpu', OLLAMA_VULKAN='0',
            GGML_VK_VISIBLE_DEVICES='-1', CUDA_VISIBLE_DEVICES='-1',
            HIP_VISIBLE_DEVICES='-1', ROCR_VISIBLE_DEVICES='-1')
_MAX_REVISION = 2**63 - 2


def _valid(backend, revision):
    if backend not in ('automatic', 'cpu') or not isinstance(backend, str):
        raise ValueError('Choose automatic or cpu runtime backend.')
    if type(revision) is not int or not 0 <= revision <= _MAX_REVISION:
        raise ValueError('Invalid runtime preference revision.')


class Preferences:
    def __init__(self, path=None):
        if path is None:
            local = os.environ.get('LOCALAPPDATA', '')
            if os.name != 'nt' or not local or not Path(local).is_absolute():
                raise ValueError('Runtime preferences require an absolute Windows local profile path.')
            path = Path(local) / 'Prometheus' / 'runtime-preferences.sqlite3'
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError('Runtime preferences require an absolute host-local path.')
        self._check_path()

    def _check_path(self):
        for path in (self.path, *(Path(str(self.path) + suffix) for suffix in ('-journal', '-wal', '-shm'))):
            _no_links(path)
        if self.path.exists() and (not self.path.is_file() or self.path.stat().st_size > 1024*1024):
            raise ValueError('Invalid runtime preference storage.')

    @staticmethod
    def _read(db):
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Unsupported or malformed runtime preference storage.')
        rows = db.execute('SELECT slot, backend, revision FROM runtime_preference LIMIT 2').fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise ValueError('Malformed runtime preference record.')
        _, backend, revision = rows[0]
        _valid(backend, revision)
        if revision < 1:
            raise ValueError('Malformed stored runtime preference revision.')
        return dict(backend=backend, revision=revision)

    def snapshot(self):
        self._check_path()
        if not self.path.exists():
            return dict(backend='automatic', revision=0)
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                return self._read(db)
        except sqlite3.Error:
            raise ValueError('Runtime preference storage is unavailable or malformed.') from None

    def _create(self, backend):
        """Publish a fully committed initial DB without replacing another writer."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._check_path()
        descriptor, name = tempfile.mkstemp(prefix='.runtime-preferences-', suffix='.sqlite3', dir=self.path.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary, timeout=5)) as db:
                with db:
                    db.execute('CREATE TABLE runtime_preference (slot INTEGER PRIMARY KEY CHECK(slot=1), backend TEXT NOT NULL, revision INTEGER NOT NULL)')
                    db.execute('PRAGMA user_version=1')
                    db.execute('INSERT INTO runtime_preference VALUES(1,?,1)', (backend,))
            self._check_path()
            try:
                # Same-directory hard-link publication is atomic and fails if the
                # destination exists; unlike POSIX rename it cannot replace it.
                os.link(temporary, self.path)
            except FileExistsError:
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)

    def configure(self, backend, expected_revision):
        """Apply one caller-approved change, rejecting stale/ABA review revisions."""
        _valid(backend, expected_revision)
        self._check_path()
        try:
            if not self.path.exists():
                if expected_revision != 0:
                    raise ValueError('Runtime preference changed; review it again.')
                if self._create(backend):
                    return dict(backend=backend, revision=1)
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    current = self._read(db)
                    if current['revision'] != expected_revision:
                        raise ValueError('Runtime preference changed; review it again.')
                    if expected_revision >= _MAX_REVISION:
                        raise ValueError('Runtime preference revision exhausted.')
                    revision = expected_revision + 1
                    db.execute('UPDATE runtime_preference SET backend=?, revision=? WHERE slot=1', (backend, revision))
                return dict(backend=backend, revision=revision)
        except sqlite3.Error:
            raise ValueError('Runtime preference storage is unavailable or malformed.') from None

    def child_environment(self, base_env):
        """Copy launcher environment; apply only explicit CPU child overrides."""
        result = dict(base_env)
        if self.snapshot()['backend'] == 'cpu':
            # Windows environment keys are case-insensitive. Remove alternate
            # casing so the child cannot receive conflicting duplicate entries.
            result = {key: value for key, value in result.items() if key.upper() not in _CPU}
            result.update(_CPU)
        return result
