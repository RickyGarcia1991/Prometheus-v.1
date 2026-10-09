"""Explicit project editing and test execution for the local coding workspace.

These functions are UI actions, not tools available to model-generated text.
Running tests executes trusted project code with the current user's permissions.
"""
from __future__ import annotations

import difflib
import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

MAX_FILE_BYTES = 256_000
MAX_OUTPUT_BYTES = 32_000
_DENIED = {'.git', '.svn', '.hg', '.ssh', '.aws', '.azure', '.gcloud', '.codex',
           '.agents', '.venv', 'venv', 'node_modules', '__pycache__', '.prometheus-edits'}
_SECRET_NAMES = {'credentials', 'credentials.json', 'secrets.json', 'secrets.toml',
                 'id_rsa', 'id_ed25519', 'authorized_keys'}
_SUFFIXES = {'.py', '.pyi', '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.html',
             '.css', '.scss', '.json', '.toml', '.yaml', '.yml', '.xml', '.md', '.txt',
             '.rst', '.csv', '.sql', '.sh', '.ps1', '.bat', '.cmd', '.cs', '.xaml',
             '.java', '.kt', '.kts', '.go', '.rs', '.c', '.h', '.cpp', '.hpp', '.cc',
             '.swift', '.m', '.r', '.jl', '.dart', '.lua', '.pl', '.rb', '.php',
             '.fs', '.fsx', '.hs', '.ex', '.exs', '.erl', '.scala', '.clj', '.ml',
             '.zig', '.asm', '.f90', '.cob', '.sol', '.vhd', '.vhdl', '.v', '.sv',
             '.ino', '.tf', '.graphql', '.proto', '.ini', '.cfg', '.cmake'}
_TEXT_NAMES = {'dockerfile', 'makefile', 'license', '.gitignore', '.gitattributes'}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_LOCK = threading.Lock()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _no_links(path):
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            if _linked(item):
                raise ValueError('Linked paths, junctions and reparse points are not accepted.')


def _private(part):
    name = part.casefold()
    return (name in _DENIED or name in _SECRET_NAMES or name == '.env'
            or name.startswith('.env.') or name.endswith(('.pem', '.key', '.pfx', '.p12')))


def _write_atomic(path, data, mode=None):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.prometheus-', delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        if mode is not None:
            temp.chmod(stat.S_IMODE(mode))
        _no_links(path.parent)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class CodingWorkspace:
    """One explicitly chosen project, with reversible edits and bounded test jobs."""

    def __init__(self, project_root, history_root, *, resources=None, protected_roots=()):
        raw = Path(project_root).expanduser()
        if not raw.is_absolute():
            raise ValueError('Select an absolute project directory.')
        _no_links(raw)
        self.root = raw.resolve(strict=True)
        if not self.root.is_dir() or self.root == self.root.parent:
            raise ValueError('Select a project folder, not a drive root.')
        if any(_private(part) for part in self.root.parts):
            raise ValueError('Select a project outside private metadata and runtime folders.')
        from .hardware import resource_root
        resource_path = resources or resource_root()
        self.resources = Path(resource_path).resolve() if resource_path else None
        protected = [Path(path).resolve() for path in protected_roots]
        # Protect the running signed release and installed runtime automatically.
        protected.append(Path(__file__).resolve().parents[2])
        if self.resources:
            protected.append(self.resources)
        self.protected = tuple(protected)
        if any(self.root == path or self.root.is_relative_to(path) for path in self.protected):
            raise ValueError('Select a project outside the signed application and installed runtimes.')
        history = Path(history_root).expanduser()
        if not history.is_absolute():
            raise ValueError('Edit history needs an absolute data directory.')
        _no_links(history)
        self.history = history.resolve() / _sha(os.fsencode(str(self.root)))[:24]
        with _LOCKS_LOCK:
            self.lock = _LOCKS.setdefault(str(self.root).casefold(), threading.RLock())

    def _path(self, relative, *, directory=False):
        if not isinstance(relative, str) or not relative or len(relative) > 1000 or '\x00' in relative:
            raise ValueError('Use a relative project path.')
        # Check Windows syntax on every platform so saved requests stay portable.
        windows = PureWindowsPath(relative)
        parts = relative.replace('\\', '/').split('/')
        if windows.drive or windows.root or relative.startswith('/') or '..' in parts:
            raise ValueError('Project path must stay inside the selected folder.')
        if any(_private(part) or ':' in part or (part not in {'.', ''} and part != part.rstrip(' .')) for part in parts):
            raise ValueError('Private metadata and credential files are not available.')
        candidate = self.root.joinpath(*parts)
        _no_links(candidate)
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(self.root):
            raise ValueError('Project path escapes the selected folder.')
        if any(path == item or path.is_relative_to(item) for item in self.protected):
            raise ValueError('Signed application and runtime files are not editable projects.')
        if path == self.history.parent or path.is_relative_to(self.history.parent):
            raise ValueError('Edit recovery history is separate from project source files.')
        if directory:
            if not path.is_dir():
                raise ValueError('Choose a project directory.')
        elif not path.is_file() or (path.suffix.lower() not in _SUFFIXES and path.name.casefold() not in _TEXT_NAMES):
            raise ValueError('Choose a supported text or source file.')
        return path

    def _read(self, relative):
        path = self._path(relative)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f'File exceeds the {MAX_FILE_BYTES}-byte editor limit.')
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES or b'\x00' in data:
            raise ValueError('Choose a bounded UTF-8 text file.')
        try:
            content = data.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('The editor currently supports UTF-8 text files.') from exc
        return path, data, content

    def describe(self):
        return {'project': str(self.root), 'files': self.list_files(),
                'test_presets': self.test_presets(), 'max_file_bytes': MAX_FILE_BYTES,
                'test_execution': 'Run tests executes this project as your Windows user; choose a trusted project.'}

    def list_files(self, limit=300):
        limit = max(1, min(int(limit), 1000))
        rows = []
        _no_links(self.root)
        visited = 0
        for folder, directories, files in os.walk(self.root, followlinks=False):
            visited += 1
            if visited > 3000:
                break
            directories[:] = sorted(name for name in directories
                                    if not _private(name) and not _linked(Path(folder, name)))
            for name in sorted(files):
                relative = Path(folder, name).relative_to(self.root).as_posix()
                try:
                    path = self._path(relative)
                    size = path.stat().st_size
                    if size <= MAX_FILE_BYTES:
                        rows.append({'path': relative, 'size': size})
                except (ValueError, OSError):
                    continue
                if len(rows) >= limit:
                    return rows
        return rows

    def read_file(self, path):
        with self.lock:
            resolved, data, content = self._read(path)
            return {'path': resolved.relative_to(self.root).as_posix(), 'content': content,
                    'sha256': _sha(data), 'size': len(data)}

    def _proposal(self, path, content, expected_sha256):
        resolved, before, old_content = self._read(path)
        if not isinstance(expected_sha256, str) or _sha(before) != expected_sha256:
            raise ValueError('File changed since it was opened. Reopen it and review a new diff.')
        if not isinstance(content, str) or '\x00' in content:
            raise ValueError('Replacement must be UTF-8 text.')
        # Browser textareas normalize CRLF. Preserve the file's existing newline/BOM style.
        if '\r\n' in old_content and '\n' not in old_content.replace('\r\n', ''):
            content = content.replace('\r\n', '\n').replace('\n', '\r\n')
        after = content.encode('utf-8')
        if before.startswith(b'\xef\xbb\xbf'):
            after = b'\xef\xbb\xbf' + after
        if len(after) > MAX_FILE_BYTES:
            raise ValueError(f'Replacement exceeds the {MAX_FILE_BYTES}-byte editor limit.')
        return resolved, before, after, old_content, content

    def preview_edit(self, path, content, expected_sha256):
        with self.lock:
            resolved, before, after, old, new = self._proposal(path, content, expected_sha256)
            relative = resolved.relative_to(self.root).as_posix()
            diff = ''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                              fromfile='a/' + relative, tofile='b/' + relative))
            return {'path': relative, 'before_sha256': _sha(before), 'after_sha256': _sha(after),
                    'diff': diff, 'changed': before != after}

    def apply_edit(self, path, content, expected_sha256):
        with self.lock:
            resolved, before, after, _, _ = self._proposal(path, content, expected_sha256)
            relative = resolved.relative_to(self.root).as_posix()
            if before == after:
                return {'edit_id': None, 'path': relative, 'sha256': _sha(after), 'changed': False}
            _no_links(self.history)
            self.history.mkdir(parents=True, exist_ok=True)
            edit_id = uuid.uuid4().hex
            mode = resolved.stat().st_mode
            record = {'edit_id': edit_id, 'project': str(self.root), 'path': relative,
                      'before_sha256': _sha(before), 'after_sha256': _sha(after),
                      'mode': mode, 'created_at': time.time(), 'undone': False}
            _write_atomic(self.history / (edit_id + '.before'), before)
            _write_atomic(self.history / (edit_id + '.json'), json.dumps(record).encode('utf-8'))
            # Recheck after saving the backup, immediately before atomic replacement.
            if self._read(relative)[1] != before:
                raise ValueError('File changed during the edit. No project change was applied.')
            _write_atomic(resolved, after, mode)
            return {'edit_id': edit_id, 'path': relative, 'sha256': _sha(after), 'changed': True}

    def undo_edit(self, edit_id):
        if not isinstance(edit_id, str) or not re.fullmatch(r'[a-f0-9]{32}', edit_id):
            raise ValueError('Invalid edit identifier.')
        with self.lock:
            _no_links(self.history)
            record_path = self.history / (edit_id + '.json')
            backup_path = self.history / (edit_id + '.before')
            _no_links(record_path)
            _no_links(backup_path)
            if record_path.stat().st_size > 16_000 or backup_path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('Invalid edit history size.')
            record = json.loads(record_path.read_text(encoding='utf-8'))
            if record.get('project') != str(self.root) or record.get('edit_id') != edit_id or record.get('undone'):
                raise ValueError('This edit cannot be undone in the selected project.')
            resolved, current, _ = self._read(record['path'])
            if _sha(current) != record['after_sha256']:
                raise ValueError('File changed after this edit. Undo would overwrite newer work.')
            before = backup_path.read_bytes()
            if _sha(before) != record['before_sha256']:
                raise ValueError('Edit backup failed its checksum check.')
            if self._read(record['path'])[1] != current:
                raise ValueError('File changed during undo. No project change was applied.')
            _write_atomic(resolved, before, resolved.stat().st_mode)
            record['undone'] = True
            _write_atomic(record_path, json.dumps(record).encode('utf-8'))
            return {'path': record['path'], 'sha256': _sha(before), 'undone': True}

    def _runtimes(self):
        python = Path(sys.executable)
        if self.resources and (self.resources / 'Python/python.exe').is_file():
            python = self.resources / 'Python/python.exe'
        node = None
        if self.resources:
            candidates = sorted((self.resources / 'Coding').glob('Ollama-Tools-*/runtime/node-*-win-x64/node.exe'), reverse=True)
            if candidates:
                node = str(candidates[0])
        node = node or shutil.which('node')
        return str(python), node

    def test_presets(self):
        python, node = self._runtimes()
        return [{'id': key, 'label': label, 'available': bool(runtime), 'runtime': runtime}
                for key, label, runtime in [('python-unittest', 'Python unittest', python),
                                           ('python-pytest', 'Python pytest (if installed)', python),
                                           ('python-compile', 'Python syntax check', python),
                                           ('node-test', 'Node.js tests', node)]]

    def _command(self, preset, target):
        python, node = self._runtimes()
        if not isinstance(target, str):
            raise ValueError('Use a relative test target.')
        if preset == 'python-unittest':
            target = target or ('tests' if (self.root / 'tests').is_dir() else '.')
            path = self._path(target, directory=True)
            return [python, '-m', 'unittest', 'discover', '-s', str(path)]
        if preset in {'python-pytest', 'python-compile', 'node-test'}:
            if not target and preset == 'python-compile':
                raise ValueError('Choose a Python file for the syntax check.')
            if target:
                candidate = self.root / target
                path = self._path(target, directory=candidate.is_dir())
            else:
                path = self._path('.', directory=True)
            if preset == 'python-pytest':
                return [python, '-m', 'pytest', str(path), '-q']
            if preset == 'python-compile':
                if not path.is_file() or path.suffix.lower() != '.py':
                    raise ValueError('Choose one Python source file for the syntax check.')
                return [python, '-m', 'py_compile', str(path)]
            if not node:
                raise ValueError('Node.js is not installed. No dependency will be downloaded automatically.')
            return [node, '--test', *([str(path)] if target else [])]
        raise ValueError('Choose a listed test preset.')

    def run_tests(self, preset, target='', *, timeout=120, cancel_event=None, on_output=None):
        """Run an explicit preset. Never call this automatically from model output."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
            raise ValueError('Test timeout must be greater than zero and at most 600 seconds.')
        argv = self._command(preset, target)
        if cancel_event is not None and cancel_event.is_set():
            return {'argv': argv, 'exit_code': None, 'output': '', 'truncated': False,
                    'cancelled': True, 'timed_out': False, 'duration_seconds': 0}
        started = time.monotonic()
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        kwargs = {'cwd': self.root, 'env': env, 'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE,
                  'stderr': subprocess.STDOUT, 'shell': False}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        process = subprocess.Popen(argv, **kwargs)
        try:
            process_job = _WindowsJob(process) if os.name == 'nt' else None
        except Exception:
            _stop_process_tree(process)
            process.wait(timeout=5)
            process.stdout.close()
            raise
        chunks = bytearray()
        total = 0

        def reader():
            nonlocal total
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    return
                remaining = MAX_OUTPUT_BYTES - len(chunks)
                total += len(chunk)
                if remaining > 0:
                    kept = chunk[:remaining]
                    chunks.extend(kept)
                    if on_output is not None:
                        try:
                            on_output(kept.decode('utf-8', errors='replace'))
                        except Exception:
                            pass

        reader_thread = threading.Thread(target=reader, daemon=True, name='prometheus-test-output')
        reader_thread.start()
        cancelled = timed_out = False
        try:
            while process.poll() is None:
                cancelled = cancel_event is not None and cancel_event.is_set()
                timed_out = time.monotonic() - started >= timeout
                if cancelled or timed_out:
                    _stop_process_tree(process)
                    break
                time.sleep(.05)
            process.wait(timeout=5)
            # Test suites sometimes leave a child holding stdout open. Close all
            # descendants before waiting for the output reader, even on success.
            if process_job is not None:
                process_job.close()
            elif os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            reader_thread.join(timeout=2)
        finally:
            if process.poll() is None:
                _stop_process_tree(process)
            if process_job is not None:
                process_job.close()
            if not reader_thread.is_alive():
                process.stdout.close()
        return {'argv': argv, 'exit_code': process.returncode,
                'output': chunks.decode('utf-8', errors='replace'), 'truncated': total > MAX_OUTPUT_BYTES,
                'cancelled': bool(cancelled), 'timed_out': timed_out,
                'duration_seconds': round(time.monotonic() - started, 3)}


def _stop_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
        try:
            subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=subprocess.CREATE_NO_WINDOW, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


class _WindowsJob:
    """Own test descendants so cancellation and ordinary completion release them."""

    def __init__(self, process):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), 'Could not create the test process job.')
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        owned_process = None
        try:
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError(ctypes.get_last_error(), 'Could not limit the test process job.')
            owned_process = self.kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
            if not owned_process or not self.kernel.AssignProcessToJobObject(self.handle, owned_process):
                raise OSError(ctypes.get_last_error(), 'Could not attach the test process job.')
        except BaseException:
            self.close()
            raise
        finally:
            if owned_process:
                self.kernel.CloseHandle(owned_process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
