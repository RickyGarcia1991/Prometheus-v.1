from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def default_memory_path():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "Prometheus" / "memory.sqlite3"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Plaintext conversation history, never automatically trusted knowledge."""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError("Unsupported memory database version; no migration attempted.")
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS turns_by_session ON turns(session_id, id);
                PRAGMA user_version = 1;
            """)
        except Exception:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def create_session(self, model):
        session_id = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session_id, model, utc_now()))
        return session_id

    def session(self, session_id):
        row = self.db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown session ID. List saved sessions before resuming.")
        return dict(row)

    def save_exchange(self, session_id, prompt, reply):
        self.session(session_id)
        now = utc_now()
        with self.db:
            self.db.executemany(
                "INSERT INTO turns (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                [(session_id, "user", prompt, now), (session_id, "assistant", reply, now)],
            )

    def history(self, session_id, limit=None):
        self.session(session_id)
        if limit is None:
            rows = self.db.execute(
                "SELECT role, content, created_at FROM turns WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT role, content, created_at FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()[::-1]
        return [dict(row) for row in rows]

    def sessions(self):
        rows = self.db.execute("""
            SELECT s.session_id, s.model, s.created_at, COUNT(t.id) AS turn_count
            FROM sessions s LEFT JOIN turns t ON s.session_id = t.session_id
            GROUP BY s.session_id ORDER BY s.created_at DESC LIMIT 100
        """).fetchall()
        return [dict(row) for row in rows]
