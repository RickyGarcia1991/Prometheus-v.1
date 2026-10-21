"""Fixed, resource-limited local compilation; submitted source is never executed.

The application owns exact approval and durable claiming. This module only
reports local eligibility and performs one already-claimed syntax operation.
It does not write project files, connect to workers, or install anything.
"""
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from .remote_job_protocol import normalize_request, validate_receipt
from .resource_observations import ResourceProbe


# No source is read before limits are installed. The fixed bootstrap imports
# only the standard library and never starts other processes. A child-owned
# Job Object avoids the race between parent launch and external assignment.
_WINDOWS_CHILD_CODE = r'''
import sys
if sys.platform != 'win32':
    raise SystemExit(2)
import ctypes
class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
        ('flags', ctypes.c_uint32), ('minimum_working_set', ctypes.c_size_t),
        ('maximum_working_set', ctypes.c_size_t), ('active_processes', ctypes.c_uint32),
        ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32),
        ('scheduling', ctypes.c_uint32)]
class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'read_operations', 'write_operations', 'other_operations',
        'read_bytes', 'write_bytes', 'other_bytes')]
class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('reserved_io', IoCounters),
        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
kernel.CreateJobObjectW.restype = ctypes.c_void_p
kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_uint32]
kernel.SetInformationJobObject.restype = ctypes.c_int
kernel.GetCurrentProcess.argtypes = []
kernel.GetCurrentProcess.restype = ctypes.c_void_p
kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
kernel.AssignProcessToJobObject.restype = ctypes.c_int
job = kernel.CreateJobObjectW(None, None)
if not job:
    raise SystemExit(2)
limits = ExtendedLimits()
limits.basic.process_time = limits.basic.job_time = 5 * 10_000_000
# Process CPU, job CPU, one active process, per-process/job committed memory,
# and terminate the job when its last handle closes.
limits.basic.flags = 0x2 | 0x4 | 0x8 | 0x100 | 0x200 | 0x2000
limits.basic.active_processes = 1
limits.process_memory = limits.job_memory = 256 * 1024 * 1024
if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
    raise SystemExit(2)
if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
    raise SystemExit(2)
import json
import warnings
warnings.simplefilter('ignore')
payload = sys.stdin.buffer.read(3100001)
if len(payload) > 3100000:
    raise SystemExit(2)
request = json.loads(payload)
checks = []
for row in request['files']:
    passed = True
    try:
        compile(row['content'], row['path'], 'exec', dont_inherit=True, optimize=0)
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError, OverflowError):
        passed = False
    checks.append({'path': row['path'], 'passed': passed})
receipt = {key: request[key] for key in ('schema_version', 'job_id', 'device_id',
    'worker_digest', 'request_digest')}
receipt.update(state='completed', checks=checks)
data = json.dumps(receipt, separators=(',', ':')).encode('utf-8')
if len(data) > 8192:
    raise SystemExit(2)
sys.stdout.buffer.write(data)
'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()


def _interpreter_identity():
    paths = {Path(sys.executable).resolve(), Path(getattr(sys, '_base_executable',
        sys.executable)).resolve()}
    if sys.platform == 'win32':
        # The parser/compiler lives in this library, not the venv launcher.
        paths.add(Path(sys.base_prefix) / f'python{sys.version_info.major}{sys.version_info.minor}.dll')
    binaries = []
    for path in sorted(paths, key=str):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        binaries.append(dict(path=str(path), sha256=digest))
    return dict(version=sys.version, implementation=sys.implementation.name,
        cache_tag=sys.implementation.cache_tag, binaries=binaries)


def _compiler_executable():
    # A Windows venv executable is a redirector. Waiting for that launcher on
    # timeout does not guarantee its interpreter has finished exiting. This
    # stdlib-only, isolated child must be the directly owned process instead.
    return str(Path(getattr(sys, '_base_executable', None) or sys.executable).resolve())


def local_target():
    """Stable host identity, bound to actual compiler code and interpreter files.

    Host renames, interpreter replacement and compiler changes invalidate exact
    approvals through the returned identity/configuration hashes. These hashes
    are local consistency bindings, not remote authentication certificates.
    """
    name = platform.node() or 'This computer'
    device = 'local-' + _digest(dict(host=name, system=platform.system()))[:32]
    if sys.platform == 'linux':
        from .remote_syntax_worker import _CHILD_CODE
        code = _CHILD_CODE
    else:
        code = _WINDOWS_CHILD_CODE
    digest = _digest(dict(schema_version=1, child=code, platform=sys.platform,
        launch=dict(executable=_compiler_executable(), contract='direct-interpreter-v1'),
        interpreter=_interpreter_identity()))
    target = dict(account_id='local', device_id=device, device_name=name[:160],
        worker_digest=digest, online=sys.platform in ('win32', 'linux'))
    return {**target, 'configuration_digest': _digest(dict(target=target,
        reserve_ram_gib=2.0, process_limit_gib=.25, reserve_disk_gib=.5,
        cpu_ceiling_percent=70, external_power_required=True))}


def _bind(request, target):
    req = normalize_request(request)
    if any(req[key] != target[key] for key in ('device_id', 'worker_digest')):
        raise ValueError('The exact request does not match this local worker.')
    return req


def _number(value, *, maximum=None):
    try:
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            if maximum is None or value <= maximum:
                return value
    except OverflowError:
        pass
    return None


def local_admission(request=None, *, probe=None, clock=time.time):
    """Return the resource-admission shape used by the authenticated worker.

    A local sample needs no remote signature. An injected probe is a test seam;
    production reads the actual temporary-file volume and native system state.
    Every call samples fresh; the two short CPU samples start no polling thread.
    An injected cached/stale sample retains its original timestamp and defers.
    """
    target = local_target()
    if request is not None:
        _bind(request, target)
    reasons = []
    if not target['online']:
        reasons.append('Local syntax limits are unsupported on this platform.')
    try:
        if probe is None:
            probe = ResourceProbe(tempfile.gettempdir())
            probe.snapshot(force=True)
            time.sleep(.1)
        raw = probe.snapshot(force=True)
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    observations = dict(enabled=target['online'],
        available_ram_gib=_number(raw.get('available_ram_gib')),
        free_disk_gib=_number(raw.get('free_disk_gib')),
        cpu_percent=_number(raw.get('cpu_percent'), maximum=100),
        battery_percent=_number(raw.get('battery_percent'), maximum=100),
        plugged_in=raw.get('plugged_in') if type(raw.get('plugged_in')) is bool else None)
    observed = None
    try:
        sampled = datetime.fromisoformat(raw.get('observed_at', ''))
        if sampled.tzinfo is not None:
            observed = _number(sampled.timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    now = _number(clock())
    expires = observed + 30 if observed is not None else None
    if now is None or observed is None or not observed <= now < expires:
        reasons.append('Local resource observations are missing, stale or future-dated.')
    for field, minimum, reason in (
        ('available_ram_gib', 2.25, 'Keep 2 GiB free for this computer plus the 256 MiB compiler allowance.'),
        ('free_disk_gib', .5, 'Keep at least 0.5 GiB free on the local temporary-file volume.')):
        value = observations[field]
        if value is None or value < minimum:
            reasons.append(reason)
    if observations['cpu_percent'] is None or observations['cpu_percent'] > 70:
        reasons.append('Local CPU use is unknown or exceeds 70 percent.')
    if observations['plugged_in'] is not True:
        reasons.append('Confirmed external power is required for local syntax work.')
    return dict(decision='defer' if reasons else 'ready', reasons=reasons,
        authenticated=True, observed_at=observed, expires_at=expires,
        observations=observations)


def _failed(req):
    return {**{key: req[key] for key in ('schema_version', 'job_id', 'device_id',
        'worker_digest', 'request_digest')}, 'state': 'failed',
        'checks': [dict(path=row['path'], passed=False) for row in req['files']]}


def execute_local_syntax(request):
    """Compile one approved, durably claimed request; never execute its source.

    Windows hard CPU/memory limits are installed in the isolated child before
    reading input; unavailable limits fail closed. The parent enforces the wall
    deadline and waits for a timed-out child to exit. Output is bounded and must
    match the complete normalized request. No source is written to disk.
    """
    if sys.platform not in ('win32', 'linux'):
        raise RuntimeError('Local syntax limits are not supported on this platform.')
    req = _bind(request, local_target())
    if sys.platform == 'linux':
        from .remote_syntax_worker import execute_linux_syntax
        return execute_linux_syntax(req)
    payload = json.dumps(req, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > 3100000:
        raise ValueError('Encoded syntax request exceeds its input bound.')
    # Keep only the Windows loader environment; -I -S ignores user Python paths
    # and startup customization. Source is passed only through the input pipe.
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}
    with tempfile.TemporaryDirectory(prefix='prometheus-local-syntax-') as cwd:
        with tempfile.TemporaryFile() as output:
            try:
                child = subprocess.run([_compiler_executable(), '-I', '-S', '-B', '-c', _WINDOWS_CHILD_CODE],
                    input=payload, stdout=output, stderr=subprocess.DEVNULL, cwd=cwd,
                    env=env, timeout=req['timeout_seconds'], close_fds=True, check=False,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                return _failed(req)
            if child.returncode:
                return _failed(req)
            output.seek(0)
            data = output.read(8193)
    if len(data) > 8192:
        raise ValueError('Local syntax receipt exceeds its output bound.')
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise ValueError('The local syntax process returned an incomplete receipt.') from None
    if validate_receipt(req, result)['status'] == 'incomplete':
        raise ValueError('The local syntax process returned an invalid receipt.')
    return result
