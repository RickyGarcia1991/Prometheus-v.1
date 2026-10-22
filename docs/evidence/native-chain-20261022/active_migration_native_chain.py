"""Read-only signed-chain test adapter over native Windows verification.
All authority/runtime/receipt inputs are synthetic generated fixtures. Not a live adapter.
"""
import ast
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

import active_host_migration_core as core

BASE = Path(__file__).resolve().parent
PWSH = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
BRIDGE = BASE / 'active-migration-native-chain.ps1'
OLD = BASE / 'reconnect-enrollment-candidate-r5'
NEW = BASE / 'reconnect-enrollment-candidate-r7'
PAYLOADS = ('Prometheus-QuietHost-v3.exe', 'Prometheus-USB-Reconnect-Supervisor.ps1', 'Prometheus-Lifecycle.ps1', 'Prometheus-Arrival.ps1', 'Prometheus-Host-Agent.ps1')

# Reuse exactly the previously tested native DPAPI primitive without importing its
# controller-provider module or executing its unrelated top-level implementation.
DPAPI_SOURCE = BASE / 'controller_pointer_windows_fixture.py'
if hashlib.sha256(DPAPI_SOURCE.read_bytes()).hexdigest() != '661b0606757ef6080ef4e3b564ee0f835b248272b76225ed3d818079d307d03a':
    raise ValueError('Reviewed DPAPI fixture primitive changed')
_tree = ast.parse(DPAPI_SOURCE.read_text())
_nodes = [node for node in _tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in ('Blob', 'dpapi')]
if len(_nodes) != 2:
    raise ValueError('DPAPI primitive unavailable')
exec(compile(ast.Module(body=_nodes, type_ignores=[]), str(DPAPI_SOURCE), 'exec'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native(root, mode='Verify', old=OLD, new=NEW):
    result = subprocess.run([str(PWSH), '-NoProfile', '-NonInteractive', '-File', str(BRIDGE), '-Scratch', str(root), '-Mode', mode, '-OldPackage', str(old), '-NewPackage', str(new)], capture_output=True, text=True, timeout=40)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def receipt_path(root):
    observed = json.loads((root / 'observations.json').read_bytes())
    return root / 'runtime' / 'Enrollment' / observed['enrollment_id'] / 'journal.json'


def setup(root):
    root = Path(root).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('prometheus-active-chain-'):
        raise ValueError('Generated fixture root required')
    runtime = root / 'runtime'
    runtime.mkdir()
    observed = dict(fixture='synthetic-authority-not-installed', enrollment_id=secrets.token_hex(16), host=hashlib.sha256(b'synthetic fixture host').hexdigest(), profile='synthetic-fixture-profile', task_identity='synthetic-task-' + secrets.token_hex(16))
    (root / 'observations.json').write_bytes(core.encode(observed))
    (runtime / 'authorized-host.json').write_bytes(core.encode(dict(fingerprint=observed['host'], authorized=True, method='SYNTHETIC-FIXTURE-NOT-INSTALLED-AUTHORIZATION')))
    (root / 'task.xml').write_text('<Task><RegistrationInfo><Description>Prometheus reconnect enrollment ' + observed['enrollment_id'] + '</Description></RegistrationInfo></Task>')
    for name in PAYLOADS:
        shutil.copyfile(NEW / name, runtime / name)
    receipt = native(root, 'CreateReceipt')
    old_rows = {row['path']: row for row in json.loads((OLD / 'SHA256-MANIFEST.json').read_bytes())}
    new_rows = {row['path']: row for row in json.loads((NEW / 'SHA256-MANIFEST.json').read_bytes())}
    plan = dict(transaction=secrets.token_hex(16), host=observed['host'], profile=observed['profile'], auth_sha256=digest(runtime / 'authorized-host.json'), enrollment_sha256=receipt['receipt_sha256'], task_config_sha256=digest(root / 'task.xml'), task_identity=observed['task_identity'], original_enabled=True, original_running=True, old_manifest=core.R5, new_manifest=core.R7, old_file_sha256=old_rows['Prometheus-Host-Agent.ps1']['sha256'], new_file_sha256=new_rows['Prometheus-Host-Agent.ps1']['sha256'], payload='Prometheus-Host-Agent.ps1', wrapper_identity='synthetic-wrapper-history', child_identity='synthetic-child-history')
    (root / 'plan.json').write_bytes(core.encode(plan))
    key = secrets.token_bytes(32)
    (root / 'key.dpapi').write_bytes(dpapi(key))
    (root / 'migration.json').write_bytes(core.seal(plan, 'complete', key))
    return plan


class NativeChainFixture:
    """Only verification callbacks; no migration mutation callbacks exist."""
    def __init__(self, root, old=OLD, new=NEW):
        self.root = Path(root)
        self.old, self.new = old, new
        self.evidence = None
        self.key = dpapi((self.root / 'key.dpapi').read_bytes(), False)
        if len(self.key) != 32:
            raise ValueError('Protected fixture key invalid')

    @contextlib.contextmanager
    def outer_lock(self):
        # This is a generated, single-test fixture: no live enrollment mutex claim.
        self.evidence = None
        yield

    def verify_original_enrollment(self, plan):
        if (self.root / 'plan.json').read_bytes() != core.encode(plan):
            raise ValueError('Caller plan differs from exact fixture plan')
        self.evidence = native(self.root, old=self.old, new=self.new)
        if self.evidence['plan_sha256'] != hashlib.sha256(core.encode(plan)).hexdigest():
            raise ValueError('Native observation bound to another plan')

    def verify(self, plan):
        if not self.evidence or self.evidence['receipt_sha256'] != plan['enrollment_sha256']:
            raise ValueError('Native authenticated original receipt verification required')

    def read_journal(self):
        return (self.root / 'migration.json').read_bytes()

    def assert_complete(self, plan):
        if not self.evidence or not self.evidence['native_verified']:
            raise ValueError('Native completion verification required')

    def completed_chain(self, plan):
        return core.verify_completed_chain(self, plan, self.key)
