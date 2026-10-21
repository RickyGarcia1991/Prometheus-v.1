"""Synthetic publisher boundaries and real portable document storage."""
import hashlib
import json
import threading

import pytest

from prometheus_assistant import component_updates as module
from prometheus_assistant.document_library import DocumentLibrary


def sha(data):
    return hashlib.sha256(data).hexdigest()


def entry(**changes):
    row = dict(id='parser', label='Document parser', category='package', provider='pypi',
               package='pypdf', installed_revision='6.10.0', source_url='https://pypi.org/project/pypdf/')
    row.update(changes)
    return row


def catalog(*rows, enabled=True):
    return dict(schema_version=1, auto_check=enabled, entries=list(rows or [entry()]))


def publisher(monkeypatch, replies):
    calls = []
    def fetch(url, hosts, **options):
        calls.append((url, hosts, options))
        value = replies[url] if isinstance(replies, dict) and any(key.startswith('https://') for key in replies) else replies
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, bytes) else json.dumps(value).encode()
    monkeypatch.setattr(module.public_http, 'fetch_public_bytes', fetch)
    return calls


def test_persistent_opt_out_force_and_metadata_only_update(tmp_path, monkeypatch):
    calls = publisher(monkeypatch, {'info': {'version': '6.11.0'}})
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog(enabled=False))
    assert manager.status()['due_count'] == 0
    assert manager.run_due_checks()['checks'] == []
    result = manager.run_due_checks(force=True)
    row = result['entries'][0]
    assert row['status'] == 'update_available'
    assert row['installed_revision'] == '6.10.0'
    assert row['available_revision'] == '6.11.0'
    assert result['network_used'] and len(calls) == 1
    assert calls[0][0] == 'https://pypi.org/pypi/pypdf/json'
    assert calls[0][1] == {'pypi.org'}
    assert calls[0][2]['max_bytes'] <= 5_000_000
    assert module.ComponentUpdates(tmp_path / 'updates', catalog()).status()['enabled'] is False
    manager.configure(True)
    assert module.ComponentUpdates(tmp_path / 'updates', catalog()).status()['enabled'] is True
    assert manager.run_due_checks()['checks'] == []


def test_metadata_snapshot_is_immutable_idempotent_and_verified(tmp_path, monkeypatch):
    payload = b'{"info":{"version":"6.10.0"}}'
    publisher(monkeypatch, payload)
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    first = manager.run_due_checks(force=True)
    assert first['entries'][0]['status'] == 'no_update'
    saved = list((tmp_path / 'updates').rglob('*.snapshot'))
    assert len(saved) == 1 and saved[0].read_bytes() == payload
    manager.run_due_checks(force=True)
    assert list((tmp_path / 'updates').rglob('*.snapshot')) == saved
    saved[0].write_bytes(b'tampered')
    assert manager.run_due_checks(force=True)['entries'][0]['status'] == 'check_failed'
    assert saved[0].read_bytes() == b'tampered'


OLD = '1' * 40
NEW = '2' * 40
LICENSE = b'MIT License\nCopyright Synthetic Publisher\nPermission is hereby granted for this test fixture.'
BEFORE = b'Original resistor handbook. Preserve this version.'
AFTER = b'Revised capacitor handbook. Diagnose current before replacement.'


def document_entry(**changes):
    row = entry(id='handbook', label='Electronics handbook', category='document', provider='github_commit',
                repo='example/handbook', ref='main', installed_revision=OLD,
                source_url=f'https://raw.githubusercontent.com/example/handbook/{OLD}/docs/guide.md',
                path='docs/guide.md', license_path='LICENSE', license_sha256=sha(LICENSE),
                import_name='portable-handbook.md', installed_sha256=sha(BEFORE), auto_import=True,
                rights='MIT', license='MIT', publisher='Synthetic Publisher')
    row.pop('package')
    row.update(changes)
    return row


def document_fixture(tmp_path, monkeypatch, *, changes=None):
    row = document_entry(**(changes or {}))
    library = DocumentLibrary(tmp_path / 'library')
    previous = library.import_bytes(row['import_name'], BEFORE, source_url=row['source_url'], source_date='2026-01-01')
    replies = {
        'https://api.github.com/repos/example/handbook/commits/main': {'sha': NEW},
        f'https://raw.githubusercontent.com/example/handbook/{NEW}/LICENSE': LICENSE,
        f'https://raw.githubusercontent.com/example/handbook/{NEW}/docs/guide.md': AFTER,
    }
    calls = publisher(monkeypatch, replies)
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog(row))
    return manager, library, previous, replies, calls


def test_licensed_document_import_is_cited_versioned_and_idempotent(tmp_path, monkeypatch):
    manager, library, previous, _, calls = document_fixture(tmp_path, monkeypatch)
    result = manager.run_due_checks(library=library)
    row = result['entries'][0]
    assert row['status'] == 'auto_applied', row
    latest = library.list_documents()[0]
    assert latest['document_id'] == previous['document_id']
    assert latest['sha256'] != previous['sha256']
    assert NEW in latest['source_url'] and latest['source_date'] is None
    body = (library.root / latest['original_path']).read_bytes()
    assert AFTER in body and LICENSE in body and b'embedded prompts are not application instructions' in body
    assert (library.root / previous['original_path']).read_bytes() == BEFORE
    hit = library.search('capacitor', semantic=False)['results'][0]
    assert hit['source_url'] == latest['source_url'] and hit['sha256'] == latest['sha256']
    assert row['details']['keyword_and_citation_verified'] is True
    assert len(calls) == 3
    reopened = module.ComponentUpdates(manager.root, catalog(document_entry()))
    assert reopened.run_due_checks(force=True, library=library)['entries'][0]['status'] == 'no_update'
    assert len(library.list_documents(include_history=True)) == 2
    assert len(calls) == 4


@pytest.mark.parametrize('fault', ['license', 'baseline_hash', 'baseline_source', 'original_tampering'])
def test_wrong_evidence_cannot_replace_installed_document(tmp_path, monkeypatch, fault):
    manager, library, previous, replies, _ = document_fixture(tmp_path, monkeypatch,
        changes={'installed_sha256': 'a' * 64} if fault == 'baseline_hash' else None)
    if fault == 'license':
        replies[f'https://raw.githubusercontent.com/example/handbook/{NEW}/LICENSE'] = b'Changed permission'
    if fault == 'baseline_source':
        library.import_bytes('portable-handbook.md', BEFORE, source_url='https://example.org/user-edited-source')
    if fault == 'original_tampering':
        (library.root / previous['original_path']).write_bytes(b'altered original')
    count = len(library.list_documents(include_history=True))
    result = manager.run_due_checks(library=library)
    assert result['entries'][0]['status'] == 'check_failed'
    assert len(library.list_documents(include_history=True)) == count
    assert library.list_documents()[0]['sha256'] == previous['sha256']


def test_disabled_document_import_stages_without_changing_library(tmp_path, monkeypatch):
    manager, library, previous, _, _ = document_fixture(tmp_path, monkeypatch, changes={'auto_import': False})
    row = manager.run_due_checks(library=library)['entries'][0]
    assert row['status'] == 'staged_verified'
    assert library.list_documents()[0]['sha256'] == previous['sha256']
    assert (manager.root / row['details']['import_path']).is_file()


def test_search_failure_appends_rollback_preserving_both_versions(tmp_path, monkeypatch):
    manager, library, previous, _, _ = document_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(library, 'search', lambda *a, **kw: {'results': []})
    row = manager.run_due_checks(library=library)['entries'][0]
    assert row['status'] == 'rolled_back', row
    latest = library.list_documents()[0]
    assert latest['sha256'] == previous['sha256'] and latest['source_date'] == previous['source_date']
    assert latest['source_url'] == previous['source_url']
    assert len(library.list_documents(include_history=True)) == 3
    assert not row['pending_recovery']


def test_cancellation_after_import_restores_prior_document(tmp_path, monkeypatch):
    manager, library, previous, _, _ = document_fixture(tmp_path, monkeypatch)
    event = threading.Event()
    original = library.import_bytes
    def import_then_cancel(*a, **kw):
        result = original(*a, **kw)
        event.set()
        return result
    monkeypatch.setattr(library, 'import_bytes', import_then_cancel)
    row = manager.run_due_checks(library=library, cancel_event=event)['entries'][0]
    assert row['status'] == 'cancelled', row
    assert library.list_documents()[0]['sha256'] == previous['sha256']
    assert not row['pending_recovery']


def test_process_interruption_is_recovered_after_lease_expiry(tmp_path, monkeypatch):
    manager, library, previous, replies, _ = document_fixture(tmp_path, monkeypatch)
    original = library.import_bytes
    class PowerLoss(BaseException):
        pass
    def interrupted(*a, **kw):
        original(*a, **kw)
        raise PowerLoss()
    monkeypatch.setattr(library, 'import_bytes', interrupted)
    with pytest.raises(PowerLoss):
        manager.run_due_checks(library=library)
    assert manager.status()['entries'][0]['pending_recovery']
    monkeypatch.setattr(library, 'import_bytes', original)
    stamp = module.time.time()
    monkeypatch.setattr(module.time, 'time', lambda: stamp + module.LEASE + 1)
    replies['https://api.github.com/repos/example/handbook/commits/main'] = RuntimeError('offline after recovery')
    reopened = module.ComponentUpdates(manager.root, catalog(document_entry()))
    row = reopened.run_due_checks(library=library)['entries'][0]
    assert row['status'] == 'check_failed' and not row['pending_recovery']
    assert library.list_documents()[0]['sha256'] == previous['sha256']
    assert len(library.list_documents(include_history=True)) == 3


def test_durable_lease_prevents_second_instance_duplicate_fetch(tmp_path, monkeypatch):
    first = module.ComponentUpdates(tmp_path / 'updates', catalog())
    second = module.ComponentUpdates(first.root, catalog())
    observations = []
    def fetch(*a, **kw):
        observations.append(second.run_due_checks(force=True))
        return b'{"info":{"version":"6.11.0"}}'
    monkeypatch.setattr(module.public_http, 'fetch_public_bytes', fetch)
    assert first.run_due_checks()['entries'][0]['status'] == 'update_available'
    assert observations[0]['checks'] == []


def test_failure_backs_off_and_never_retains_an_old_pass_as_current(tmp_path, monkeypatch):
    publisher(monkeypatch, {'info': {'version': '6.10.0'}})
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    assert manager.run_due_checks()['entries'][0]['status'] == 'no_update'
    calls = publisher(monkeypatch, RuntimeError('publisher unavailable'))
    row = manager.run_due_checks(force=True)['entries'][0]
    assert row['status'] == 'check_failed' and row['last_success']
    assert row['next_due'] - row['last_checked'] == module.BACKOFF
    assert manager.run_due_checks()['checks'] == [] and len(calls) == 1


@pytest.mark.parametrize('changes', [
    {'source_url': 'https://attacker.test/pypdf'}, {'source_url': 'http://pypi.org/project/pypdf'},
    {'source_url': 'https://secret@pypi.org/project/pypdf'}, {'id': '../escape'},
    {'package': '../escape'}, {'provider': 'shell'},
])
def test_unregistered_or_unsafe_catalog_fails_before_disk_writes(tmp_path, changes):
    with pytest.raises(ValueError):
        module.ComponentUpdates(tmp_path / 'updates', catalog(entry(**changes)))
    assert not (tmp_path / 'updates').exists()


@pytest.mark.parametrize('changes', [{'path': '../../escape.md'}, {'license_sha256': 'unknown'},
                                     {'repo': '../handbook'}, {'import_name': '../file.md'},
                                     {'rights': 'unknown'}])
def test_unsafe_document_policy_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        module.ComponentUpdates(tmp_path / 'updates', catalog(document_entry(**changes)))


def test_staging_quota_failure_preserves_existing_snapshot(tmp_path, monkeypatch):
    publisher(monkeypatch, {'info': {'version': '6.10.0'}})
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    manager.run_due_checks()
    snapshots = {path: path.read_bytes() for path in manager.root.rglob('*.snapshot')}
    publisher(monkeypatch, {'info': {'version': '6.11.0'}})
    monkeypatch.setattr(module, 'MAX_BYTES', 1)
    assert manager.run_due_checks(force=True)['entries'][0]['status'] == 'check_failed'
    assert {path: path.read_bytes() for path in snapshots} == snapshots


def test_provider_endpoints_are_metadata_only_and_pinned_rows_never_fetch(tmp_path, monkeypatch):
    rows = [entry(id='runtime', provider='github_release', repo='ollama/ollama', installed_revision='v0.1',
                  source_url='https://github.com/ollama/ollama'),
            entry(id='weights', category='model', provider='ollama_manifest', model='qwen3', tag='0.6b',
                  source_url='https://ollama.com/library/qwen3', installed_revision='sha256:'+'a'*64),
            entry(id='code', category='core', provider='pinned', source_url='', installed_revision='', reason='Signed release required.')]
    replies = {'https://api.github.com/repos/ollama/ollama/releases/latest': {'tag_name': 'v0.2', 'prerelease': False},
               'https://registry.ollama.ai/v2/library/qwen3/manifests/0.6b': {'schemaVersion': 2,
                    'config': {'digest': 'sha256:'+'b'*64, 'size': 20},
                    'layers': [{'digest': 'sha256:'+'c'*64, 'size': 600000000}]}}
    calls = publisher(monkeypatch, replies)
    result = module.ComponentUpdates(tmp_path / 'updates', catalog(*rows)).run_due_checks(max_checks=3)
    assert len(calls) == 2 and len(result['checks']) == 2
    assert {row['id']: row['status'] for row in result['entries']} == {
        'runtime': 'update_available', 'weights': 'update_available', 'code': 'check_unavailable'}
    assert not result['executable_updates_supported'] and not result['model_promotion_supported']
    assert not list((tmp_path / 'updates').rglob('*.exe'))


def test_tag_prefix_selects_numeric_stable_version_and_not_prerelease(tmp_path, monkeypatch):
    row = entry(provider='github_release', repo='python/cpython', version_prefix='v3.12.',
                installed_revision='3.12.9', source_url='https://github.com/python/cpython')
    calls = publisher(monkeypatch, [{'name': 'v3.13.2'}, {'name': 'v3.12.9'}, {'name': 'v3.12.11rc1'}, {'name': 'v3.12.10'}])
    result = module.ComponentUpdates(tmp_path / 'updates', catalog(row)).run_due_checks()
    assert result['entries'][0]['available_revision'] == 'v3.12.10'
    assert calls[0][0].endswith('/tags?per_page=100')


def test_original_summary_without_revision_is_not_called_an_upgrade(tmp_path, monkeypatch):
    row = document_entry(installed_revision='', auto_import=False)
    row.pop('path')
    publisher(monkeypatch, {'sha': NEW})
    result = module.ComponentUpdates(tmp_path / 'updates', catalog(row)).run_due_checks()
    assert result['entries'][0]['status'] == 'baseline_recorded'
    assert result['entries'][0]['details']['metadata_baseline_only'] is True
    publisher(monkeypatch, {'sha': '3' * 40})
    changed = module.ComponentUpdates(tmp_path / 'updates', catalog(row)).run_due_checks(force=True)
    assert changed['entries'][0]['status'] == 'upstream_changed'
    assert changed['entries'][0]['installed_revision'] == ''


def test_pre_cancelled_request_does_not_fetch_or_change_component(tmp_path, monkeypatch):
    calls = publisher(monkeypatch, RuntimeError('must not fetch'))
    event = threading.Event()
    event.set()
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    assert manager.run_due_checks(cancel_event=event)['checks'] == []
    assert manager.status()['entries'][0]['status'] == 'installed' and not calls


def test_cancellation_during_publisher_response_does_not_publish_snapshot(tmp_path, monkeypatch):
    event = threading.Event()
    def fetch(*a, **kw):
        assert kw['cancel_event'] is event and kw['overall_timeout'] == 8
        event.set()
        return b'{"info":{"version":"7.0"}}'
    monkeypatch.setattr(module.public_http, 'fetch_public_bytes', fetch)
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    row = manager.run_due_checks(cancel_event=event)['entries'][0]
    assert row['status'] == 'cancelled'
    assert not list(manager.root.rglob('*.snapshot'))


def test_intervening_user_version_is_not_replaced_during_rollback(tmp_path, monkeypatch):
    manager, library, _, _, _ = document_fixture(tmp_path, monkeypatch)
    user_content = b'My independently revised handbook must survive.'
    def changed_while_validating(*a, **kw):
        library.import_bytes('portable-handbook.md', user_content, source_url='https://example.org/my-revision')
        return {'results': []}
    monkeypatch.setattr(library, 'search', changed_while_validating)
    result = manager.run_due_checks(library=library)
    assert result['entries'][0]['status'] == 'check_failed'
    assert result['entries'][0]['pending_recovery'] is True
    latest = library.list_documents()[0]
    assert latest['sha256'] == sha(user_content)
    with pytest.raises(ValueError, match='Recover interrupted'):
        module.ComponentUpdates(manager.root, catalog(entry()))
    with pytest.raises(ValueError, match='before changing'):
        module.ComponentUpdates(manager.root, catalog(document_entry(ref='different-ref')))
    manager.run_due_checks(force=True, library=library)
    assert library.list_documents()[0]['sha256'] == sha(user_content)


def test_new_component_identity_does_not_reuse_prior_update_status(tmp_path, monkeypatch):
    publisher(monkeypatch, {'info': {'version': '6.11.0'}})
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    manager.run_due_checks()
    changed = entry(package='different-package', source_url='https://pypi.org/project/different-package/')
    row = module.ComponentUpdates(manager.root, catalog(changed)).status()['entries'][0]
    assert row['status'] == 'installed' and row['last_success'] is None
    assert row['available_revision'] is None
    assert len(list(manager.root.rglob('*.snapshot'))) == 1


def test_linked_update_root_is_rejected_before_database_open(tmp_path, monkeypatch):
    # The OS may prohibit creating junctions in a sandbox. Exercise the path
    # boundary deterministically; public_http has separate real transport tests.
    from pathlib import Path
    target = tmp_path / 'linked'
    original = Path.is_junction
    monkeypatch.setattr(Path, 'is_junction', lambda path: path == target or original(path))
    with pytest.raises(ValueError, match='Linked'):
        module.ComponentUpdates(target, catalog())
    assert not target.exists()


def test_force_checks_each_component_once_within_job_and_obeys_bound(tmp_path, monkeypatch):
    calls = publisher(monkeypatch, {'info': {'version': '6.11.0'}})
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    result = manager.run_due_checks(force=True, max_checks=3)
    assert len(result['checks']) == 1 and len(calls) == 1
    for bad in (0, 4, True):
        with pytest.raises(ValueError):
            manager.run_due_checks(max_checks=bad)


def test_installed_extensionless_license_can_be_safely_versioned(tmp_path, monkeypatch):
    manager, library, _, _, calls = document_fixture(tmp_path, monkeypatch,
        changes={'path': 'LICENSE', 'installed_sha256': sha(BEFORE)})
    row = manager.run_due_checks(library=library)['entries'][0]
    assert row['status'] == 'auto_applied', row
    assert calls[1][0] == calls[2][0]
    assert LICENSE in (library.root/library.list_documents()[0]['original_path']).read_bytes()


def test_shipped_signed_catalog_contract_is_accepted(tmp_path):
    from pathlib import Path
    source = Path(module.__file__).with_name('component_updates.json')
    shipped = json.loads(source.read_text(encoding='utf-8'))
    manager = module.ComponentUpdates(tmp_path / 'updates', shipped)
    rows = manager.status()['entries']
    assert len(rows) == len(shipped['entries']) <= 256
    assert any(row['automatic_apply'] for row in rows)
    assert all(row['status'] in {'installed', 'check_unavailable'} for row in rows)


def test_display_catalog_changes_preserve_applied_identity_and_allow_next_revision(tmp_path, monkeypatch):
    manager, library, _, replies, _ = document_fixture(tmp_path, monkeypatch)
    assert manager.run_due_checks(library=library)['entries'][0]['status'] == 'auto_applied'
    before = library.list_documents()[0]
    display = document_entry(label='Renamed handbook', reason='Clearer display description')
    reopened = module.ComponentUpdates(manager.root, catalog(display))
    row = reopened.run_due_checks(force=True, library=library)['entries'][0]
    assert row['status'] == 'no_update' and row['installed_revision'] == NEW
    assert library.list_documents()[0]['sha256'] == before['sha256']
    newer = '3' * 40
    replies['https://api.github.com/repos/example/handbook/commits/main'] = {'sha': newer}
    replies[f'https://raw.githubusercontent.com/example/handbook/{newer}/LICENSE'] = LICENSE
    replies[f'https://raw.githubusercontent.com/example/handbook/{newer}/docs/guide.md'] = b'Next edition describes transistor testing.'
    row = reopened.run_due_checks(force=True, library=library)['entries'][0]
    assert row['status'] == 'auto_applied' and row['installed_revision'] == newer
    assert len(library.list_documents(include_history=True)) == 3


def test_changed_import_policy_preserves_applied_provenance_and_requires_review(tmp_path, monkeypatch):
    manager, library, _, _, _ = document_fixture(tmp_path, monkeypatch)
    manager.run_due_checks(library=library)
    latest = library.list_documents()[0]
    with pytest.raises(ValueError, match='retained applied provenance'):
        module.ComponentUpdates(manager.root, catalog(document_entry(ref='unreviewed-channel')))
    old = module.ComponentUpdates(manager.root, catalog(document_entry())).status()['entries'][0]
    assert old['installed_revision'] == NEW
    assert library.list_documents()[0]['sha256'] == latest['sha256']


def test_large_bounded_pypi_metadata_is_retained_and_oversize_is_rejected(tmp_path, monkeypatch):
    payload = json.dumps({'info': {'version': '6.11.0'}, 'historic_releases': 'x' * 3_200_000}).encode()
    publisher(monkeypatch, payload)
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog())
    row = manager.run_due_checks()['entries'][0]
    assert row['status'] == 'update_available'
    saved = manager.root / row['details']['metadata_snapshot']
    assert saved.read_bytes() == payload
    publisher(monkeypatch, b'x' * (module.MAX_RESPONSE + 1))
    assert manager.run_due_checks(force=True)['entries'][0]['status'] == 'check_failed'
    assert saved.read_bytes() == payload


def test_older_publisher_numeric_release_is_not_offered_as_upgrade(tmp_path, monkeypatch):
    publisher(monkeypatch, {'info': {'version': '6.9.0'}})
    row = module.ComponentUpdates(tmp_path / 'updates', catalog()).run_due_checks()['entries'][0]
    assert row['status'] == 'no_update'
    assert row['details']['publisher_revision_is_older'] is True
    assert row['installed_revision'] == '6.10.0'


def test_owner_citation_date_change_is_not_mistaken_for_failed_update_version(tmp_path, monkeypatch):
    manager, library, _, _, _ = document_fixture(tmp_path, monkeypatch)
    def edit_date_during_validation(*a, **kw):
        current = library.list_documents()[0]
        body = (library.root/current['original_path']).read_bytes()
        library.import_bytes('portable-handbook.md', body, source_url=current['source_url'], source_date='2026-10-10')
        return {'results': []}
    monkeypatch.setattr(library, 'search', edit_date_during_validation)
    row = manager.run_due_checks(library=library)['entries'][0]
    assert row['status'] == 'check_failed' and row['pending_recovery']
    assert library.list_documents()[0]['source_date'] == '2026-10-10'
    assert len(library.list_documents(include_history=True)) == 3


def application_fixture(tmp_path, monkeypatch):
    import prometheus_control_center
    root = tmp_path / 'Prometheus-v0.8.0-dev42-test'
    location = root / 'src/prometheus_assistant/component_updates.py'
    location.parent.mkdir(parents=True)
    monkeypatch.setattr(module, '__file__', str(location))
    monkeypatch.setattr(prometheus_control_center, '__version__', '0.8.0.dev42')
    record = entry(id='application-prometheus', label='Prometheus', category='application',
                   provider='pinned', installed_revision='Prometheus-v0.8.0-dev3-old', source_url='')
    manager = module.ComponentUpdates(tmp_path / 'updates', catalog(record))
    return manager, root, record


def test_application_status_uses_running_provenance_without_mutating_catalog_or_database(tmp_path, monkeypatch):
    manager, root, record = application_fixture(tmp_path, monkeypatch)
    (root / 'RELEASE-PROVENANCE.json').write_text(json.dumps({'release': root.name, 'version': '0.8.0.dev42'}))
    with manager._db() as db:
        before = dict(db.execute('SELECT * FROM components').fetchone())
    status = manager.status()
    assert status['entries'][0]['installed_revision'] == root.name
    assert record['installed_revision'] == 'Prometheus-v0.8.0-dev3-old'
    assert manager.entries['application-prometheus']['installed_revision'] == record['installed_revision']
    assert status['entries'][0]['status'] == 'check_unavailable' and not status['network_used']
    with manager._db() as db:
        assert dict(db.execute('SELECT * FROM components').fetchone()) == before


@pytest.mark.parametrize('content', [None, '', 'not json', '[]', '{"release":"../outside","version":"0.8.0.dev42"}',
    '{"release":"Prometheus-v0.8.0-dev42-other","version":"0.8.0.dev42"}',
    '{"release":"Prometheus-v0.8.0-dev42-test","version":"0.8.0.dev1"}', ' ' * 65537],
    ids=['missing', 'empty', 'invalid-json', 'array', 'unsafe-name', 'wrong-folder', 'wrong-version', 'oversized'])
def test_unusable_release_provenance_preserves_recorded_catalog_identity(tmp_path, monkeypatch, content):
    manager, root, record = application_fixture(tmp_path, monkeypatch)
    if content is not None:
        (root / 'RELEASE-PROVENANCE.json').write_text(content)
    assert manager.status()['entries'][0]['installed_revision'] == record['installed_revision']


def test_linked_release_provenance_is_not_used_as_application_identity(tmp_path, monkeypatch):
    manager, root, record = application_fixture(tmp_path, monkeypatch)
    (root / 'RELEASE-PROVENANCE.json').write_text(json.dumps({'release': root.name, 'version': '0.8.0.dev42'}))
    from pathlib import Path
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path.name == 'RELEASE-PROVENANCE.json' or original(path))
    assert manager.status()['entries'][0]['installed_revision'] == record['installed_revision']
