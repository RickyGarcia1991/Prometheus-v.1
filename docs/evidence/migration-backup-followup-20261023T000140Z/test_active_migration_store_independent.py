"""Independent generated-root checks; never operates on installed state."""
import pytest
import active_host_migration_core as core
import active_migration_windows_store as store


def fixture():
    root, plan = store.setup_fixture()
    return store.WindowsMigrationStore(root), plan


def advance(obj, phase):
    for name in core.PHASES[:core.PHASES.index(phase) + 1]:
        obj.write_journal(core.seal(obj.plan, name, obj.key))


def test_existing_empty_journal_is_preserved():
    obj, plan = fixture()
    obj.journal.write_bytes(b'')
    with pytest.raises(Exception):
        obj.write_journal(core.seal(plan, 'prepared', obj.key))
    assert obj.journal.read_bytes() == b''


@pytest.mark.parametrize('target', ['new', 'foreign'])
def test_invalid_initial_target_does_not_create_protected_key(target):
    root, plan = store.setup_fixture()
    (root / 'runtime' / store.PAYLOAD).write_bytes(
        store.bounded(store.chain.NEW / store.PAYLOAD) if target == 'new' else b'foreign')
    with pytest.raises(Exception):
        store.WindowsMigrationStore(root)
    assert not (root / 'ActiveHostMigration' / plan['transaction'] / 'key.dpapi').exists()


def test_changed_actual_unchanged_payload_refuses_backup():
    obj, plan = fixture()
    advance(obj, 'backup')
    name = next(iter(obj.other_pins))
    path = obj.runtime / name
    path.write_bytes(b'foreign unchanged payload')
    with pytest.raises(Exception):
        obj.backup_verified(plan)
    assert path.read_bytes() == b'foreign unchanged payload'
    assert not (obj.directory / 'backup-intent.json').exists()


@pytest.mark.parametrize('which', ['authority', 'original'])
def test_changed_authority_after_constructor_refuses_backup(which):
    obj, plan = fixture()
    advance(obj, 'backup')
    path = obj.auth if which == 'authority' else obj.original
    path.write_bytes(b'changed original metadata')
    with pytest.raises(Exception):
        obj.backup_verified(plan)
    assert path.read_bytes() == b'changed original metadata'
    assert not (obj.directory / 'backup-intent.json').exists()


@pytest.mark.parametrize('damage', ['replacement_receipt', 'backup_bytes'])
def test_post_replace_cannot_accept_inconsistent_authenticated_evidence(damage):
    obj, plan = fixture()
    advance(obj, 'backup')
    obj.backup_verified(plan)
    obj.write_journal(core.seal(plan, 'replace', obj.key))
    obj.replace_owned(plan)
    prior = obj.journal.read_bytes()
    if damage == 'replacement_receipt':
        path = obj.directory / 'replacement-receipt.json'
        path.write_bytes(obj._seal('replacement-receipt', {'nonce': '0' * 32, 'sha256': plan['new_file_sha256']}))
    else:
        path = next(obj.directory.glob('backup-*.bin'))
        path.write_bytes(b'corrupted backup')
    damaged = path.read_bytes()
    with pytest.raises(Exception):
        obj.write_journal(core.seal(plan, 'restore', obj.key))
    assert obj.journal.read_bytes() == prior
    assert path.read_bytes() == damaged
