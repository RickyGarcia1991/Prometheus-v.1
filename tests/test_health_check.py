import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from prometheus_assistant import health_check as health
from prometheus_assistant.hardware import HardwareProfile


@pytest.fixture
def installation(tmp_path):
    release = tmp_path / 'Prometheus-v0.8.0-dev1-test'
    release.mkdir()
    resources = tmp_path / 'Prometheus-Resources'
    for relative in ('Python/python.exe', 'Ollama/runtime/ollama.exe'):
        path = resources / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'runtime')
    (release / 'pyproject.toml').write_text('[project]\nversion = "0.8.0.dev1"\n')
    (release / 'RELEASE-PROVENANCE.json').write_text(json.dumps({'release': release.name}))
    (tmp_path / 'PROMETHEUS-SSD-STATUS.json').write_text(json.dumps({
        'version': '0.8.0.dev1', 'code_release': 'Z:\\' + release.name}))
    entries = [{'path': name, 'size_bytes': (release / name).stat().st_size,
                'sha256': hashlib.sha256((release / name).read_bytes()).hexdigest()}
               for name in ('pyproject.toml', 'RELEASE-PROVENANCE.json')]
    (release / 'SHA256-MANIFEST.json').write_text(json.dumps(entries))
    models = resources / 'Ollama/.ollama/models'
    manifest = models / 'manifests/registry.ollama.ai/library/gemma3/270m'
    manifest.parent.mkdir(parents=True)
    blobs = models / 'blobs'
    blobs.mkdir()
    digest = '1' * 64
    blob = blobs / ('sha256-' + digest)
    blob.write_bytes(b'model')
    reference = {'digest': 'sha256:' + digest, 'size': 5}
    manifest.write_text(json.dumps({'schemaVersion': 2, 'config': reference,
                                    'layers': [reference]}))
    memory = tmp_path / 'data/memory.sqlite3'
    memory.parent.mkdir()
    with sqlite3.connect(memory) as db:
        db.execute('create table example (value text)')
        db.execute('insert into example values (?)', ('private conversation',))
    db.close()
    return SimpleNamespace(release=release, resources=resources, memory=memory,
                           manifest=manifest, blob=blob, entries=entries)


def run(installation, **kwargs):
    return health.run_health_check(installation.memory, installation.resources,
        release_root=installation.release, running_version='0.8.0.dev1',
        hardware=kwargs.pop('hardware', HardwareProfile(8, 4, 'Windows', 4)), **kwargs)


def check(report, identity):
    return next(item for item in report['checks'] if item['id'] == identity)


def test_read_only_health_reports_explicit_scope_and_remaining_acceptance(installation):
    before = installation.memory.read_bytes()
    before_files = {p.relative_to(installation.release.parent): p.read_bytes()
                    for p in installation.release.parent.rglob('*') if p.is_file()}
    report = run(installation)
    for identity in ('release_metadata', 'code_manifest', 'memory_database', 'runtime', 'model_files', 'model_ram'):
        assert check(report, identity)['status'] == 'pass'
    assert check(report, 'physical_acceptance')['status'] == 'untested'
    assert check(report, 'model_acceptance')['status'] == 'untested'
    assert report['network_used'] is False and report['inference_used'] is False
    assert installation.memory.read_bytes() == before
    assert before_files == {p.relative_to(installation.release.parent): p.read_bytes()
                            for p in installation.release.parent.rglob('*') if p.is_file()}
    assert 'private conversation' not in json.dumps(report)
    assert check(report, 'model_files')['models'] == ['gemma3:270m']


@pytest.mark.parametrize('payload', ['{', '[]', '{"release": 12}'])
def test_malformed_metadata_is_failure_without_echoing_contents(installation, payload):
    (installation.release / 'RELEASE-PROVENANCE.json').write_text(payload)
    report = run(installation)
    assert check(report, 'release_metadata')['status'] == 'fail'
    assert check(report, 'runtime')['status'] == 'pass'


def test_active_pointer_mismatch_is_not_silently_accepted(installation):
    path = installation.resources.parent / 'PROMETHEUS-SSD-STATUS.json'
    path.write_text(json.dumps({'version': '0.8.0.dev1', 'code_release': 'D:\\Prometheus-v0.8.0-other'}))
    assert check(run(installation), 'release_metadata')['status'] == 'fail'


@pytest.mark.parametrize('payload', ['[]', '{"code_release":"../private"}', '{'])
def test_malformed_active_pointer_fails_independently(installation, payload):
    (installation.resources.parent / 'PROMETHEUS-SSD-STATUS.json').write_text(payload)
    assert check(run(installation), 'release_metadata')['status'] == 'fail'


def test_running_package_version_mismatch_is_failure(installation):
    (installation.release / 'pyproject.toml').write_text('[project]\nversion = "0.8.0.dev0"\n')
    assert check(run(installation), 'release_metadata')['status'] == 'fail'


def test_development_directory_is_explicitly_untested_for_active_pointer(installation):
    staged = installation.release.parent / 'local-development'
    installation.release.rename(staged)
    installation.release = staged
    item = check(run(installation), 'release_metadata')
    assert item['status'] == 'untested'
    assert 'development' in item['summary'].lower()


def test_missing_memory_is_not_created(installation):
    installation.memory.unlink()
    assert check(run(installation), 'memory_database')['status'] == 'blocked'
    assert not installation.memory.exists()


def test_corrupt_memory_fails_without_dumping_database(installation):
    installation.memory.write_bytes(b'private invalid database')
    assert check(run(installation), 'memory_database')['status'] == 'fail'


def test_live_wal_is_blocked_instead_of_ignoring_uncheckpointed_data(installation):
    wal = Path(str(installation.memory) + '-wal')
    wal.write_bytes(b'pending database writes')
    before = wal.read_bytes()
    assert check(run(installation), 'memory_database')['status'] == 'blocked'
    assert wal.read_bytes() == before


@pytest.mark.parametrize('change', ['checksum', 'size', 'duplicate', 'traversal', 'absolute', 'bad_row'])
def test_code_manifest_rejects_invalid_or_changed_files(installation, change):
    entries = installation.entries
    if change == 'checksum': entries[0]['sha256'] = '0' * 64
    elif change == 'size': entries[0]['size_bytes'] += 1
    elif change == 'duplicate': entries.append(dict(entries[0]))
    elif change == 'traversal': entries[0]['path'] = '../outside.txt'
    elif change == 'absolute': entries[0]['path'] = 'C:/outside.txt'
    else: entries.append(None)
    (installation.release / 'SHA256-MANIFEST.json').write_text(json.dumps(entries))
    assert check(run(installation), 'code_manifest')['status'] == 'fail'


def test_model_missing_blob_is_installation_failure_and_not_ram_failure(installation):
    installation.blob.unlink()
    report = run(installation)
    assert check(report, 'model_files')['status'] == 'fail'
    assert check(report, 'model_ram')['status'] == 'blocked'


@pytest.mark.parametrize('payload', ['[]', '{"schemaVersion":2,"layers":[]}', '{'])
def test_invalid_model_manifest_does_not_count_as_installed(installation, payload):
    installation.manifest.write_text(payload)
    report = run(installation)
    assert check(report, 'model_files')['status'] == 'fail'
    assert check(report, 'model_files')['models'] == []


def test_truncated_model_blob_is_not_complete(installation):
    installation.blob.write_bytes(b'm')
    assert check(run(installation), 'model_files')['status'] == 'fail'


def test_missing_runtime_is_a_failure(installation):
    (installation.resources / 'Ollama/runtime/ollama.exe').unlink()
    assert check(run(installation), 'runtime')['status'] == 'fail'


def test_low_ram_is_blocked_with_complete_model_files(installation):
    report = run(installation, hardware=HardwareProfile(8, 4, 'Windows', .5))
    assert check(report, 'model_files')['status'] == 'pass'
    assert check(report, 'model_ram')['status'] == 'blocked'
    assert 'RAM' in check(report, 'model_ram')['summary']


def test_unknown_available_ram_is_not_a_confirmed_feasibility_pass(installation):
    report = run(installation, hardware=HardwareProfile(8, 4, 'Windows', None))
    assert check(report, 'model_ram')['status'] == 'untested'


def test_workspace_health_action_does_not_start_runtime_or_create_memory(installation, monkeypatch):
    from prometheus_assistant import ui_workspace, ui_server
    installation.memory.unlink()
    monkeypatch.setattr(ui_workspace, 'resource_root', lambda: str(installation.resources))
    request = ui_server.validate_job({'mode': 'health_check'})
    app = SimpleNamespace(memory_path=installation.memory)
    result = ui_workspace.execute(app, request)
    assert result['action'] == 'health_check'
    assert check(result['details'], 'memory_database')['status'] == 'blocked'
    assert not installation.memory.exists()
