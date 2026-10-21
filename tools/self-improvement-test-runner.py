"""Fixed full regression contract for a staged application candidate.

Invoke the reviewed host copy, never the candidate's copy of this script.
No package installation, model inference, microphone or promotion is performed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile


MAX_FILES = 4096
MAX_BYTES = 512 * 1024 * 1024
MAX_LOG = 16 * 1024 * 1024
DEADLINE_SECONDS = 1800
CHECK_IDS = ('python-regression', 'javascript-regression')
BUNDLE_HASHES = {
    'LOCK.json': '36aea147b496422e6c2ab91d91414f072c12b5c2489971b00755bf14efcaaa2d',
    'node-guard.cjs': '677ce4f9d6088ed0ce4b65a673b61661a3f3f55bf5627bb5646f37c999bef48b',
    'python-bootstrap.py': '63dbc0b73e40385c50e25619e33b76b31d8d573457ecfde070a9121f7465d559',
    'sitecustomize.py': '4de9ffc82e103c85f9c0a4cb4dee313666c4c94ec1d317e0f9189517bbedaef1',
    'wheels/colorama-0.4.6-py2.py3-none-any.whl': '4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6',
    'wheels/iniconfig-2.3.1-py3-none-any.whl': '9121e2c1fdb355232495be3194c8dfe87ccc2d5dee45947b78e68f499790d7a7',
    'wheels/packaging-26.3-py3-none-any.whl': 'd7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c',
    'wheels/pluggy-1.6.0-py3-none-any.whl': 'e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746',
    'wheels/pygments-2.21.0-py3-none-any.whl': '2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9',
    'wheels/pytest-9.1.1-py3-none-any.whl': '37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode()


def plain(path):
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked paths and reparse points are not allowed.')


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError('Invalid payload path.')
    value = value.replace('\\', '/')
    if PurePosixPath(value).is_absolute() or any(not part or part in {'.', '..'}
            or ':' in part or part.endswith(('.', ' ')) or any(ord(char) < 32 for char in part)
            for part in value.split('/')):
        raise ValueError('Unsafe payload path.')
    return value


def read(path, limit=64 * 1024 * 1024):
    plain(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing or oversized input: ' + str(path))
    with path.open('rb') as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Input exceeds its size limit.')
    return body


def tree(root):
    plain(root)
    result, total, count = {}, 0, 0
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            count += 1
            if count > MAX_FILES * 3:
                raise ValueError('Payload tree has too many entries.')
            plain(Path(current) / name)
        for name in files:
            path = Path(current) / name
            body = read(path)
            total += len(body)
            if total > MAX_BYTES or len(result) >= MAX_FILES:
                raise ValueError('Payload exceeds the bounded package limit.')
            key = path.relative_to(root).as_posix()
            if key.casefold() in {name.casefold() for name in result}:
                raise ValueError('Case-colliding payload names.')
            result[key] = digest(body)
    return result


def payloads(baseline, candidate):
    body = read(baseline / 'SHA256-MANIFEST.json', 1_000_000)
    rows = json.loads(body.decode('utf-8-sig'))
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_FILES:
        raise ValueError('The signed baseline must include a bounded manifest.')
    expected, total = {}, 0
    for row in rows:
        name = relative(row['path'])
        if name.casefold() in {key.casefold() for key in expected}:
            raise ValueError('Duplicate manifest path.')
        data = read(baseline / name)
        total += len(data)
        if total > MAX_BYTES or digest(data) != str(row['sha256']).lower():
            raise ValueError('Baseline payload does not match its manifest.')
        expected[name] = digest(data)
    actual = tree(candidate)
    if set(actual) != set(expected):
        raise ValueError('Candidate must contain exactly the unsigned manifest payload.')
    # Only the application implementation may differ; tests, test configuration,
    # tools, fixtures, pinned runtime wheels and dependencies are immutable.
    for name in expected:
        if not name.startswith(('src/prometheus_assistant/', 'src/prometheus_control_center/')) and actual[name] != expected[name]:
            raise ValueError('Candidate altered a baseline oracle/tool: ' + name)
    for required in ('tests/conftest.py', 'tools/maps-studio/test_studio_backend.py',
                     'tools/maps-studio/test_studio_ui.cjs', 'pyproject.toml'):
        if required not in expected:
            raise ValueError('Baseline lacks its full mandatory test contract: ' + required)
    ui = sorted(name for name in expected if re.fullmatch(r'tests/ui/test-[^/]+\.cjs', name))
    if not ui or not any(name.startswith('tests/test_') and name.endswith('.py') for name in expected):
        raise ValueError('Baseline has no complete Python/interface regression suite.')
    return digest(body), expected, actual, ui


def extract_runtime(bundle, destination):
    for name, expected in BUNDLE_HASHES.items():
        if digest(read(bundle / name)) != expected:
            raise ValueError('Trusted test bundle pin mismatch: ' + name)
    if not BUNDLE_HASHES:
        raise ValueError('Trusted test runtime is not pinned.')
    lock = json.loads(read(bundle / 'LOCK.json'))
    expected_wheels = {name for name in BUNDLE_HASHES if name.startswith('wheels/')}
    if {'wheels/' + row['filename'] for row in lock['wheels']} != expected_wheels:
        raise ValueError('Offline wheel lock differs from the trusted runner pins.')
    destination.mkdir()
    total = 0
    for row in lock['wheels']:
        name = 'wheels/' + relative(row['filename'])
        wheel = bundle / name
        if row['sha256'] != BUNDLE_HASHES[name] or wheel.stat().st_size != row['size']:
            raise ValueError('Offline wheel provenance mismatch.')
        with zipfile.ZipFile(wheel) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                relative_name = relative(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('Linked wheel entry refused.')
                total += entry.file_size
                if total > 32 * 1024 * 1024 or entry.file_size > 10 * 1024 * 1024:
                    raise ValueError('Wheel extraction exceeds its bounded limit.')
                target = destination / relative_name
                if target.exists():
                    raise ValueError('Overlapping wheel payload.')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))


def host_temp_root(storage):
    """Use the OS user temp location, independent of portable AppData overrides."""
    if os.name == 'nt':
        folder = ctypes.create_unicode_buffer(32768)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.SHGetFolderPathW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p]
        shell.SHGetFolderPathW.restype = ctypes.c_long
        if shell.SHGetFolderPathW(None, 0x001C, None, 0, folder) != 0:
            raise OSError('Cannot locate the host-local test cache.')
        root = Path(folder.value) / 'Temp/PrometheusChecks'
    else:
        root = Path(tempfile.gettempdir()) / 'PrometheusChecks'
    plain(root)
    if root.is_relative_to(storage) or len(str(root)) > 130:
        raise ValueError('A short host-local test directory is required.')
    root.mkdir(parents=True, exist_ok=True)
    return root


class WindowsJob:
    """Kill owned descendants when the suite ends, including on cancellation."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                ('active_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle))):
            self.close()
            process.kill()
            process.wait(timeout=10)
            raise OSError('Could not bind the test process to an owned cleanup job.')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def stopped(cancel_file, deadline):
    return 'cancelled' if cancel_file is not None and cancel_file.exists() else 'timed_out' if time.monotonic() >= deadline else ''


def run_command(command, cwd, env, log, cancel_file, deadline):
    reason = stopped(cancel_file, deadline)
    if reason:
        return {'executed': False, 'exit_code': -1, 'state': reason, 'seconds': 0}
    started = time.monotonic()
    process = None
    job = None
    with log.open('wb') as stream:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                start_new_session=os.name != 'nt')
            job = WindowsJob(process)
            while process.poll() is None:
                reason = stopped(cancel_file, deadline)
                if log.stat().st_size > MAX_LOG:
                    reason = 'output_limit'
                if reason:
                    if os.name == 'nt':
                        subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                            '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=15)
                    break
                time.sleep(.1)
            return {'executed': True, 'exit_code': process.returncode if not reason else -1,
                    'state': reason or 'completed', 'seconds': round(time.monotonic() - started, 3)}
        finally:
            if job is not None:
                job.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def log_fields(path):
    body = read(path, MAX_LOG + 1024 * 1024) if path.exists() else b''
    return {'log_path': str(path), 'log_sha256': digest(body),
            'output_tail': body[-8000:].decode('utf-8', errors='replace')}


def python_counts(path):
    if not path.is_file():
        return {'tests_run': 0, 'skipped': 0, 'skip_reasons': []}
    root = ET.fromstring(read(path, 16 * 1024 * 1024))
    cases = list(root.iter('testcase'))
    skips = [case.find('skipped') for case in cases if case.find('skipped') is not None]
    return {'tests_run': len(cases) - len(skips), 'skipped': len(skips),
            'skip_reasons': [item.attrib.get('message', item.text or '') for item in skips]}


def execute(candidate, baseline, report_path, cancel_file=None):
    deadline = time.monotonic() + DEADLINE_SECONDS
    candidate, baseline, report_path = (Path(os.path.abspath(path)) for path in (candidate, baseline, report_path))
    for path in (candidate, baseline, report_path):
        plain(path)
    storage = baseline.parent
    if report_path.is_relative_to(storage) or candidate == baseline:
        raise ValueError('Test reports/workspaces must be host-local and the candidate must be separate.')
    resources = storage / 'Prometheus-Resources'
    python = resources / 'Python' / ('python.exe' if os.name == 'nt' else 'bin/python3')
    node = resources / 'Coding/Ollama-Tools-20261009/runtime/node-v24.21.0-win-x64/node.exe'
    if os.name != 'nt':
        raise ValueError('This portable test runtime is qualified for Windows; another OS needs a qualified runtime.')
    for runtime in (python, node):
        plain(runtime)
        if not runtime.is_file():
            raise ValueError('Required portable test runtime is missing: ' + str(runtime))
    if report_path.exists():
        raise ValueError('Use a new test report; prior evidence is immutable.')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    evidence = report_path.parent / ('test-evidence-' + uuid.uuid4().hex)
    evidence.mkdir()
    report = {'schema': 1, 'state': 'running', 'checks': [
        {'id': name, 'executed': False, 'exit_code': -1, 'tests_run': 0} for name in CHECK_IDS]}
    temp_root = host_temp_root(storage)
    scratch = Path(tempfile.mkdtemp(prefix='run-', dir=temp_root))
    try:
        report['runner_sha256'] = digest(read(Path(__file__).resolve()))
        baseline_manifest, baseline_before, candidate_before, ui_scripts = payloads(baseline, candidate)
        report.update(baseline_manifest_sha256=baseline_manifest,
            baseline_fingerprint=digest(encoded(baseline_before)), candidate_fingerprint=digest(encoded(candidate_before)))
        workspace = scratch / 'workspace'
        shutil.copytree(candidate, workspace)
        if tree(workspace) != candidate_before:
            raise ValueError('Temporary candidate copy differs from its validated payload.')
        bundle = Path(__file__).resolve().with_name('self-improvement-tests')
        packages = scratch / 'python-packages'
        extract_runtime(bundle, packages)
        env = {key: value for key, value in os.environ.items() if key not in {
            'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'NODE_OPTIONS'}}
        for variable, folder in [('TEMP', 'tmp'), ('TMP', 'tmp'), ('LOCALAPPDATA', 'localappdata'),
                ('APPDATA', 'appdata'), ('PROMETHEUS_HOME', 'home'), ('PROMETHEUS_RESOURCE_ROOT', 'resources')]:
            path = scratch / folder
            path.mkdir(exist_ok=True)
            env[variable] = str(path)
        guard_log = evidence / 'isolation-events.jsonl'
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
            PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PROMETHEUS_PYTHON=str(python),
            PYTHONPATH=os.pathsep.join([str(bundle), str(packages), str(workspace / 'src')]),
            PROMETHEUS_TEST_GUARD_LOG=str(guard_log), PROMETHEUS_TEST_STORAGE_ROOT=str(storage),
            PROMETHEUS_TEST_SCRATCH=str(scratch))
        xml = evidence / 'python.xml'
        log = evidence / 'python.log'
        command = [str(python), '-I', '-B', str(bundle / 'python-bootstrap.py'), str(bundle),
            str(packages), str(workspace), 'tests', 'tools/maps-studio', '-q', '--tb=short',
            '--basetemp', str(scratch / 'pytest-temp'), '-o', 'cache_dir=' + str(scratch / 'pytest-cache'),
            '--junitxml', str(xml)]
        report['checks'][0].update(run_command(command, workspace, env, log, cancel_file, deadline))
        report['checks'][0].update(python_counts(xml), **log_fields(log))
        script_receipts = []
        for index, name in enumerate([*ui_scripts, 'tools/maps-studio/test_studio_ui.cjs']):
            path = workspace / name
            log = evidence / ('javascript-' + str(index) + '.log')
            command = [str(node), '--permission', '--allow-fs-read=' + str(workspace),
                '--allow-fs-read=' + str(bundle), '--allow-fs-write=' + str(scratch / 'tmp'),
                '--require', str(bundle / 'node-guard.cjs'), str(path)]
            result = run_command(command, workspace, env, log, cancel_file, deadline)
            result.update(script=name, **log_fields(log))
            if result['exit_code'] == 0:
                result['output_tail'] = ''
            script_receipts.append(result)
            if result['state'] in {'cancelled', 'timed_out'}:
                break
        javascript_log = evidence / 'javascript.json'
        javascript_log.write_bytes(encoded(script_receipts))
        report['checks'][1].update(executed=any(item['executed'] for item in script_receipts),
            exit_code=0 if len(script_receipts) == len(ui_scripts) + 1 and all(item['exit_code'] == 0 for item in script_receipts) else -1,
            tests_run=sum(item['executed'] for item in script_receipts), count_unit='test scripts',
            scripts=script_receipts, output_tail='\n'.join(item['output_tail'] for item in script_receipts if item['exit_code'] != 0)[-8000:],
            log_path=str(javascript_log), log_sha256=digest(encoded(script_receipts)))
        manifest_after, baseline_after, candidate_after, _ = payloads(baseline, candidate)
        invariants = (manifest_after == baseline_manifest and baseline_before == baseline_after
                      and candidate_before == candidate_after and tree(workspace) == candidate_before)
        guard_clean = not guard_log.exists() or guard_log.stat().st_size == 0
        runtime_unchanged = (digest(read(Path(__file__).resolve())) == report['runner_sha256'] and all(
            digest(read(bundle / name)) == expected for name, expected in BUNDLE_HASHES.items()))
        report.update(payloads_unchanged=invariants, isolation_clean=guard_clean,
            trusted_runtime_unchanged=runtime_unchanged,
            baseline_fingerprint_after=digest(encoded(baseline_after)), candidate_fingerprint_after=digest(encoded(candidate_after)))
        for check in report['checks']:
            check.update(baseline_fingerprint=report['baseline_fingerprint'], candidate_fingerprint=report['candidate_fingerprint'])
            if not invariants or not guard_clean or not runtime_unchanged:
                check['exit_code'] = -1
        report['state'] = stopped(cancel_file, deadline) or ('passed' if invariants and guard_clean and runtime_unchanged and all(
            item['executed'] and item['exit_code'] == 0 and item['tests_run'] > 0 for item in report['checks']) else 'failed')
    except Exception as exc:
        report.update(state='failed', error=str(exc)[:8000])
        for check in report['checks']:
            check['exit_code'] = -1
    finally:
        # Resolved target is verified before recursive removal; evidence remains.
        plain(scratch)
        if scratch.parent != temp_root or not scratch.name.startswith('run-'):
            raise ValueError('Unsafe scratch cleanup target.')
        shutil.rmtree(scratch)
        report_path.write_bytes(encoded(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cancel-file', type=Path)
    args = parser.parse_args()
    try:
        report = execute(args.candidate, args.baseline, args.report, args.cancel_file)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'state': report['state'], 'report': str(args.report)}, sort_keys=True))
    return 0 if report['state'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
