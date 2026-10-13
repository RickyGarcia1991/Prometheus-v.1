from __future__ import annotations

from datetime import datetime, timezone
import json
import math
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


MAX_TURN_METADATA_BYTES = 131072


def _metadata_fields(value, *, strings=None, numbers=(), booleans=()):
    """Copy named response fields only; never recursively persist arbitrary objects."""
    if not isinstance(value, dict):
        raise ValueError('Turn metadata must contain objects.')
    result = {}
    for key, limit in (strings or {}).items():
        item = value.get(key)
        if item is not None:
            if not isinstance(item, str) or len(item) > limit:
                raise ValueError('Turn metadata text exceeds its supported bounds.')
            result[key] = item
    for key in numbers:
        item = value.get(key)
        if item is not None:
            if type(item) not in (int, float) or abs(item) > 1e15 or not math.isfinite(item):
                raise ValueError('Turn metadata numbers must be finite and bounded.')
            result[key] = item
    for key in booleans:
        item = value.get(key)
        if item is not None:
            if type(item) is not bool:
                raise ValueError('Turn metadata flags must be booleans.')
            result[key] = item
    return result


def _metadata_sources(value):
    if not isinstance(value, list) or len(value) > 5:
        raise ValueError('Turn metadata supports at most five source excerpts.')
    result = []
    for source in value:
        row = _metadata_fields(source, strings={
            'id': 32, 'title': 500, 'location': 2304, 'excerpt': 16384,
            'source_date': 100, 'imported_at': 100, 'sha256': 128},
            booleans=('is_latest', 'excerpt_truncated'))
        for key in ('version', 'version_id'):
            item = source.get(key)
            if item is not None:
                if not ((isinstance(item, str) and len(item) <= 100)
                        or (type(item) is int and abs(item) <= 1e15)):
                    raise ValueError('Turn metadata source version is invalid.')
                row[key] = item
        offsets = source.get('excerpt_characters')
        if offsets is not None:
            if (not isinstance(offsets, list) or len(offsets) != 2
                    or any(type(v) is not int for v in offsets) or not 1 <= offsets[0] <= offsets[1] <= 400000):
                raise ValueError('Invalid excerpt character offsets.')
            row['excerpt_characters'] = list(offsets)
        result.append(row)
    return result


def _metadata_search_results(value):
    """Keep source provenance from the Documents/search result shape, not internal records."""
    rows = _metadata_sources(value)
    for original, saved in zip(value, rows):
        saved.update(_metadata_fields(original, strings={
            'document_id': 64, 'source': 2000, 'path': 2000, 'source_url': 2000, 'url': 2000,
            'citation': 2304, 'revision': 200, 'license': 500, 'context': 4000,
            'summary': 16384, 'snippet': 16384, 'description': 16384, 'text': 16384},
            numbers=('line', 'context_start_line', 'context_end_line'), booleans=('context_truncated',)))
    return rows


def _response_metadata(details):
    """Keep bounded provenance exactly as supplied, excluding request/auth/image fields."""
    result = _metadata_fields(details, strings={
        'model': 240, 'source': 100, 'retrieval': 500, 'reason': 2000, 'note': 2000,
        'generation': 500, 'quality_scope': 1000},
        booleans=('generated_answer', 'evidence_is_untrusted_data', 'model_used'))
    if 'sources' in details:
        result['sources'] = _metadata_sources(details['sources'])
    if 'results' in details:
        result['results'] = _metadata_search_results(details['results'])
    if 'grounding' in details:
        grounding = details['grounding']
        saved = _metadata_fields(grounding, strings={'claim_verification': 500})
        if 'sources' in grounding:
            saved['sources'] = _metadata_sources(grounding['sources'])
        for key in ('recognized_citations', 'unrecognized_citations'):
            if key in grounding:
                items = grounding[key]
                if (not isinstance(items, list) or len(items) > 100
                        or any(not isinstance(item, str) or len(item) > 32 for item in items)):
                    raise ValueError('Turn metadata citation IDs are invalid.')
                saved[key] = list(items)
        result['grounding'] = saved
    if 'metrics' in details:
        result['metrics'] = _metadata_fields(details['metrics'], strings={'model': 240}, numbers=(
            'seconds', 'first_token_seconds', 'eval_count', 'context', 'load_seconds', 'total_seconds',
            'output_tokens', 'prompt_eval_count', 'prompt_eval_seconds', 'eval_seconds',
            'input_utf8_bytes', 'input_message_count'))
        for key in ('input_utf8_bytes', 'input_message_count'):
            count = result['metrics'].get(key)
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError('Turn metadata input counts must be nonnegative bounded integers.')
        if 'attempts' in details['metrics']:
            attempts = details['metrics']['attempts']
            if not isinstance(attempts, list) or len(attempts)>2:
                raise ValueError('Invalid generation attempts.')
            result['metrics']['attempts'] = [_metadata_fields(a, strings={'model':240,'status':30,'error':500},
                numbers=('seconds','output_tokens')) for a in attempts]
    if 'routing' in details:
        result['routing'] = _metadata_fields(details['routing'],
            strings={'role': 100, 'model': 240, 'reason': 2000},
            numbers=('attempts',), booleans=('automatic',))
    encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_TURN_METADATA_BYTES:
        raise ValueError('Turn metadata exceeds the supported storage bound.')
    return encoded


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
                CREATE TABLE IF NOT EXISTS turn_metadata (
                    turn_id INTEGER PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
                    label TEXT,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS model_events (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    model TEXT NOT NULL,
                    context_tokens INTEGER NOT NULL,
                    cpu_threads INTEGER,
                    created_at TEXT NOT NULL
                );
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
            # Optional turn metadata is additive: older v2 readers/backups remain compatible.
            # Reassigning even the same version takes SQLite's writer lock.
            # Existing history/session readers need no write when the schema is current.
            if version != 2:
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

    def save_exchange(self, session_id, prompt, reply, *, label=None, details=None):
        self.session(session_id)
        if label is not None and (not isinstance(label, str) or len(label) > 240):
            raise ValueError('Turn metadata label exceeds its supported bounds.')
        encoded = _response_metadata(details) if details is not None else None
        now = utc_now()
        with self.db:
            self.db.execute(
                "INSERT INTO turns (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, "user", prompt, now),
            )
            assistant = self.db.execute(
                "INSERT INTO turns (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, "assistant", reply, now),
            )
            if label is not None or encoded is not None:
                self.db.execute('INSERT INTO turn_metadata (turn_id, label, details_json) VALUES (?, ?, ?)',
                                (assistant.lastrowid, label, encoded))

    def record_model_use(self, session_id, model, context_tokens, cpu_threads):
        self.session(session_id)
        with self.db:
            self.db.execute(
                "INSERT INTO model_events (session_id, model, context_tokens, cpu_threads, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, model, context_tokens, cpu_threads, utc_now()),
            )

    def history(self, session_id, limit=None, *, include_metadata=False):
        self.session(session_id)
        select = 'SELECT t.role, t.content, t.created_at'
        if include_metadata:
            select += ', m.label AS _label, m.details_json AS _details'
        select += ' FROM turns t'
        if include_metadata:
            select += ' LEFT JOIN turn_metadata m ON m.turn_id = t.id'
        select += ' WHERE t.session_id = ? ORDER BY t.id'
        if limit is None:
            rows = self.db.execute(select, (session_id,)).fetchall()
        else:
            rows = self.db.execute(select+' DESC LIMIT ?', (session_id, limit)).fetchall()[::-1]
        result = []
        for row in rows:
            item = dict(row)
            label, encoded = item.pop('_label', None), item.pop('_details', None)
            if include_metadata and item['role'] == 'assistant':
                if isinstance(label, str) and len(label) <= 240:
                    item['label'] = label
                if isinstance(encoded, str) and len(encoded.encode('utf-8')) <= MAX_TURN_METADATA_BYTES:
                    try:
                        item['details'] = json.loads(_response_metadata(json.loads(encoded)))
                    except (ValueError, TypeError, RecursionError):
                        pass  # Damaged optional metadata must not hide the saved conversation.
            result.append(item)
        return result

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
            existing = self.db.execute(
                "SELECT id FROM knowledge WHERE kind = ? AND subject = ? AND value = ? AND source_type = ? AND source_ref = ? AND retention = ? AND confidence = ? AND superseded_at IS NULL",
                (kind, subject, value.strip(), source_type.strip(), source_ref.strip(), retention, confidence),
            ).fetchone()
            if existing is not None:
                return existing[0]
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
