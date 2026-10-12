"""Opening existing history must not compete for SQLite's single writer slot."""
import sqlite3

import pytest

from prometheus_assistant.memory import MemoryStore


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_existing_history_and_sessions_can_open_while_writer_is_reserved(tmp_path, monkeypatch, journal):
    path = tmp_path / "memory.db"
    with MemoryStore(path) as store:
        session = store.create_session("fixture")
        store.save_exchange(session, "Saved question", "Saved answer", label="Saved helper",
                            details={"generated_answer": True})
        assert store.db.execute("PRAGMA journal_mode=" + journal).fetchone()[0] == journal.lower()

    connect = sqlite3.connect
    writer = connect(path, timeout=0)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE turns SET content='Uncommitted answer' WHERE role='assistant'")
        # This reservation allows ordinary readers in both supported modes.
        reader = connect(path, timeout=0)
        try:
            assert reader.execute("SELECT content FROM turns WHERE role='assistant'").fetchone()[0] == "Saved answer"
        finally:
            reader.close()

        def immediate_connect(database, **kwargs):
            # Keep real SQLite locking; fail immediately instead of waiting ten seconds.
            return connect(database, **{**kwargs, "timeout": 0})

        monkeypatch.setattr(sqlite3, "connect", immediate_connect)
        with MemoryStore(path) as store:
            assert store.sessions()[0]["turn_count"] == 2
            rows = store.history(session, limit=40, include_metadata=True)
            assert [row["content"] for row in rows] == ["Saved question", "Saved answer"]
            assert rows[1]["label"] == "Saved helper"
            assert rows[1]["details"] == {"generated_answer": True}
            assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert writer.in_transaction
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.parametrize("version", [0, 1])
def test_older_schema_still_migrates_and_retains_history(tmp_path, version):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, model TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO sessions VALUES ('legacy', 'old-helper', '2026-01-01');
            INSERT INTO turns VALUES (1, 'legacy', 'user', 'Earlier question', '2026-01-01');
            INSERT INTO turns VALUES (2, 'legacy', 'assistant', 'Earlier answer', '2026-01-01');
        """)
        connection.execute("PRAGMA user_version=" + str(version))
        connection.commit()
    finally:
        connection.close()
    with MemoryStore(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [row["content"] for row in store.history("legacy", include_metadata=True)] == [
            "Earlier question", "Earlier answer"]
        store.save_exchange("legacy", "New question", "New answer", label="New helper")
        assert store.history("legacy", include_metadata=True)[-1]["label"] == "New helper"
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
