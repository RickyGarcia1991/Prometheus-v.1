import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.recovery import backup_memory, restore_memory
from prometheus_assistant.documents import search_documents


def test_backup_restore_preserves_history_and_refuses_overwrite(tmp_path):
    source, backup, restored = [tmp_path / n for n in ('live.db', 'backup.db', 'restored.db')]
    with MemoryStore(source) as store:
        session = store.create_session('local')
        store.save_exchange(session, 'café?', 'yes')
        assert backup_memory(source, backup)['turns'] == 2
    restore_memory(backup, restored)
    with MemoryStore(restored) as store:
        assert store.history(session)[0]['content'] == 'café?'
    with pytest.raises(FileExistsError):
        restore_memory(backup, source)
    backup.write_bytes(backup.read_bytes() + b'tamper')
    with pytest.raises(ValueError, match='checksum'):
        restore_memory(backup, tmp_path / 'bad.db')


def test_invalid_database_rejected_without_destination(tmp_path):
    source = tmp_path / 'invalid.db'
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):
        backup_memory(source, tmp_path / 'backup.db')
    assert not (tmp_path / 'backup.db').exists()


def test_backup_deadline_cleans_incomplete_destination(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    with MemoryStore(source):
        pass
    with pytest.raises(ValueError, match='timed out'):
        backup_memory(source, target, timeout_seconds=-1)
    assert not target.exists()
    assert not target.with_suffix('.db.json').exists()


def test_search_returns_exact_citations_and_no_invented_answer(tmp_path):
    (tmp_path / 'plan.md').write_text('Plan\nDelivery target: November 12.\nBudget: 200 dollars.', encoding='utf-8')
    result = search_documents(tmp_path, 'delivery target')
    assert result['results'][0] == {'source': 'plan.md', 'line': 2,
        'excerpt': 'Delivery target: November 12.', 'matched_terms': 2}
    assert search_documents(tmp_path, 'missing fact')['results'] == []
    assert result['generated_answer'] is False


def test_search_skips_unsupported_invalid_and_large_files(tmp_path):
    (tmp_path / 'private.sqlite3').write_text('delivery target')
    (tmp_path / 'invalid.txt').write_bytes(b'\xff')
    (tmp_path / 'large.md').write_bytes(b'x' * 1_000_001)
    result = search_documents(tmp_path, 'delivery')
    assert result['results'] == []
    assert set(result['skipped_files']) == {'invalid.txt', 'large.md'}


def test_search_rejects_oversized_collection_and_empty_query(tmp_path):
    with pytest.raises(ValueError, match='query'):
        search_documents(tmp_path, ' ')
    for i in range(501):
        (tmp_path / f'{i}.txt').write_text('target')
    with pytest.raises(ValueError, match='500'):
        search_documents(tmp_path, 'target')


def test_search_excludes_hidden_documents_and_outside_links(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    hidden = docs / '.private'
    hidden.mkdir()
    (hidden / 'secret.txt').write_text('target')
    outside = tmp_path / 'outside.txt'
    outside.write_text('target')
    try:
        (docs / 'linked.txt').symlink_to(outside)
    except OSError:
        pass  # Link creation needs Windows privilege; hidden-directory check still runs.
    assert search_documents(docs, 'target')['results'] == []


def test_backup_restore_preserves_structured_knowledge(tmp_path):
    source, backup, restored = [tmp_path / name for name in ("live.db", "backup.db", "restored.db")]
    with MemoryStore(source) as store:
        store.remember(
            "preference", "response.detail", "step-by-step",
            source_type="user_statement", source_ref="turn:42",
            retention="until_replaced",
        )
    metadata = backup_memory(source, backup)
    assert metadata["schema_version"] == 2
    assert metadata["knowledge"] == 1
    restore_memory(backup, restored)
    with MemoryStore(restored) as store:
        rows = store.knowledge(kind="preference", subject="response.detail")
        assert len(rows) == 1
        assert rows[0]["value"] == "step-by-step"
        assert rows[0]["source_ref"] == "turn:42"
