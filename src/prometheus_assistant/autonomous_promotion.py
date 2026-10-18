"""Host-owned promotion of one exact, tested, owner-approved application edit.

The application approval callback queues this driver only after consuming an
action-specific owner approval. Candidate text never selects commands, signing
keys, host paths, regression checks or trust anchors. The detached worker uses
host configuration and keeps prior signed releases and personal data intact.
"""
from __future__ import annotations

if __name__ == '__main__' and not __package__:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'prometheus_assistant'

import ast
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tomllib
from urllib.request import ProxyHandler, build_opener

from .coding_workspace import _no_links, _write_atomic
from .self_improvement import _eligible, _json_bytes, _read, _relative, _strict_json, fingerprint

REQUIRED_CHECKS = ('python-regression', 'javascript-regression')
_HASH = re.compile(r'[a-f0-9]{64}')
_RELEASE = re.compile(r'Prometheus-v[\w.-]{1,150}')
_SIGNATURES = {'SHA256-MANIFEST.json', 'SHA256-MANIFEST.sig', 'MANIFEST-PUBLIC.cer'}


def _validate_installed_health(result):
    """Reconcile sparse health counters with the actual required check results."""
    required = {'release_metadata', 'code_manifest', 'memory_database',
                'runtime', 'model_files', 'model_ram'}
    statuses = {'pass', 'fail', 'blocked', 'untested'}
    error = 'Candidate installed health checks failed.'
    if not isinstance(result, dict):
        raise ValueError(error)
    counts, checks = result.get('counts'), result.get('checks')
    if not isinstance(counts, dict) or set(counts) - statuses or not isinstance(checks, list):
        raise ValueError(error)
    actual, seen, passed = Counter(), set(), set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError(error)
        identity, status = check.get('id'), check.get('status')
        if (not isinstance(identity, str) or not identity or identity in seen
                or not isinstance(status, str) or status not in statuses):
            raise ValueError(error)
        seen.add(identity)
        actual[status] += 1
        if status == 'pass':
            passed.add(identity)
    # The producer uses Counter, so absent statuses mean zero, not missing evidence.
    for status in statuses:
        value = counts.get(status, 0)
        if type(value) is not int or value < 0 or value != actual[status]:
            raise ValueError(error)
    if not required <= passed or actual['fail'] or actual['blocked']:
        raise ValueError(error)


def sha(path):
    return hashlib.sha256(_read(Path(path))).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _owned_test_process(process):
    """Cleanup ownership for test children; this is not a security sandbox."""
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),
            ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',wintypes.DWORD),
            ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('reads','writes','other','read_bytes','write_bytes','other_bytes')]
    class Limits(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
            ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];kernel.SetInformationJobObject.restype=wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    job=kernel.CreateJobObjectW(None,None);limits=Limits();limits.basic.flags=0x2000
    try:
        if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job,wintypes.HANDLE(int(process._handle))):
            raise OSError('Cannot establish owned test-process cleanup.')
        yield
    finally:
        if job:kernel.CloseHandle(job)
        if process.poll() is None:process.terminate()
        process.wait(timeout=15)


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('Expected an exact SHA-256 digest.')
    return value


def _save(path, value):
    _write_atomic(Path(path), _json_bytes(value))


def _checks(rows):
    if not isinstance(rows, list) or len(rows) != len(REQUIRED_CHECKS):
        raise ValueError('Both fixed regression suites must execute.')
    if {row.get('id') for row in rows if isinstance(row, dict)} != set(REQUIRED_CHECKS):
        raise ValueError('Mandatory regression suite identity changed.')
    for row in rows:
        if (row.get('executed') is not True or type(row.get('exit_code')) is not int
                or row['exit_code'] != 0 or type(row.get('tests_run')) is not int
                or row['tests_run'] < 1):
            raise ValueError('Mandatory regression tests did not execute and pass.')
    return rows


def payload(root, expected):
    """Bounded exact tree check, including unexpected files and reparse paths."""
    root = Path(root)
    _no_links(root)
    if not isinstance(expected, dict) or not 1 <= len(expected) <= 4096:
        raise ValueError('Invalid candidate payload map.')
    seen, total = {}, 0
    for name, digest in expected.items():
        if _relative(name) != name or name.casefold() in seen:
            raise ValueError('Duplicate or noncanonical payload path.')
        seen[name.casefold()] = name
        _hash(digest)
    actual, count = {}, 0
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            count += 1
            if count > 12288:
                raise ValueError('Candidate tree exceeds the entry limit.')
            _no_links(Path(folder) / name)
        for name in files:
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if relative not in expected:
                raise ValueError('Unreviewed candidate file: ' + relative)
            body = _read(path)
            total += len(body)
            if total > 512 * 1024 * 1024:
                raise ValueError('Candidate exceeds the release size limit.')
            actual[relative] = hashlib.sha256(body).hexdigest()
    if actual != expected:
        raise ValueError('Candidate bytes differ from the approved and tested payload.')
    return actual


def baseline_payload(root, digest):
    manifest = _read(Path(root) / 'SHA256-MANIFEST.json', 1_000_000)
    if hashlib.sha256(manifest).hexdigest() != _hash(digest):
        raise ValueError('Signed baseline manifest changed.')
    rows = json.loads(manifest.decode('utf-8-sig'))
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
        raise ValueError('Invalid baseline manifest.')
    result, seen, total = {}, set(), 0
    for row in rows:
        name = _relative(row.get('path'))
        if name.casefold() in seen or Path(name).name.casefold() in {x.casefold() for x in _SIGNATURES}:
            raise ValueError('Duplicate or reserved manifest member.')
        body = _read(Path(root) / name)
        total += len(body)
        if total > 512 * 1024 * 1024 or type(row.get('size_bytes')) is not int or len(body) != row['size_bytes'] or hashlib.sha256(body).hexdigest() != _hash(row.get('sha256')):
            raise ValueError('Baseline payload integrity failed.')
        result[name] = row['sha256']; seen.add(name.casefold())
    return result


def validate_approval(candidate, baseline, receipt):
    """Integrity proof; owner authentication remains the caller's authority."""
    if not isinstance(receipt, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(receipt.get('job_id', ''))):
        raise ValueError('Invalid promotion job.')
    if receipt.get('state') != 'promotion_requested':
        raise ValueError('Promotion requires the engine-consumed approval state.')
    if receipt.get('approval_fingerprint') != fingerprint(receipt):
        raise ValueError('Approval no longer matches the exact change and test results.')
    _checks(receipt.get('checks'))
    before = baseline_payload(baseline, receipt['baseline_manifest_sha256'])
    after = payload(candidate, receipt.get('payload_sha256s'))
    target = receipt.get('target')
    if set(before) != set(after) or [name for name in before if before[name] != after[name]] != [target] or not _eligible(target):
        raise ValueError('Promotion is limited to the approved one-file implementation edit.')
    diff = ''.join(difflib.unified_diff(
        _read(Path(baseline) / target).decode('utf-8').splitlines(keepends=True),
        _read(Path(candidate) / target).decode('utf-8').splitlines(keepends=True),
        fromfile='before/' + target, tofile='after/' + target))
    if diff != receipt.get('diff'):
        raise ValueError('The owner-reviewed diff does not describe the candidate bytes.')
    return after


def _copy_payload(source, destination, expected):
    destination.mkdir(parents=True, exist_ok=False)
    for name, digest in expected.items():
        body = _read(Path(source) / name)
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('Payload changed during snapshot copy.')
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    payload(destination, expected)


def package(snapshot, baseline, receipt, checks, destination):
    """Only deterministic release metadata is added after exact source tests."""
    expected = validate_approval(snapshot, baseline, receipt)
    _checks(checks)
    _copy_payload(snapshot, destination, expected)
    version = tomllib.loads(_read(destination / 'pyproject.toml').decode('utf-8-sig'))['project']['version']
    if not isinstance(version, str) or not re.fullmatch(r'[0-9A-Za-z.+-]{1,60}', version):
        raise ValueError('Invalid package version.')
    syntax = ast.parse(_read(destination / 'src/prometheus_control_center/__init__.py').decode('utf-8-sig'))
    declared = [ast.literal_eval(node.value) for node in syntax.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets)]
    if declared != [version]:
        raise ValueError('Python package and project versions disagree.')
    release = 'Prometheus-v' + version.replace('.dev', '-dev') + '-self-' + receipt['job_id'][:12]
    if not _RELEASE.fullmatch(release):
        raise ValueError('Invalid generated release identity.')
    provenance = _strict_json(_read(destination / 'RELEASE-PROVENANCE.json').decode('utf-8-sig'))
    provenance.update(release=release, base_release=Path(baseline).name,
                      base_manifest_sha256=receipt['baseline_manifest_sha256'])
    provenance['self_improvement'] = {'job_id':receipt['job_id'], 'approval_fingerprint':receipt['approval_fingerprint'],
        'target':receipt['target'], 'goal':receipt['goal'], 'reason':receipt['reason'],
        'source_kind':'owner-approved patch to the signed baseline', 'tests':checks}
    _save(destination / 'RELEASE-PROVENANCE.json', provenance)
    _save(destination / 'BUILD-PROVENANCE.json', {'release':release, 'version':version,
        'built_utc':now(), 'baseline_release':Path(baseline).name,
        'baseline_manifest_sha256':receipt['baseline_manifest_sha256'],
        'approval_fingerprint':receipt['approval_fingerprint'], 'reviewed_diff':receipt['diff'],
        'tested_payload_sha256s':expected, 'checks':checks,
        'personal_data_included':False, 'private_keys_included':False})
    rows = [{'path':p.relative_to(destination).as_posix(), 'size_bytes':p.stat().st_size, 'sha256':sha(p)}
            for p in sorted(destination.rglob('*')) if p.is_file()]
    _save(destination / 'SHA256-MANIFEST.json', rows)
    return {'release':release, 'version':version, 'manifest_sha256':sha(destination / 'SHA256-MANIFEST.json')}


def add_guard_pin(original, release, digest, recovery):
    if not _RELEASE.fullmatch(release) or _relative(recovery) != recovery:
        raise ValueError('Invalid exact release/recovery tuple.')
    _hash(digest)
    needle = b'        supported = {\n'
    if original.count(needle) != 1 or ("'" + release + "'").encode() in original:
        raise ValueError('Guard layout changed or release is already pinned.')
    entry = ("            '"+release+"': (\n                '"+digest+"',\n                '"+recovery+"',\n            ),\n").encode()
    updated = original.replace(needle, needle + entry)
    if updated.replace(entry, b'') != original:
        raise ValueError('Guard update exceeded the additive tuple.')
    ast.parse(updated)
    return updated


def promote_snapshot(host, snapshot, baseline, receipt, job):
    """Serial coordinator. Real host owns signing, process identity and locking."""
    job = Path(job)
    state = {'job_id':receipt.get('job_id'), 'state':'validating', 'utc_started':now(),
             'personal_data_overwritten':False, 'forced_application_stop':False}
    def record(phase, **details):
        state.update(state=phase, **details); _save(job / 'result.json', state)
    switched = stopped = False
    previous = None
    built = None
    try:
        host.verify(baseline, receipt['baseline_manifest_sha256'])
        expected = validate_approval(snapshot, baseline, receipt)
        record('retesting')
        checks = _checks(host.rerun_checks(snapshot, baseline, job)['checks'])
        payload(snapshot, expected)
        host.verify(baseline, receipt['baseline_manifest_sha256'])
        built = package(snapshot, baseline, receipt, checks, job / 'signed-build')
        record('signing', release=built['release'], manifest_sha256=built['manifest_sha256'])
        host.sign(job / 'signed-build', built['manifest_sha256'])
        host.verify(job / 'signed-build', built['manifest_sha256'])
        staged = host.stage(job / 'signed-build', built, job)
        previous = host.capture_selection(baseline, receipt['baseline_manifest_sha256'])
        updated_guard = add_guard_pin(previous['guard'], built['release'], built['manifest_sha256'], staged['recovery'])
        record('waiting_for_shutdown')
        host.stop(baseline)
        stopped = True
        with host.lock():
            host.assert_selection(previous)
            host.verify(baseline, receipt['baseline_manifest_sha256'])
            host.verify(staged['release_path'], built['manifest_sha256'])
            host.backup(job)
            # From this point every failure attempts restoration under the lock.
            switched = True
            host.select(previous, updated_guard, built, receipt)
            host.health(staged['release_path'], job)
        record('starting')
        host.launch(job, 'new')
        host.ready(staged['release_path'])
        host.trust_guard(updated_guard)
        record('active', utc_finished=now(), previous_release=Path(baseline).name,
               approval_fingerprint=receipt['approval_fingerprint'])
        return state
    except Exception as error:
        record('failed', error=str(error)[:2000])
        if stopped:
            try:
                if switched:
                    # Also handles a launcher that raised after starting its child.
                    host.stop(host.ssd / built['release'])
                    with host.lock(accepted_guard_bytes=(previous['guard'], updated_guard)):
                        host.restore_selection(previous)
                        host.verify(baseline, receipt['baseline_manifest_sha256'])
                    host.trust_guard(previous['guard'])
                host.launch(job, 'rollback')
                host.ready(baseline)
                record('rolled_back', utc_finished=now(), previous_release=Path(baseline).name)
            except Exception as recovery_error:
                record('recovery_required', recovery_error=str(recovery_error)[:2000])
        raise


class WindowsPromotionHost:
    """Concrete pinned Windows adapter, configured outside candidate/application.

    Config fields: host_root, jobs_directory, shutdown_request, guard_sha256,
    certificate_sha256, and tools{name:{path,sha256}} for host tools, or
    {ssd_relative,sha256} for portable resource tools. Tool names are python, powershell,
    signer, signing_library, verifier, launcher, test_runner and care.
    The test_runner must implement the documented candidate/baseline/report CLI
    and enforce its host sandbox; this adapter never invents candidate commands.
    """
    def __init__(self, config_path):
        self.config_path = Path(config_path).absolute()
        _no_links(self.config_path); self.config_path=self.config_path.resolve(strict=True)
        self.config_bytes = _read(self.config_path, 100_000)
        self.config = _strict_json(self.config_bytes.decode('utf-8-sig'))
        self.host_root = Path(self.config['host_root']).absolute()
        _no_links(self.host_root); self.host_root=self.host_root.resolve(strict=True)
        self.jobs = Path(self.config['jobs_directory']).absolute()
        self.shutdown = Path(self.config['shutdown_request']).absolute()
        for path in (self.config_path, self.jobs, self.shutdown):
            _no_links(path)
            if not path.is_relative_to(self.host_root):
                raise ValueError('Promotion configuration and state must stay in the configured host root.')
        self.tools = self.config['tools']
        _hash(self.config['guard_sha256']); _hash(self.config['certificate_sha256'])
        if 'ssd_relative' in self.tools['powershell']:
            raise ValueError('SSD discovery requires a pinned host PowerShell path.')
        volumes = self.run([self.tool('powershell'),'-NoProfile','-Command',
            "@([IO.DriveInfo]::GetDrives()|Where-Object {$_.IsReady -and $_.VolumeLabel -eq 'Prometheus-2TB'}|ForEach-Object {$_.Name})|ConvertTo-Json -Compress"], 15)
        roots = json.loads(volumes.stdout); roots = [roots] if isinstance(roots,str) else roots
        if len(roots) != 1:
            raise ValueError('Connect exactly one Prometheus-2TB SSD.')
        self.ssd = Path(roots[0]).resolve(strict=True)
        if self.host_root.is_relative_to(self.ssd):
            raise ValueError('Host signing configuration must not reside on the portable SSD.')
        self.guard = self.ssd / 'Prometheus-Resources/Tools/portable-memory-guard.py'
        self.marker = self.ssd / 'PROMETHEUS-SSD-STATUS.json'
        self.trust_path = self.config_path.with_name('promotion-trust.json')
        for name in ('python','powershell','signer','signing_library','verifier','launcher','test_runner','care'):
            self.tool(name)
        if self.tool('signing_library') != self.tool('signer').with_name('Prometheus-Signing.ps1'):
            raise ValueError('Pinned signing library is not the library loaded by the signer.')

    def tool(self, name):
        row = self.tools[name]
        if not isinstance(row,dict) or set(row) not in ({'path','sha256'},{'ssd_relative','sha256'}):
            raise ValueError('Host tool must have one exact path form and SHA-256.')
        if 'ssd_relative' in row:
            relative=_relative(row['ssd_relative'])
            if relative!=row['ssd_relative'] or not relative.startswith('Prometheus-Resources/'):
                raise ValueError('Portable tool must be a bounded SSD resource path.')
            path=self.ssd/relative
        else:
            path=Path(row['path'])
            if not path.is_absolute():raise ValueError('Host tool paths must be absolute.')
        _no_links(path); path=path.resolve(strict=True)
        if sha(path) != _hash(row['sha256']):
            raise ValueError('Pinned host tool changed: ' + name)
        return path

    def run(self, arguments, timeout=240, *, log=None):
        env = {key:value for key,value in os.environ.items() if key.upper() not in {'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'}}
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        result = subprocess.run(list(map(str,arguments)), capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout, env=env, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if log is not None:
            Path(log).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('Pinned host operation failed: ' + Path(str(arguments[0])).name + '; ' + result.stderr[-1000:])
        return result

    def _expected_guard(self):
        if not self.trust_path.exists():
            return self.config['guard_sha256']
        trust = _strict_json(_read(self.trust_path, 10000).decode('utf-8'))
        if trust.get('configuration_sha256') != hashlib.sha256(self.config_bytes).hexdigest():
            raise ValueError('Host promotion trust belongs to a different configuration.')
        return _hash(trust['guard_sha256'])

    def trust_guard(self, content):
        if _read(self.guard) != content:
            raise ValueError('Guard changed before trusted state was saved.')
        _save(self.trust_path, {'configuration_sha256':hashlib.sha256(self.config_bytes).hexdigest(),
                               'guard_sha256':hashlib.sha256(content).hexdigest(), 'utc':now()})

    def verify(self, root, digest):
        baseline_payload(root, digest)
        if sha(Path(root) / 'MANIFEST-PUBLIC.cer') != self.config['certificate_sha256']:
            raise ValueError('Release public trust anchor changed.')
        result = self.run([self.tool('powershell'),'-NoProfile','-File',self.tool('verifier'),'-Root',root],180)
        report = json.loads(result.stdout)
        if report.get('signature_verified') is not True or report.get('healthy') is not True or report.get('repaired') or report.get('memory_modified'):
            raise ValueError('Pinned verifier did not confirm an unchanged healthy signed release.')

    def rerun_checks(self, snapshot, baseline, job):
        report = job / 'trusted-retest.json'
        cancel = job / 'retest-cancel.request'
        arguments = [self.tool('python'),'-B',self.tool('test_runner'),'--candidate',snapshot,'--baseline',baseline,'--report',report,'--cancel-file',cancel]
        env = {key:value for key,value in os.environ.items() if key.upper() not in {'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'}}
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
        expired = False
        with (job/'retest.log').open('w',encoding='utf-8') as log:
            process = subprocess.Popen(list(map(str,arguments)),env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with _owned_test_process(process):
                deadline=time.monotonic()+2400
                while process.poll() is None and time.monotonic()<deadline:time.sleep(.1)
                if process.poll() is None:
                    expired=True;cancel.write_text('Cancel this owned regression job.\n',encoding='utf-8')
                    try:process.wait(timeout=30)
                    except subprocess.TimeoutExpired:pass
        if expired or process.returncode:
            raise RuntimeError('Fixed regression runner failed or exceeded its deadline.')
        result = _strict_json(_read(report, 1_000_000).decode('utf-8'))
        _checks(result.get('checks'))
        if result.get('state')!='passed' or result.get('payloads_unchanged') is not True or result.get('isolation_clean') is not True:
            raise ValueError('Trusted runner did not confirm its payload and isolation invariants.')
        return result

    def sign(self, build, digest):
        self.tool('signing_library')
        result = self.run([self.tool('powershell'),'-NoProfile','-File',self.tool('signer'),
            '-ReleaseDirectory',build,'-ExpectedManifestSha256',digest,'-VerifierPath',self.tool('verifier'),
            '-ExpectedVerifierSha256',self.tools['verifier']['sha256']],240,log=build.parent/'signing.log')
        proof = json.loads(result.stdout)
        if proof.get('signed_and_verified') is not True or proof.get('manifest_sha256','').lower() != digest or proof.get('private_key_exported'):
            raise ValueError('Host signer did not verify the exact release manifest.')

    def stage(self, build, identity, job):
        recovery = 'Prometheus-Recovery/Self-' + job.name + '/core'
        destinations = [self.ssd/identity['release'], self.ssd/recovery, self.host_root/'Backups'/recovery]
        for destination in destinations:
            _no_links(destination)
            if destination.exists():
                raise ValueError('Promotion destination already exists; refusing to overwrite it.')
            shutil.copytree(build, destination)
            self.verify(destination, identity['manifest_sha256'])
        return {'release_path':destinations[0], 'recovery':recovery}

    def capture_selection(self, baseline, digest):
        guard, marker = _read(self.guard), _read(self.marker,100_000)
        selected = _strict_json(marker.decode('utf-8-sig'))
        if hashlib.sha256(guard).hexdigest() != self._expected_guard() or selected.get('code_release') != Path(baseline).name or Path(baseline).parent != self.ssd:
            raise ValueError('The selected release or trusted guard changed.')
        self.verify(baseline,digest)
        return {'guard':guard,'marker':marker}

    def assert_selection(self, previous):
        if _read(self.guard) != previous['guard'] or _read(self.marker,100_000) != previous['marker']:
            raise ValueError('Selection changed while acquiring the exclusive memory lock.')

    def _processes(self):
        result = self.run([self.tool('powershell'),'-NoProfile','-Command',
            "@(Get-CimInstance Win32_Process|Where-Object {$_.Name -like 'python*' -and ($_.CommandLine -like '*--shutdown-request*' -or $_.CommandLine -like '*portable-memory-guard.py*')}|Select-Object ProcessId,ParentProcessId,CommandLine)|ConvertTo-Json -Compress"],15)
        rows = json.loads(result.stdout or '[]')
        return [rows] if isinstance(rows,dict) else rows

    @staticmethod
    def _argument(command, value):
        return re.search(r'(?<!\S)(?:"'+re.escape(str(value))+r'"|'+re.escape(str(value))+r')(?=\s|$)',command or '',re.I) is not None

    def stop(self, release):
        deadline = time.monotonic()+180
        requested = False
        while time.monotonic() < deadline:
            rows = self._processes()
            guards = [row for row in rows if self._argument(row.get('CommandLine'),self.guard)]
            apps = [row for row in rows if self._argument(row.get('CommandLine'),Path(release)/'prometheus.py')]
            if not apps and not guards:
                return
            if len(apps)>1 or len(guards)>1:
                raise ValueError('Ambiguous application processes; no process was forced to stop.')
            if apps:
                app = apps[0]
                if not guards or app['ParentProcessId'] != guards[0]['ProcessId'] or not self._argument(app.get('CommandLine'),'--shutdown-request') or not self._argument(app.get('CommandLine'),self.shutdown):
                    raise ValueError('Cooperative shutdown process identity was not verified.')
                if not requested:
                    _no_links(self.shutdown)
                    if not self.shutdown.exists():
                        self.shutdown.write_text('Finish current work and close for the exact owner-approved signed update.\n',encoding='utf-8')
                    requested = True
            time.sleep(.5)
        raise TimeoutError('Application did not close cooperatively; no process was forced to stop.')

    @contextmanager
    def lock(self, *, accepted_guard_bytes=()):
        accepted = {self._expected_guard(), *(hashlib.sha256(value).hexdigest() for value in accepted_guard_bytes)}
        if sha(self.guard) not in accepted:
            raise ValueError('Cannot import an untrusted memory guard.')
        spec = importlib.util.spec_from_file_location('prometheus_promotion_guard',self.guard)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        handle = module.acquire(self.ssd/'Prometheus-Data/.memory-session.lock')
        try:
            yield
        finally:
            handle.close()

    def backup(self, job):
        report = job/'backup.json'
        self.run([self.tool('python'),'-B',self.tool('care'),'backup','--root',self.ssd,
            '--destination',self.host_root/'Backups/Prometheus','--restore-check',job/'restore-check','--report',report],240,log=job/'backup.log')
        proof = _strict_json(_read(report,1_000_000).decode('utf-8-sig'))
        if proof.get('restore_check',{}).get('live_data_overwritten') is not False:
            raise ValueError('Backup did not demonstrate an isolated restore check.')

    def select(self, previous, guard, identity, receipt):
        self.assert_selection(previous)
        updated = _strict_json(previous['marker'].decode('utf-8-sig'))
        updated.update(code_release=identity['release'],version=identity['version'],built=now())
        updated['self_improvement'] = {'job_id':receipt['job_id'],'approval_fingerprint':receipt['approval_fingerprint'],
            'previous_release':json.loads(previous['marker'])['code_release'],'manifest_sha256':identity['manifest_sha256']}
        previous['next_guard'], previous['next_marker'] = guard, _json_bytes(updated)
        _write_atomic(self.guard,guard)
        _write_atomic(self.marker,previous['next_marker'])

    def restore_selection(self, previous):
        # Exact byte identities prevent rollback over another process's update.
        if (_read(self.marker,100_000) not in (previous['marker'],previous.get('next_marker'))
                or _read(self.guard) not in (previous['guard'],previous.get('next_guard'))):
            raise ValueError('Another release took ownership; rollback stopped.')
        _write_atomic(self.marker,previous['marker'])
        _write_atomic(self.guard,previous['guard'])

    def health(self, release, job):
        report = job/'installed-health.json'
        self.run([self.tool('python'),'-B',self.tool('care'),'check','--root',self.ssd,'--report',report],180,log=job/'health.log')
        result = _strict_json(_read(report,1_000_000).decode('utf-8-sig'))
        _validate_installed_health(result)

    def launch(self, job, label):
        # Detached app children can retain inherited stdout handles. A pipe would
        # wait for their EOF even after the supported launcher has exited.
        log_path=job/(label+'-launcher.log')
        env={key:value for key,value in os.environ.items() if key.upper() not in {'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'}}
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
        with log_path.open('wb') as log:
            result=subprocess.run(list(map(str,[self.tool('powershell'),'-NoProfile','-File',self.tool('launcher'),'-Mode','Open','-NoBrowser'])),
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=240,env=env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise RuntimeError('Supported host launcher failed; see '+log_path.name)

    def ready(self, release):
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic()+45
        while time.monotonic()<deadline:
            rows = self._processes()
            apps = [row for row in rows if self._argument(row.get('CommandLine'),Path(release)/'prometheus.py')]
            guards = [row for row in rows if self._argument(row.get('CommandLine'),self.guard)]
            try:
                with opener.open('http://127.0.0.1:54555/api/bootstrap',timeout=3) as response:
                    gate = json.load(response).get('owner_access',{})
                selected=_strict_json(_read(self.marker,100_000).decode('utf-8-sig'))
                if (len(apps)==1 and len(guards)==1 and apps[0]['ParentProcessId']==guards[0]['ProcessId']
                        and selected.get('code_release')==Path(release).name
                        and all(gate.get(key) is True for key in ('required','ready','enrolled'))):
                    return
            except (OSError,ValueError):
                pass
            time.sleep(.5)
        raise TimeoutError('The exact candidate did not become ready behind the owner gate.')

    def __call__(self, candidate, receipt):
        candidate = Path(candidate).absolute(); _no_links(candidate)
        job_id = receipt.get('job_id','')
        if not re.fullmatch(r'[0-9a-f]{32}',job_id) or candidate.name != 'candidate' or candidate.parent.name != job_id:
            raise ValueError('Unexpected engine candidate location.')
        if _read(candidate.parent/'promotion.claim',1000).decode().strip() != receipt.get('approval_fingerprint'):
            raise ValueError('One-use engine approval claim does not match.')
        marker = _strict_json(_read(self.marker,100_000).decode('utf-8-sig'))
        name = marker.get('code_release','')
        if not _RELEASE.fullmatch(name): raise ValueError('Invalid selected baseline.')
        baseline = self.ssd/name
        self.verify(baseline,receipt['baseline_manifest_sha256'])
        expected = validate_approval(candidate,baseline,receipt)
        self.jobs.mkdir(parents=True,exist_ok=True)
        job = self.jobs/job_id; _no_links(job); job.mkdir(exist_ok=False)
        _copy_payload(candidate,job/'snapshot',expected)
        request = {'receipt':receipt,'baseline':name,'configuration_sha256':hashlib.sha256(self.config_bytes).hexdigest()}
        path = job/'dispatch.json'; _save(path,request)
        driver = Path(__file__).resolve()
        if not driver.is_relative_to(baseline) or sha(driver) != baseline_payload(baseline,receipt['baseline_manifest_sha256']).get(driver.relative_to(baseline).as_posix()):
            raise ValueError('Detached driver must come from the currently signed baseline.')
        with (job/'worker.log').open('xb') as log:
            process = subprocess.Popen([str(self.tool('python')),'-B','-I',str(driver),'--worker',str(self.config_path),str(path),sha(path)],
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
        return {'state':'promotion_pending','job_id':job_id,'host_result':str(job/'result.json'),'worker_pid':process.pid}

    def result(self, job_id):
        """Read only this host's bounded result for an already-known job ID."""
        if not isinstance(job_id,str) or not re.fullmatch(r'[0-9a-f]{32}',job_id):
            raise ValueError('Invalid promotion job identifier.')
        path=self.jobs/job_id/'result.json';_no_links(path)
        if not path.is_file():
            return {'job_id':job_id,'state':'promotion_pending'}
        result=_strict_json(_read(path,100_000).decode('utf-8'))
        allowed={'validating','retesting','signing','waiting_for_shutdown','starting','active','failed','rolled_back','recovery_required'}
        if result.get('job_id')!=job_id or result.get('state') not in allowed:
            raise ValueError('Promotion result identity or state is invalid.')
        return result


def worker(config_path, request_path, request_digest):
    host = WindowsPromotionHost(config_path)
    request_path = Path(request_path).absolute();_no_links(request_path);request_path=request_path.resolve(strict=True)
    if request_path.name != 'dispatch.json' or request_path.parent.parent != host.jobs or sha(request_path) != _hash(request_digest):
        raise ValueError('Detached handoff is not the exact queued host request.')
    request = _strict_json(_read(request_path,2_000_000).decode('utf-8'))
    if request['configuration_sha256'] != hashlib.sha256(host.config_bytes).hexdigest():
        raise ValueError('Host configuration changed after owner-approved dispatch.')
    job = request_path.parent
    if request['receipt']['job_id'] != job.name or not _RELEASE.fullmatch(request['baseline']):
        raise ValueError('Detached handoff identity mismatch.')
    with (job/'execution.claim').open('x',encoding='utf-8') as claim:
        claim.write(request_digest+'\n')
    return promote_snapshot(host,job/'snapshot',host.ssd/request['baseline'],request['receipt'],job)


if __name__ == '__main__':
    import sys
    if len(sys.argv)!=5 or sys.argv[1]!='--worker':
        raise SystemExit('Use the authenticated application promotion callback.')
    print(json.dumps(worker(*sys.argv[2:])))
