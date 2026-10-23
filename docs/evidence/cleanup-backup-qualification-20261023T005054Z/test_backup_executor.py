import json
from pathlib import Path
import tempfile
import subprocess
import sys
import uuid
import pytest
import backup_executor as b


def fixture():
    root = Path(tempfile.gettempdir()) / (b.PREFIX + uuid.uuid4().hex)
    source = root / 'source'
    source.mkdir(parents=True)
    (source / 'empty').mkdir()
    (source / 'nested').mkdir()
    (source / 'data.bin').write_bytes(b'original-data')
    (source / 'nested' / 'data.bin').write_bytes(b'same-name-must-remain')
    Path(str(source / 'nested' / 'data.bin') + ':fixture').write_bytes(b'file-stream')
    Path(str(source / 'empty') + ':fixture').write_bytes(b'directory-stream')
    return root, source


def test_exact_archive_exclusion_and_stream_restore():
    root, source = fixture()
    archive = root / 'archive.bin'
    archive.write_bytes((source / 'data.bin').read_bytes())
    ref = dict(path=str(archive), bytes=archive.stat().st_size, sha256=b.sha(archive))
    destination = b.prepare_fixture(source, root / 'backup', {'data.bin': ref})
    report = b.copy_fixture(destination)
    assert report['passed'] and report['archive_references'] == 1
    assert not (destination / 'tree/data.bin').exists()
    assert (destination / 'tree/nested/data.bin').read_bytes() == b'same-name-must-remain'
    assert Path(str(destination / 'tree/nested/data.bin') + ':fixture').read_bytes() == b'file-stream'
    assert Path(str(destination / 'tree/empty') + ':fixture').read_bytes() == b'directory-stream'


def test_corrupt_same_size_destination_is_repaired_on_resume():
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    b.copy_fixture(destination)
    target = destination / 'tree/data.bin'
    stamp = target.stat().st_mtime_ns
    target.write_bytes(b'corrupt--data')
    b.os.utime(target, ns=(target.stat().st_atime_ns, stamp))
    assert target.stat().st_size == (source / 'data.bin').stat().st_size
    assert b.copy_fixture(destination)['passed']
    assert target.read_bytes() == b'original-data'


@pytest.mark.parametrize('point', ['after-intent', 'after-copy', 'before-complete'])
def test_interrupted_journal_resumes_without_false_completion(point):
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    def interrupt(at):
        if at == point:
            raise RuntimeError('injected interruption')
    with pytest.raises(RuntimeError):
        b.copy_fixture(destination, interrupt)
    assert not (destination / 'verified.json').exists()
    assert b.copy_fixture(destination)['passed']


def test_changed_source_and_foreign_destination_preserved():
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    (source / 'data.bin').write_bytes(b'changed')
    with pytest.raises(b.Refusal):
        b.copy_fixture(destination)
    assert not (destination / 'tree').exists()


def test_archive_reference_cannot_drop_ads():
    root, source = fixture()
    archive = root / 'archive.bin'
    original = source / 'nested/data.bin'
    archive.write_bytes(original.read_bytes())
    with pytest.raises(b.Refusal):
        b.prepare_fixture(source, root / 'backup', {'nested/data.bin': dict(path=str(archive), bytes=archive.stat().st_size, sha256=b.sha(archive))})
    assert not (root / 'backup').exists()


def test_reserve_and_full_execution_authority_fail_closed(tmp_path):
    with pytest.raises(b.Refusal):
        b.check_reserve(100, free=b.RESERVE + 99)
    with pytest.raises(b.Refusal):
        b.check_reserve(0, reserve=0, free=10**12)
    identities = tmp_path / 'identities.json'
    identities.write_bytes(b.encode(dict(ReadOnly=True, Targets=[{'Label': 'test'}])))
    for maintenance, metadata in [(None, True), ({'verified_by_production_adapter': True}, False), ({'verified_by_production_adapter': True}, True)]:
        with pytest.raises(b.Refusal):
            b.validate_full_prerequisites(identities, b.sha(identities), maintenance, metadata, 10**12, 0)


def test_inaccessible_directory_cannot_form_complete_inventory(monkeypatch):
    root, source = fixture()
    def failed_walk(path, *, followlinks, onerror):
        onerror(PermissionError('injected inaccessible directory'))
        return iter(())
    monkeypatch.setattr(b.os, 'walk', failed_walk)
    with pytest.raises(PermissionError):
        b.prepare_fixture(source, root / 'backup')
    assert not (root / 'backup').exists()


def test_foreign_output_is_not_adopted_or_deleted():
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    b.copy_fixture(destination)
    foreign = destination / 'tree/foreign.bin'
    foreign.write_bytes(b'preserve')
    with pytest.raises(b.Refusal):
        b.copy_fixture(destination)
    assert foreign.read_bytes() == b'preserve'


def test_resumed_repair_unpublishes_prior_complete_receipt():
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    b.copy_fixture(destination)
    original = (destination / 'verified.json').read_bytes()
    def interrupt(point):
        if point == 'after-intent':
            raise RuntimeError('interrupt after resume intent')
    with pytest.raises(RuntimeError):
        b.copy_fixture(destination, interrupt)
    assert not (destination / 'verified.json').exists()
    assert next(destination.glob('previous-verified-*.json')).read_bytes() == original
    assert b.copy_fixture(destination)['passed']


def test_actual_process_exit_after_copy_resumes_committed_intent():
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    code = "import os,sys; import backup_executor as b; b.copy_fixture(b.Path(sys.argv[1]), lambda point: os._exit(79) if point=='after-copy' else None)"
    result = subprocess.run([sys._base_executable, '-B', '-c', code, str(destination)],
                            cwd=Path(__file__).parent, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 79 and not (destination / 'verified.json').exists()
    assert b.copy_fixture(destination)['passed']


def test_remaining_budget_credits_actual_allocation_only(monkeypatch):
    root, source = fixture()
    destination = b.prepare_fixture(source, root / 'backup')
    plan = b.load_plan(destination)
    missing = b.required_remaining(plan, destination / 'tree')
    b.copy_fixture(destination)
    present = b.required_remaining(plan, destination / 'tree')
    assert present < missing
    monkeypatch.setattr(b, 'allocated', lambda path: 0)
    assert b.required_remaining(plan, destination / 'tree') == missing


def test_prepare_snapshot_is_pinned_and_never_execution_authority(tmp_path):
    values = [('OS', 493705228288, 'NTFS'), ('Prometheus-2TB', 1950372327424, 'NTFS'), ('PROMETHEUS', 32076988416, 'FAT32')]
    targets = []
    for number, (label, size, filesystem) in enumerate(values):
        targets.append(dict(Label=label, Volume=dict(Size=size, FileSystem=filesystem, HealthStatus='Healthy', UniqueId=str(number)),
                            Partition=dict(DiskNumber=number, Guid=str(number)),
                            Disk=dict(Number=number, UniqueId=str(number), SerialNumber=str(number), PartitionStyle='GPT')))
    snapshot = tmp_path / 'identity.json'
    snapshot.write_bytes(b.encode(dict(ReadOnly=True, ObservedUtc='2026-10-23T00:00:00Z', Targets=targets)))
    output = tmp_path / 'prepare.json'
    with pytest.raises(b.Refusal):
        b.prepare(snapshot, '0' * 64, output)
    assert not output.exists()
    report = b.prepare(snapshot, b.sha(snapshot), output)
    assert report['prepared'] and not report['execution_allowed']
    assert not json.loads(output.read_bytes())['production_copy_available']
    targets[2]['Disk']['UniqueId'] = targets[0]['Disk']['UniqueId']
    snapshot.write_bytes(b.encode(dict(ReadOnly=True, ObservedUtc='2026-10-23T00:00:00Z', Targets=targets)))
    with pytest.raises(b.Refusal):
        b.prepare(snapshot, b.sha(snapshot), tmp_path / 'invalid.json')
