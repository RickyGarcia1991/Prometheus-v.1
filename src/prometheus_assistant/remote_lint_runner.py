"""Bounded, pinned Linux Ruff primitive; no dispatch, enrollment or approval.

Only a trusted, provisioned native Ruff binary may be pinned here. Resource
limits and no-fix flags are not a sandbox for an arbitrary malicious binary.
The caller owns authentication, admission, worker/runtime integrity and locking.
Submitted Python is sent to Ruff as text, never imported or executed by us.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time


PROFILE = 'ruff-python-v1'
MAX_OUTPUT_BYTES = 65536
MAX_DIAGNOSTICS = 16
MAX_MESSAGE_BYTES = 160
_FLAGS = ['check', '--isolated', '--no-fix', '--no-fix-only', '--no-unsafe-fixes',
          '--select', 'E9,F', '--output-format', 'json', '--target-version', 'py311',
          '--no-cache', '--no-preview']
_RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)
_PRIVATE = {'venv', '__pycache__', 'node_modules', 'site-packages'}
_TOOL_LIMIT = 128 * 1024 * 1024

# Fixed trusted launcher: no interpolation of source, no preexec_fn in a
# threaded service. The held fd is hashed again and execve'd directly so path
# replacement cannot select another executable between verification and exec.
_CHILD = r'''
import hashlib,json,os,resource,sys
if sys.platform != 'linux' or os.geteuid() == 0:
    raise SystemExit(125)
def ceiling(kind,wanted):
    old=resource.getrlimit(kind)
    value=min([wanted]+[v for v in old if v != resource.RLIM_INFINITY])
    resource.setrlimit(kind,(value,value))
ceiling(resource.RLIMIT_AS,512*1024*1024)
ceiling(resource.RLIMIT_CPU,5)
ceiling(resource.RLIMIT_FSIZE,65536)
ceiling(resource.RLIMIT_CORE,0)
ceiling(resource.RLIMIT_NOFILE,32)
ceiling(resource.RLIMIT_NPROC,64)
fd=int(sys.argv[1]); expected=sys.argv[2]; argv=json.loads(sys.argv[3])
os.lseek(fd,0,0)
digest=hashlib.sha256(); total=0; first=True
while True:
    block=os.read(fd,65536)
    if not block: break
    if first and block[:4] != b'\x7fELF': raise SystemExit(125)
    first=False; total+=len(block)
    if total>128*1024*1024: raise SystemExit(125)
    digest.update(block)
if first or digest.hexdigest()!=expected: raise SystemExit(125)
os.execve(fd,argv,dict(os.environ))
'''


class _Failure(Exception):
    pass


def _path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or not value.endswith('.py'):
        raise ValueError('A bounded portable relative Python path is required.')
    parts = value.split('/')
    if any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', part)
           or part.endswith('.') or part.casefold() in _PRIVATE or _RESERVED.match(part)
           for part in parts) or any(part.endswith('.py') for part in parts[:-1]):
        raise ValueError('Nonportable or private snapshot path.')
    return value


def _files(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValueError('One to eight Python snapshots are required.')
    rows, seen, components, total = [], set(), {}, 0
    for row in raw:
        if not isinstance(row, dict) or set(row) != {'path', 'content'}:
            raise ValueError('Unexpected snapshot fields.')
        path = _path(row['path'])
        if path.casefold() in seen:
            raise ValueError('Duplicate or case-colliding snapshot path.')
        seen.add(path.casefold())
        parts = path.split('/')
        for index in range(1, len(parts) + 1):
            part = '/'.join(parts[:index])
            previous = components.setdefault(part.casefold(), part)
            if previous != part:
                raise ValueError('Case-colliding snapshot directory.')
            if len(components) > 128:
                raise ValueError('Snapshot tree exceeds its entry bound.')
        text = row['content']
        if not isinstance(text, str) or len(text) > 256000:
            raise ValueError('Snapshot exceeds its text bound.')
        try:
            content = text.encode('utf-8')
        except UnicodeError:
            raise ValueError('Snapshot must be valid UTF-8.') from None
        total += len(content)
        if len(content) > 256000 or total > 512000:
            raise ValueError('Snapshot exceeds its UTF-8 byte bound.')
        rows.append(dict(path=path, content=content, sha256=hashlib.sha256(content).hexdigest()))
    return rows


def _tool_fd(path, expected):
    # Reject linked parents as well as a linked leaf. The integrator must keep
    # the provisioned tool tree protected from writes by other principals.
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise _Failure('tool_identity_mismatch')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or not 4 <= info.st_size <= _TOOL_LIMIT
                or os.name != 'nt' and not info.st_mode & 0o111):
            raise _Failure('tool_identity_mismatch')
        _verify_fd(fd, expected)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _verify_fd(fd, expected):
    os.lseek(fd, 0, os.SEEK_SET)
    digest, total, first = hashlib.sha256(), 0, True
    while True:
        block = os.read(fd, 65536)
        if not block:
            break
        if first and block[:4] != b'\x7fELF':
            raise _Failure('tool_identity_mismatch')
        first = False
        total += len(block)
        if total > _TOOL_LIMIT:
            raise _Failure('tool_identity_mismatch')
        digest.update(block)
    if first or digest.hexdigest() != expected:
        raise _Failure('tool_identity_mismatch')


def _linux_process(argv, *, cwd, env, timeout, tool_fd, tool_sha256, stdin):
    """Own one process group; hard limits also apply to --version calls."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([sys.executable, '-I', '-S', '-c', _CHILD,
            str(tool_fd), tool_sha256, json.dumps(argv)], stdin=subprocess.PIPE,
            stdout=output, stderr=errors, cwd=cwd, env=env, close_fds=True,
            pass_fds=(tool_fd,), start_new_session=True)
        try:
            process.communicate(input=stdin, timeout=timeout)
        finally:
            # Kill any remaining members of this owned group, including after
            # a parent exits unexpectedly, and reap the direct child.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        output.seek(0)
        errors.seek(0)
        return process.returncode, output.read(MAX_OUTPUT_BYTES + 1), errors.read(MAX_OUTPUT_BYTES + 1)


def _unchanged(source, work, rows):
    expected = {row['path']: row['sha256'] for row in rows}
    expected_dirs = {str(parent).replace('\\', '/') for name in expected
                     for parent in Path(name).parents if str(parent) != '.'}
    found, dirs, count = {}, set(), 0
    try:
        for root in (source, work):
            info = root.lstat()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                return False
        if any(work.iterdir()):
            return False
        pending = [source]
        while pending:
            directory = pending.pop()
            for path in directory.iterdir():
                count += 1
                if count > 128:
                    return False
                info = path.lstat()
                name = path.relative_to(source).as_posix()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    return False
                if stat.S_ISDIR(info.st_mode):
                    dirs.add(name)
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode) and info.st_size <= 256000 and info.st_nlink == 1:
                    with path.open('rb') as stream:
                        data = stream.read(256001)
                    found[name] = hashlib.sha256(data).hexdigest()
                else:
                    return False
        return found == expected and dirs == expected_dirs
    except OSError:
        return False


def _json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON field.')
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError('Non-finite JSON value.')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)


def _diagnostics(data, row, cwd):
    records = _json(data)
    if not isinstance(records, list) or len(records) > MAX_DIAGNOSTICS:
        raise ValueError('Diagnostics exceed their count bound.')
    lines = row['content'].decode('utf-8').split('\n')
    result = []
    for item in records:
        if not isinstance(item, dict):
            raise ValueError('Invalid diagnostic.')
        filename = item.get('filename')
        # Ruff commonly returns an absolute filename even with --stdin-filename.
        if not isinstance(filename, str):
            raise ValueError('Missing diagnostic path.')
        path = Path(filename)
        if path.is_absolute():
            filename = path.relative_to(cwd).as_posix()
        if filename != row['path']:
            raise ValueError('Diagnostic does not bind to this exact snapshot.')
        code, message = item.get('code'), item.get('message')
        if (not isinstance(code, str) or not re.fullmatch(r'(?:F[0-9]{3}|E9[0-9]{2}|invalid-syntax)', code)
                or not isinstance(message, str) or not message
                or len(message.encode('utf-8')) > MAX_MESSAGE_BYTES
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in message)):
            raise ValueError('Invalid or excessive diagnostic text.')
        points = []
        for key in ('location', 'end_location'):
            point = item.get(key)
            if not isinstance(point, dict) or set(point) != {'row', 'column'}:
                raise ValueError('Missing diagnostic position.')
            line, column = point['row'], point['column']
            if (type(line) is not int or type(column) is not int or not 1 <= line <= len(lines)
                    or not 1 <= column <= len(lines[line - 1].removesuffix('\r')) + 1):
                raise ValueError('Diagnostic position is outside the source.')
            points.append((line, column))
        if points[1] < points[0]:
            raise ValueError('Reversed diagnostic range.')
        result.append(dict(path=row['path'], code=code, message=message,
            line=points[0][0], column=points[0][1], end_line=points[1][0], end_column=points[1][1]))
    return result


def execute_linux_lint(files, *, tool_path, tool_sha256, tool_version,
                       profile=PROFILE, timeout_seconds=10, executor=None):
    """Return bounded tool evidence for exact in-memory Python snapshots.

    Tool pins come from trusted local configuration, never solely from a remote
    request. executor is a trusted test seam, not a remotely selectable option.
    No inputs are truncated. Invalid input raises ValueError; operational/tool
    failures return failed/tool_failed with no accepted diagnostics. A completed
    result with findings is not a tool failure and never grants edit approval.
    Linux limits: each process AS 512MiB, CPU 5s, output file 64KiB, 32 fds,
    per-user process/thread ceiling 64; entire job wall deadline <=30s. Root is
    refused because RLIMIT_NPROC does not constrain it. Admission stays external.
    """
    rows = _files(files)
    if (profile != PROFILE or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30
            or not isinstance(tool_sha256, str) or not re.fullmatch('[0-9a-f]{64}', tool_sha256)
            or not isinstance(tool_version, str) or len(tool_version) > 32
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', tool_version)):
        raise ValueError('Invalid lint profile, timeout or tool pin.')
    if not isinstance(tool_path, (str, os.PathLike)):
        raise ValueError('An absolute provisioned tool path is required.')
    path = Path(tool_path)
    if not path.is_absolute() or len(str(path)) > 4096 or '..' in path.parts:
        raise ValueError('An absolute provisioned tool path is required.')
    if executor is None and sys.platform != 'linux':
        raise RuntimeError('Ruff execution requires Linux resource limits.')
    result = dict(state='failed', status='tool_failed', diagnostics=[], reason='',
        tool=dict(sha256=tool_sha256, version=tool_version), profile=profile,
        files=[dict(path=row['path'], sha256=row['sha256']) for row in rows], source_unchanged=True)
    if executor is None and os.geteuid() == 0:
        return dict(result, status='unavailable', reason='unprivileged_linux_required')
    deadline = time.monotonic() + timeout_seconds
    fd = None
    try:
        fd = _tool_fd(path, tool_sha256)
        with tempfile.TemporaryDirectory(prefix='prometheus-lint-') as temporary:
            root = Path(temporary)
            source, work, scratch = root/'source', root/'work', root/'scratch'
            for directory in (source, work, scratch):
                directory.mkdir(mode=0o700)
            for row in rows:
                target = source/row['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(row['content'])
                target.chmod(0o400)
            env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8',
                HOME=str(scratch), TMPDIR=str(scratch), XDG_CACHE_HOME=str(scratch),
                RUFF_CACHE_DIR=str(scratch), RAYON_NUM_THREADS='1')
            total_output, diagnostics = 0, []
            def invoke(args, stdin=b''):
                nonlocal total_output
                _verify_fd(fd, tool_sha256)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Failure('timeout')
                returned = (executor or _linux_process)([str(path), *args], cwd=work,
                    env=dict(env), timeout=remaining, tool_fd=fd, tool_sha256=tool_sha256, stdin=stdin)
                if (not isinstance(returned, tuple) or len(returned) != 3
                        or type(returned[0]) is not int
                        or not isinstance(returned[1], bytes) or not isinstance(returned[2], bytes)):
                    raise _Failure('invalid_tool_result')
                total_output += len(returned[1]) + len(returned[2])
                if total_output > MAX_OUTPUT_BYTES:
                    raise _Failure('output_limit')
                if time.monotonic() >= deadline:
                    raise _Failure('timeout')
                return returned
            try:
                code, output, errors = invoke(['--version'])
                if code != 0 or errors or output != ('ruff ' + tool_version + '\n').encode('ascii'):
                    raise _Failure('tool_version_mismatch')
                for row in rows:
                    code, output, errors = invoke([*_FLAGS, '--stdin-filename', row['path'], '-'], row['content'])
                    if code not in (0, 1) or errors:
                        raise _Failure('tool_exit')
                    found = _diagnostics(output, row, work)
                    if (code == 0) != (not found):
                        raise _Failure('inconsistent_tool_exit')
                    diagnostics.extend(found)
                    if len(diagnostics) > MAX_DIAGNOSTICS:
                        raise _Failure('diagnostic_limit')
                _verify_fd(fd, tool_sha256)
                diagnostics.sort(key=lambda row: tuple(row[key] for key in
                    ('path', 'line', 'column', 'end_line', 'end_column', 'code', 'message')))
                if any(before == after for before, after in zip(diagnostics, diagnostics[1:])):
                    raise _Failure('duplicate_diagnostics')
                result.update(state='completed', status='findings' if diagnostics else 'passed',
                              diagnostics=diagnostics)
            finally:
                result['source_unchanged'] = _unchanged(source, work, rows)
    except _Failure as error:
        result.update(state='failed', status='tool_failed', diagnostics=[], reason=str(error))
    except subprocess.TimeoutExpired:
        result.update(state='failed', status='tool_failed', diagnostics=[], reason='timeout')
    except (OSError, ValueError, UnicodeError, RecursionError):
        result.update(state='failed', status='tool_failed', diagnostics=[], reason='invalid_tool_result')
    finally:
        if fd is not None:
            os.close(fd)
    if not result['source_unchanged']:
        result.update(state='failed', status='tool_failed', diagnostics=[], reason='source_changed')
    return result
