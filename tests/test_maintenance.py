import json
from pathlib import Path
import sqlite3
import threading

import pytest

from prometheus_assistant.maintenance import (
    FreshnessSchedule, create_backup, readiness_report, restore_backup, verify_backup,
)
from prometheus_assistant.public_history import snapshot_history


def response(query, *, provider, online, limit):
    assert online and limit == 5
    return dict(query=query, provider=provider, retrieved_utc='2026-10-09T12:00:00+00:00',
                response_sha256='a' * 64, results=[dict(title='Moon', url='https://images.nasa.gov/details/moon')])


def test_watch_is_explicit_persistent_and_offline_by_default(tmp_path):
    schedule = FreshnessSchedule(tmp_path / 'maintenance.sqlite3', tmp_path / 'history')
    assert schedule.status(now=10)['watches'] == []
    watched = schedule.watch('nasa', 'Moon', now=10)
    reopened = FreshnessSchedule(schedule.path, tmp_path / 'history')
    assert reopened.status(now=10)['due_count'] == 1
    assert reopened.run_due_checks(now=10, search=lambda *a, **k: pytest.fail('Network called'))['network_used'] is False
    assert reopened.status(now=10)['watches'][0]['id'] == watched['id']
    schedule.watch('nasa', 'Moon', enabled=False)
    assert not reopened.run_due_checks(online=True, now=10, search=response)['checks']


def test_success_retains_versions_and_compares_content_not_time(tmp_path):
    schedule = FreshnessSchedule(tmp_path / 'maintenance.sqlite3', tmp_path / 'history')
    schedule.watch('nasa', 'Moon', interval_days=1, now=100)
    first = schedule.run_due_checks(online=True, now=100, search=response)['checks'][0]
    assert first['baseline'] and first['changed'] is None
    assert not schedule.run_due_checks(online=True, now=101, search=response)['checks']
    def second_response(*args, **kwargs):
        result = response(*args, **kwargs)
        result['retrieved_utc'] = '2026-10-10T12:00:00+00:00'
        result['response_sha256'] = 'b' * 64
        return result
    second = schedule.run_due_checks(online=True, now=100 + 86400, search=second_response)['checks'][0]
    assert second['changed'] is False
    def third_response(*args, **kwargs):
        result = second_response(*args, **kwargs)
        result['results'][0]['title'] = 'Moon updated'
        return result
    third = schedule.run_due_checks(online=True, now=100 + 2 * 86400, search=third_response)['checks'][0]
    assert third['changed'] is True
    history = snapshot_history(tmp_path / 'history', 'nasa', 'Moon')
    assert len(history['versions']) == 3
    assert schedule.status(now=100 + 2 * 86400)['watches'][0]['last_success'] == 100 + 2 * 86400


def test_failed_refresh_keeps_success_and_backs_off(tmp_path):
    schedule = FreshnessSchedule(tmp_path / 'state.db', tmp_path / 'history')
    schedule.watch('nasa', 'Moon', interval_days=1, now=100)
    schedule.run_due_checks(online=True, now=100, search=response)
    def fail(*args, **kwargs):
        raise RuntimeError('Publisher unavailable')
    failed = schedule.run_due_checks(online=True, now=86500, search=fail)
    assert failed['checks'][0]['ok'] is False
    row = schedule.status(now=86500)['watches'][0]
    assert row['last_success'] == 100 and row['next_due'] == 86500 + 21600
    assert len(snapshot_history(tmp_path / 'history', 'nasa', 'Moon')['versions']) == 1


def test_persisted_lease_prevents_overlapping_checks(tmp_path):
    first = FreshnessSchedule(tmp_path / 'state.db', tmp_path / 'history')
    second = FreshnessSchedule(tmp_path / 'state.db', tmp_path / 'history')
    first.watch('nasa', 'Moon', now=100)
    entered, finish = threading.Event(), threading.Event()
    results = []
    def delayed(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return response(*args, **kwargs)
    thread = threading.Thread(target=lambda: results.append(first.run_due_checks(online=True, now=100, search=delayed)))
    thread.start()
    try:
        assert entered.wait(5)
        assert not second.run_due_checks(online=True, now=100, search=response)['checks']
    finally:
        finish.set()
        thread.join(5)
    assert len(results[0]['checks']) == 1


@pytest.mark.parametrize('provider,query,days', [('unknown', 'Moon', 7), ('nasa', '', 7), ('nasa', 'x\n', 7), ('nasa', 'x', 0), ('nasa', 'x', 366)])
def test_watch_validation(tmp_path, provider, query, days):
    schedule = FreshnessSchedule(tmp_path / 'state.db', tmp_path / 'history')
    with pytest.raises(ValueError):
        schedule.watch(provider, query, interval_days=days)


def make_memory(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE messages (text TEXT)')
    db.execute('INSERT INTO messages VALUES (?)', ('Saved conversation',))
    db.commit()
    return db


def test_online_sqlite_backup_restore_and_credential_exclusion(tmp_path):
    memory_path = tmp_path / 'memory.sqlite3'
    db = make_memory(memory_path)
    library = tmp_path / 'Library'
    library.mkdir()
    (library / 'notes.txt').write_text('Useful imported reference', encoding='utf-8')
    (library / '.env').write_text('TOKEN=private', encoding='utf-8')
    (library / 'credentials.json').write_text('{}', encoding='utf-8')
    (library / 'models').mkdir()
    (library / 'models' / 'large.gguf').write_bytes(b'not a useful backup target')
    try:
        backup = create_backup(memory_path, tmp_path / 'backups', data_dirs={'Library': library}, require_separate_volume=False)
        assert backup['verified'] and backup['files'] == 2
        assert backup['source_unchanged'] and backup['encrypted'] is False
        restored = restore_backup(backup['path'], tmp_path / 'restore-test')
        assert restored['verified'] and restored['live_data_overwritten'] is False
        restored_db = sqlite3.connect(Path(restored['path']) / 'memory.sqlite3')
        try:
            assert restored_db.execute('SELECT text FROM messages').fetchone()[0] == 'Saved conversation'
            assert restored_db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        finally:
            restored_db.close()
        assert (Path(restored['path']) / 'Library/notes.txt').read_text(encoding='utf-8') == 'Useful imported reference'
        assert not (Path(restored['path']) / 'Library/.env').exists()
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
        with pytest.raises(ValueError, match='nonexistent'):
            restore_backup(backup['path'], tmp_path / 'restore-test')
    finally:
        db.close()


def test_tampered_backup_refuses_restore(tmp_path):
    db = make_memory(tmp_path / 'memory.sqlite3')
    db.close()
    backup = create_backup(tmp_path / 'memory.sqlite3', tmp_path / 'backups', require_separate_volume=False)
    (Path(backup['path']) / 'memory.sqlite3').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum'):
        restore_backup(backup['path'], tmp_path / 'restore-test')
    assert not (tmp_path / 'restore-test').exists()


@pytest.mark.parametrize('unsafe', ['../outside', '/absolute', 'C:/outside', 'Library\\outside', './memory.sqlite3', '../outside ', 'Library//outside', 'Library/NUL'])
def test_manifest_traversal_is_rejected(tmp_path, unsafe):
    db = make_memory(tmp_path / 'memory.sqlite3')
    db.close()
    backup = create_backup(tmp_path / 'memory.sqlite3', tmp_path / 'backups', require_separate_volume=False)
    manifest_path = Path(backup['path']) / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files'][0]['path'] = unsafe
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='Unsafe'):
        verify_backup(backup['path'])


def test_backups_require_separate_volume_by_default(tmp_path):
    db = make_memory(tmp_path / 'memory.sqlite3')
    db.close()
    with pytest.raises(ValueError, match='separate volume'):
        create_backup(tmp_path / 'memory.sqlite3', tmp_path / 'backups')


def test_backup_output_cannot_recurse_into_source(tmp_path):
    db = make_memory(tmp_path / 'memory.sqlite3')
    db.close()
    library = tmp_path / 'Library'
    library.mkdir()
    with pytest.raises(ValueError, match='inside a source'):
        create_backup(tmp_path / 'memory.sqlite3', library / 'backups', data_dirs={'Library': library}, require_separate_volume=False)


def test_readiness_only_observes_devices(tmp_path):
    result = readiness_report(tmp_path)
    assert result['network_used'] is False
    assert result['devices']['capture_performed'] is False
    assert result['data_disk']['total_gib'] > 0
    assert len(result['manual_checks_remaining']) == 5
