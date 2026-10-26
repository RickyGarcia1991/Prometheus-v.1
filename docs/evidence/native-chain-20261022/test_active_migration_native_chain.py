"""Real RSA signatures + native DPAPI receipt + HMAC completion, synthetic authority."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

import pytest
import active_migration_native_chain as f


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory(prefix='prometheus-active-chain-') as directory:
        root = Path(directory)
        plan = f.setup(root)
        yield root, plan


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}


def test_native_signed_chain_accepts_and_preserves_every_fixture_byte(fixture):
    root, plan = fixture
    before = snapshot(root)
    provider = f.NativeChainFixture(root)
    result = provider.completed_chain(plan)
    assert result['old_manifest'] == f.core.R5 and result['new_manifest'] == f.core.R7
    assert provider.evidence['bundle_entries'] == 7
    assert provider.evidence['unchanged_entries'] == 6
    assert provider.evidence['runtime_payloads'] == 5
    assert provider.evidence['legacy_r5_runtime_refused']
    assert provider.evidence['existing_activation_guard_rejects_r7']
    assert provider.evidence['receipt_as_r7_refused']
    assert provider.evidence['fixture_original_receipt']
    assert not provider.evidence['installed_authorization_authenticated']
    assert snapshot(root) == before


@pytest.mark.parametrize('damage', ['corrupt-dpapi-receipt', 'wrong-old-receipt', 'receipt-as-r7', 'incomplete-journal', 'tampered-journal', 'wrong-key', 'modified-runtime', 'modified-other-runtime', 'changed-auth', 'changed-task-xml', 'changed-task-identity', 'changed-host', 'wrong-package', 'changed-other-bundle-entry', 'changed-signed-manifest'])
def test_native_chain_rejects_changed_authority_and_bytes(fixture, damage):
    root, plan = fixture
    package_copy = None
    new = f.NEW
    if damage == 'corrupt-dpapi-receipt':
        receipt = f.receipt_path(root)
        envelope = json.loads(receipt.read_bytes())
        import base64
        sealed = bytearray(base64.b64decode(envelope['sealed']))
        sealed[-1] ^= 1
        envelope['sealed'] = base64.b64encode(sealed).decode()
        receipt.write_bytes(f.core.encode(envelope))
        plan['enrollment_sha256'] = f.digest(receipt)
        (root / 'plan.json').write_bytes(f.core.encode(plan))
        key = f.dpapi((root / 'key.dpapi').read_bytes(), False)
        (root / 'migration.json').write_bytes(f.core.seal(plan, 'complete', key))
    elif damage == 'wrong-old-receipt':
        # Another valid DPAPI receipt for this same id/package has different bytes.
        original = f.receipt_path(root)
        original.rename(original.with_suffix('.original'))
        f.native(root, 'CreateReceipt')
    elif damage == 'receipt-as-r7':
        plan['old_manifest'] = f.core.R7
        (root / 'plan.json').write_bytes(f.core.encode(plan))
    elif damage == 'incomplete-journal':
        key = f.dpapi((root / 'key.dpapi').read_bytes(), False)
        (root / 'migration.json').write_bytes(f.core.seal(plan, 'replace', key))
    elif damage == 'tampered-journal':
        data = (root / 'migration.json').read_bytes()
        (root / 'migration.json').write_bytes(data.replace(b'complete', b'prepared'))
    elif damage == 'wrong-key':
        (root / 'key.dpapi').write_bytes(f.dpapi(b'x' * 32))
    elif damage in ('modified-runtime', 'modified-other-runtime'):
        name = 'Prometheus-Host-Agent.ps1' if damage == 'modified-runtime' else 'Prometheus-Arrival.ps1'
        with (root / 'runtime' / name).open('ab') as handle:
            handle.write(b'\n# fixture tamper')
    elif damage == 'changed-auth':
        (root / 'runtime' / 'authorized-host.json').write_bytes(b'{"fixture":"different"}')
    elif damage == 'changed-task-xml':
        with (root / 'task.xml').open('ab') as handle:
            handle.write(b'<!-- changed -->')
    elif damage in ('changed-task-identity', 'changed-host'):
        observed = json.loads((root / 'observations.json').read_bytes())
        observed['task_identity' if damage == 'changed-task-identity' else 'host'] = 'changed'
        (root / 'observations.json').write_bytes(f.core.encode(observed))
    elif damage == 'wrong-package':
        new = f.OLD
    else:
        # The existing qualifier only admits companion-release descendants. Use a
        # fresh disposable copy, never alter the real signed package directories.
        package_copy = f.BASE / ('active-chain-package-fixture-' + uuid.uuid4().hex)
        shutil.copytree(f.NEW, package_copy)
        new = package_copy
        path = package_copy / ('Prometheus-Arrival.ps1' if damage == 'changed-other-bundle-entry' else 'SHA256-MANIFEST.json')
        with path.open('ab') as handle:
            handle.write(b'\n# fixture tamper')
    before = snapshot(root)
    try:
        with pytest.raises(ValueError):
            f.NativeChainFixture(root, new=new).completed_chain(plan)
        assert snapshot(root) == before
    finally:
        if package_copy:
            assert package_copy.resolve().parent == f.BASE.resolve()
            assert package_copy.name.startswith('active-chain-package-fixture-')
            shutil.rmtree(package_copy)


