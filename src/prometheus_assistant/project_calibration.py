"""Local, version-bound calibration identity; never stores project paths or commands."""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import os
import platform
import sys

METRICS = ('direct_ram_gib', 'direct_cpu_seconds', 'job_cpu_seconds')


@lru_cache(maxsize=64)
def _digest(path, size, modified):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(path):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    return _digest(str(path), stat.st_size, stat.st_mtime_ns)


def scope(candidate, policy):
    """Versioned narrow family: same host, project/checks, size and stdlib runtime."""
    # UI workspaces use this same resource discovery. A bundled interpreter
    # may differ from the host running admission; do not apply host-qualified
    # corrections before that different interpreter executes.
    from .hardware import resource_root
    resources = resource_root()
    if resources:
        bundled = Path(resources).resolve() / 'Python/python.exe'
        if bundled.is_file() and bundled.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
            return None
    checks = candidate.get('checks', [])
    if not checks or any(c.get('preset') not in ('python-compile', 'python-unittest') for c in checks):
        return None
    items = candidate.get('candidates', [candidate])
    size = sum(len(c.get('content', '').encode('utf-8')) for c in items)
    root = Path(__file__).parent
    files = ['project_calibration.py', 'prediction_calibration.py', 'resource_scheduler.py',
             'project_resources.py', 'coding_workspace.py', 'project_check_metrics.py', 'project_job_metrics.py']
    identity = dict(version='project-calibration-v1',
        host=[platform.node(), platform.system(), platform.machine(), platform.processor(), os.cpu_count()],
        runtime=[sys.version, digest(sys.executable)],
        implementation={name: digest(root/name) for name in files}, policy=policy,
        project=str(Path(items[0]['project']).resolve()), checks=checks,
        size_bucket=max(1,size).bit_length())
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def outcome(result, direct, job):
    checks = result.get('checks', [])
    if any(row.get('cancelled') is True for row in checks):return 'cancelled'
    if any(row.get('timed_out') is True for row in checks):return 'timed_out'
    if result.get('status') != 'passed':return 'failed'
    if not direct.get('recorded'):return 'unavailable'
    # Only the same native interpreter qualifies the forecast family.
    for row in checks:
        argv = row.get('argv')
        if not isinstance(argv,list) or not argv:return 'unavailable'
        try:
            if Path(argv[0]).resolve(strict=True) != Path(sys.executable).resolve(strict=True):return 'unavailable'
        except (OSError,TypeError,ValueError):return 'unavailable'
    return 'completed'
