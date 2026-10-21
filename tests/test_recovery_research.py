from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.recovery import backup_memory, restore_memory


def test_research_survives_verified_backup(tmp_path):
    source = tmp_path / 'source.db'
    backup = tmp_path / 'backup.db'
    restored = tmp_path / 'restored.db'
    with MemoryStore(source) as store:
        session = store.create_session('fixture')
        store.retain_research(session, 'robot actuators', 'Actuator source https://example.org/robot')
    metadata = backup_memory(source, backup)
    assert metadata['research_notes'] == 1
    restore_memory(backup, restored)
    with MemoryStore(restored) as store:
        assert len(store.research_notes('robot actuators')) == 1
