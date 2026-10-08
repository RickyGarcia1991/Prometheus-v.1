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
            if version not in {0, 1, 2}:
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
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('preference', 'decision', 'fact', 'instruction')),
                    subject TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
                    retention TEXT NOT NULL DEFAULT 'persistent' CHECK (retention IN ('session', 'persistent', 'until_replaced')),
                    created_at TEXT NOT NULL,
                    superseded_at TEXT
                );
                CREATE INDEX IF NOT EXISTS knowledge_active ON knowledge(kind, subject, superseded_at);
                CREATE TABLE IF NOT EXISTS research_notes (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    query TEXT NOT NULL,
                    context TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            self.db.execute("PRAGMA user_version = 2")
            self.db.commit()
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

    def remember(self, kind, subject, value, *, source_type, source_ref, confidence=1.0, retention="persistent"):
        fields = (kind, subject, value, source_type, source_ref)
        if not all(isinstance(item, str) and item.strip() for item in fields):
            raise ValueError("Knowledge fields must be non-empty strings.")
        if kind not in {"preference", "decision", "fact", "instruction"}:
            raise ValueError("Unsupported knowledge kind.")
        if retention not in {"session", "persistent", "until_replaced"}:
            raise ValueError("Unsupported retention policy.")
        confidence = float(confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1.")
        now = utc_now()
        subject = subject.strip()
        with self.db:
            if retention == "until_replaced":
                self.db.execute(
                    "UPDATE knowledge SET superseded_at = ? WHERE kind = ? AND subject = ? AND superseded_at IS NULL",
                    (now, kind, subject),
                )
            cursor = self.db.execute(
                """INSERT INTO knowledge
                (kind, subject, value, source_type, source_ref, confidence, retention, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (kind, subject, value.strip(), source_type.strip(), source_ref.strip(),
                 confidence, retention, now),
            )
        return cursor.lastrowid

    def knowledge(self, *, kind=None, subject=None, include_superseded=False):
        clauses, params = [], []
        if not include_superseded:
            clauses.append("superseded_at IS NULL")
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.execute(
            "SELECT * FROM knowledge" + where + " ORDER BY id", params
        ).fetchall()
        return [dict(row) for row in rows]

    def retain_research(self, session_id, query, context):
        """Explicitly retain untrusted evidence, separately from knowledge."""
        from hashlib import sha256
        self.session(session_id)
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("Research query must be non-empty and bounded.")
        if not isinstance(context, str) or not context.strip() or len(context) > 4000:
            raise ValueError("Research evidence must be non-empty and bounded.")
        digest = sha256(context.encode("utf-8")).hexdigest()
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO research_notes (session_id, query, context, context_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, query, context, digest, utc_now()))
        return cursor.lastrowid

    def research_notes(self, query, *, limit=8):
        """Find bounded relevant cached evidence; never promote it to knowledge."""
        import re
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("Research retrieval limit must be between 1 and 20.")
        terms = set(re.findall(r"[A-Za-z0-9_]+", query.casefold()))
        rows = self.db.execute("SELECT * FROM research_notes ORDER BY id DESC LIMIT 200").fetchall()
        ranked = []
        for row in rows:
            words = set(re.findall(r"[A-Za-z0-9_]+", row["query"].casefold()))
            score = len(terms & words)
            if score:
                ranked.append((score, row["id"], dict(row)))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [row for _, _, row in ranked[:limit]]

    def sessions(self):
        rows = self.db.execute("""
            SELECT s.session_id, s.model, s.created_at, COUNT(t.id) AS turn_count
            FROM sessions s LEFT JOIN turns t ON s.session_id = t.session_id
            GROUP BY s.session_id ORDER BY s.created_at DESC LIMIT 100
        """).fetchall()
        return [dict(row) for row in rows]
