"""Versioned local documents with bounded imports and optional real embeddings.

Imported content is evidence, never executable instructions. Embedding clients
are opt-in: without a client or sufficient resources search remains local FTS.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid
import zipfile
from xml.etree import ElementTree

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
MAX_DOCX_EXPANDED = 16 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_PASSAGES = 384
MAX_VECTOR_DIMENSION = 4096
MAX_SEMANTIC_PASSAGES = 5000
SUPPORTED_EXTENSIONS = ('.txt', '.md', '.csv', '.pdf', '.docx')
_STOP = frozenset('a an the how what where when why can could do does did i you we is are was were to of for in on with please me my about use using explain show find'.split())


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _no_links(path):
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
            raise ValueError('Document paths cannot contain symbolic links or junctions.')


def _name(value):
    if (not isinstance(value, str) or not value or len(value) > 180
            or value.strip() != value or value.endswith('.')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value in {'.', '..'}
            or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', value)):
        raise ValueError('Use a plain portable filename, without folders or special path characters.')
    if Path(value).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError('Supported documents: UTF-8 TXT, Markdown, CSV, PDF and DOCX.')
    return value


def _metadata(source_url, source_date):
    if source_url is not None:
        if not isinstance(source_url, str) or len(source_url) > 2000 or any(ord(c) < 32 for c in source_url):
            raise ValueError('Invalid source URL.')
        parsed = urlsplit(source_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Source URLs must be HTTP(S) links without credentials.')
    if source_date is not None:
        if not isinstance(source_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', source_date):
            raise ValueError('Source date must be YYYY-MM-DD, or omitted when unknown.')
        try:
            date.fromisoformat(source_date)
        except ValueError as error:
            raise ValueError('Source date must be a valid YYYY-MM-DD date.') from error


def _validate_text(text):
    if not text.strip():
        raise ValueError('The document has no extractable text. Scanned PDFs need OCR before import.')
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f'Extracted text exceeds the {MAX_TEXT_CHARS:,}-character import limit.')
    if '\0' in text:
        raise ValueError('Binary content is not a UTF-8 text document.')
    return text


def _docx_text(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(e.file_size for e in entries) > MAX_DOCX_EXPANDED:
                raise ValueError('DOCX archive exceeds the expanded size or entry limit.')
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (entry.filename in names or path.is_absolute() or '..' in path.parts
                        or '\\' in entry.orig_filename or ':' in entry.filename
                        or any(ord(char) < 32 for char in entry.orig_filename)
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise ValueError('DOCX contains unsafe, duplicate or encrypted archive entries.')
                names.add(entry.filename)
                if entry.file_size > max(1, entry.compress_size) * 100:
                    raise ValueError('DOCX compression ratio exceeds the import limit.')
            if 'word/document.xml' not in names:
                raise ValueError('DOCX has no main document XML.')
            entry = archive.getinfo('word/document.xml')
            if entry.file_size > MAX_XML_BYTES:
                raise ValueError('DOCX document XML exceeds the import limit.')
            with archive.open(entry) as member:
                data = member.read(MAX_XML_BYTES + 1)
            try:
                xml_text = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
            except UnicodeError as error:
                raise ValueError('DOCX XML must use UTF-8 or BOM-marked UTF-16.') from error
            if len(data) > MAX_XML_BYTES or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', xml_text, re.I):
                raise ValueError('DOCX XML declarations or size are unsupported.')
            # All relationships, images, embedded objects and macros are ignored.
            root = ElementTree.fromstring(xml_text)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            paragraphs = []
            for paragraph in root.iter(ns + 'p'):
                paragraphs.append(''.join(node.text or '' for node in paragraph.iter(ns + 't')))
            return _validate_text('\n'.join(paragraphs))
    except (zipfile.BadZipFile, ElementTree.ParseError, RuntimeError, OSError) as error:
        raise ValueError('The DOCX archive or document XML is invalid.') from error


def _limit_pdf_process():
    """Bound the short-lived parser's address space/commit, including malformed input."""
    budget = 192 * 1024 * 1024
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [('basic', BasicLimit), ('io', IOCounters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimit()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.process_memory = budget
    if (not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())):
        raise ValueError('Cannot establish the PDF parser memory limit on this host.')
    # This worker holds the handle until process exit; closing it here would drop the limit.


def _pdf_worker(path):
    """Run only in a short-lived child; stream decompression is explicitly capped."""
    _limit_pdf_process()
    try:
        import pypdf
        import pypdf.filters as filters
    except ImportError as error:
        raise ValueError('PDF import needs the bundled pypdf package.') from error
    for setting in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                    'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'MAX_DECLARED_STREAM_LENGTH'):
        if not hasattr(filters, setting):
            raise ValueError('The installed PDF parser does not provide required decompression limits.')
        setattr(filters, setting, MAX_XML_BYTES)
    reader = pypdf.PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDFs must be decrypted before importing.')
    if len(reader.pages) > 100:
        raise ValueError('PDF import is limited to 100 pages per document.')
    sections = []
    total = 0
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text(extraction_mode='plain') or ''
        total += len(text)
        if total > MAX_TEXT_CHARS:
            raise ValueError('PDF extracted text exceeds the import limit.')
        if text.strip():
            sections.append((f'page {index}', text))
    if not sections:
        raise ValueError('The PDF has no extractable text. Scanned PDFs need OCR before import.')
    return sections


def _extract(content, suffix, temp_dir=None):
    if suffix == '.docx':
        return [('document', _docx_text(content))]
    if suffix != '.pdf':
        try:
            text = content.decode('utf-8-sig')
        except UnicodeError as error:
            raise ValueError('Text, Markdown and CSV imports must use UTF-8 encoding.') from error
        return [('text', _validate_text(text))]
    if not content.startswith(b'%PDF-'):
        raise ValueError('The file does not have a PDF header.')
    # Separate process bounds parser duration and isolates parser failures. Files
    # are never opened in a viewer, and embedded actions are never executed.
    with tempfile.TemporaryDirectory(prefix='prometheus-pdf-', dir=temp_dir) as folder:
        path = Path(folder) / 'source.pdf'
        path.write_bytes(content)
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), '--extract-pdf', str(path)],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    if result.returncode != 0:
        try:
            message = json.loads(result.stdout).get('error', 'PDF extraction failed.')
        except (ValueError, AttributeError):
            message = 'PDF extraction failed.'
        raise ValueError(str(message)[:500])
    if len(result.stdout) > MAX_TEXT_CHARS * 8:
        raise ValueError('PDF extraction output exceeds the import limit.')
    return json.loads(result.stdout)['sections']


def _passages(sections):
    result = []
    for label, text in sections:
        _validate_text(text)
        start = 0
        while start < len(text):
            end = min(len(text), start + 1600)
            if end < len(text):
                boundary = text.rfind('\n', start + 1000, end)
                if boundary > start:
                    end = boundary
            body = text[start:end].strip()
            if body:
                location = f'{label}, characters {start + 1}-{end}'
                result.append((location, body))
            if len(result) > MAX_PASSAGES:
                raise ValueError('Document exceeds the passage limit; split it into smaller documents.')
            if end == len(text):
                break
            start = end - 160
    return result


def _vector(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_VECTOR_DIMENSION:
        raise ValueError('Embedding dimensions are invalid.')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError('Embedding values must be finite numbers.')
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Embedding vector has no finite direction.')
    return [value / norm for value in values]


class OllamaEmbeddingClient:
    """Uses actual installed-model digests and Ollama's local /api/embed endpoint."""

    def __init__(self, base_url='http://127.0.0.1:11434', model='qwen3-embedding:0.6b', timeout=45):
        from .ollama import OllamaClient
        self.client = OllamaClient(base_url=base_url, model=model, timeout=timeout, num_ctx=2048, num_thread=2)

    def identity(self):
        entry = self.client.ensure_local_model()
        digest = entry.get('digest')
        if not isinstance(digest, str) or not re.fullmatch(r'(?:sha256:)?[a-fA-F0-9]{64}', digest):
            raise ValueError('Local embedding model has no verifiable model digest.')
        return f'ollama:{self.client.model}@{digest}:ctx2048'

    def embed(self, texts):
        if not 1 <= len(texts) <= 2 or any(not isinstance(t, str) or len(t) > 4000 for t in texts):
            raise ValueError('Embedding requests must contain 1–2 bounded texts.')
        result = self.client._request('/api/embed', {
            'model': self.client.model, 'input': texts, 'truncate': False, 'keep_alive': '10s',
            'options': {'num_ctx': 2048, 'num_thread': 2},
        })
        vectors = result.get('embeddings')
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ValueError('Ollama returned an invalid embedding batch.')
        return vectors


class DocumentLibrary:
    def __init__(self, data_dir, embedding_client=None, embedding_allowed=None):
        requested = Path(data_dir).absolute()
        _no_links(requested)
        requested.mkdir(parents=True, exist_ok=True)
        self.root = requested.resolve()
        self.path = self.root / 'documents.sqlite3'
        self.originals = self.root / 'originals'
        _no_links(self.path)
        _no_links(self.originals)
        self.originals.mkdir(exist_ok=True)
        self.embedding_client = embedding_client
        self.embedding_allowed = embedding_allowed or (lambda: True)
        with closing(self._connect()) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError('Unsupported document library schema.')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    sha256 TEXT NOT NULL, imported_at TEXT NOT NULL, source_url TEXT, source_date TEXT,
                    bytes INTEGER NOT NULL, passage_count INTEGER NOT NULL, original_path TEXT NOT NULL,
                    original_filename TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS versions_document ON versions(document_id,id);
                CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id),
                    title TEXT NOT NULL, location TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS passages_version ON passages(version_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS passage_search USING fts5(title,body,content=passages,content_rowid=id,tokenize='unicode61');
                CREATE TABLE IF NOT EXISTS embedding_spaces(identity TEXT PRIMARY KEY, dimension INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS embeddings(passage_id INTEGER NOT NULL REFERENCES passages(id), identity TEXT NOT NULL,
                    dimension INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(passage_id,identity));
            ''')
            if 'original_filename' not in {row['name'] for row in db.execute('PRAGMA table_info(versions)')}:
                db.execute("ALTER TABLE versions ADD COLUMN original_filename TEXT NOT NULL DEFAULT ''")
                db.execute('UPDATE versions SET original_filename=(SELECT name FROM documents WHERE documents.id=versions.document_id)')
            db.execute('PRAGMA user_version=2')
            db.commit()

    def _connect(self):
        _no_links(self.root)
        _no_links(self.path)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA cache_size=-2048')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def _embedding_identity(self):
        if self.embedding_client is None:
            return None, 'not_configured'
        try:
            if not self.embedding_allowed():
                return None, 'resource_deferred'
            identity = self.embedding_client.identity()
            if not isinstance(identity, str) or not identity or len(identity) > 500:
                raise ValueError('Invalid embedding model identity.')
            return identity, 'ready'
        except Exception:
            return None, 'unavailable'

    @staticmethod
    def _record(row):
        result = dict(row)
        if 'id' in result:
            result['version_id'] = result.pop('id')
        return result

    def list_documents(self, *, include_history=False, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Document list limit must be 1–500.')
        with closing(self._connect()) as db:
            rows = db.execute('''SELECT v.*, d.name AS title,
                    v.id = (SELECT max(v2.id) FROM versions v2 WHERE v2.document_id=v.document_id) AS is_latest
                FROM versions v JOIN documents d ON d.id=v.document_id
                WHERE (? OR v.id=(SELECT max(v2.id) FROM versions v2 WHERE v2.document_id=v.document_id))
                ORDER BY v.id DESC LIMIT ?''', (bool(include_history), limit)).fetchall()
            return [self._record(row) for row in rows]

    def import_file(self, path, **metadata):
        source = Path(path).absolute()
        _no_links(source)
        _name(source.name)
        if not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Document must be a regular file no larger than 8 MiB.')
        with source.open('rb') as stream:
            content = stream.read(MAX_FILE_BYTES + 1)
        return self.import_bytes(source.name, content, **metadata)

    def import_bytes(self, name, content, *, source_url=None, source_date=None, document_id=None):
        name = _name(name)
        _metadata(source_url, source_date)
        if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_FILE_BYTES:
            raise ValueError('Document must contain 1 byte to 8 MiB.')
        if document_id is not None and (not isinstance(document_id, str) or not re.fullmatch(r'[a-f0-9]{32}', document_id)):
            raise ValueError('Invalid document ID.')
        try:
            sections = _extract(content, Path(name).suffix.lower(), self.root)
        except subprocess.TimeoutExpired as error:
            raise ValueError('PDF extraction exceeded its 20-second time limit.') from error
        passages = _passages(sections)
        digest = hashlib.sha256(content).hexdigest()
        original_name = name
        original = self.originals / (digest + Path(name).suffix.lower())
        _no_links(self.originals)
        _no_links(original)
        with closing(self._connect()) as db, db:
            # Serialize naming and version updates across multiple local callers.
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM documents WHERE id=?' if document_id else
                                  'SELECT * FROM documents WHERE name_key=?',
                                  (document_id or name.casefold(),)).fetchone()
            if document_id and not existing:
                raise ValueError('Unknown document ID.')
            if original.exists():
                if original.stat().st_size != len(content) or hashlib.sha256(original.read_bytes()).hexdigest() != digest:
                    raise ValueError('A retained original failed its checksum; restore it before importing.')
            else:
                with original.open('xb') as destination:
                    destination.write(content)
            if existing:
                document_id = existing['id']
                name = existing['name']
            else:
                document_id = uuid.uuid4().hex
                db.execute('INSERT INTO documents VALUES (?,?,?)', (document_id, name, name.casefold()))
            latest = db.execute('SELECT * FROM versions WHERE document_id=? ORDER BY id DESC LIMIT 1', (document_id,)).fetchone()
            duplicate = bool(latest and latest['sha256'] == digest and latest['source_url'] == source_url
                             and latest['source_date'] == source_date and latest['original_filename'] == original_name)
            if duplicate:
                record = self._record(latest)
            else:
                cursor = db.execute('''INSERT INTO versions(document_id,sha256,imported_at,source_url,source_date,bytes,passage_count,original_path,original_filename)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (document_id, digest, _utc_now(), source_url, source_date,
                                                  len(content), len(passages), str(original.relative_to(self.root)), original_name))
                version_id = cursor.lastrowid
                for location, body in passages:
                    cursor = db.execute('INSERT INTO passages(version_id,title,location,body) VALUES (?,?,?,?)',
                                        (version_id, name, location, body))
                    db.execute('INSERT INTO passage_search(rowid,title,body) VALUES (?,?,?)', (cursor.lastrowid, name, body))
                record = self._record(db.execute('SELECT * FROM versions WHERE id=?', (version_id,)).fetchone())
        embedded = self.index_missing_embeddings(limit=32, document_id=document_id)
        return {**record, 'title': name, 'duplicate': duplicate, 'is_latest': True,
                'embedding_status': embedded['status'], 'embeddings': embedded}

    def index_missing_embeddings(self, *, limit=32, document_id=None, force=False):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('Embedding indexing limit must be 1–128 passages.')
        if document_id is not None and (not isinstance(document_id, str) or not re.fullmatch(r'[a-f0-9]{32}', document_id)):
            raise ValueError('Invalid document ID.')
        identity, status = self._embedding_identity()
        result = {'status': status, 'indexed': 0, 'pending': 0, 'model_identity': identity, 'dimension': None}
        with closing(self._connect()) as db:
            eligible = '''FROM passages p JOIN versions v ON v.id=p.version_id
                WHERE v.id=(SELECT max(v2.id) FROM versions v2 WHERE v2.document_id=v.document_id)
                AND (? IS NULL OR v.document_id=?)'''
            args = (document_id, document_id)
            if identity is None:
                result['pending'] = db.execute('SELECT count(*) ' + eligible, args).fetchone()[0]
                return result
            if force:
                with db:
                    db.execute('DELETE FROM embeddings WHERE identity=? AND passage_id IN (SELECT p.id ' + eligible + ')', (identity, *args))
            missing = eligible + ' AND NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.passage_id=p.id AND e.identity=?)'
            rows = db.execute('SELECT p.id,p.body ' + missing + ' ORDER BY p.id LIMIT ?', (*args, identity, limit)).fetchall()
            for offset in range(0, len(rows), 2):
                batch = rows[offset:offset + 2]
                try:
                    if not self.embedding_allowed():
                        result['status'] = 'resource_deferred'
                        break
                    raw = self.embedding_client.embed([row['body'] for row in batch])
                    if not isinstance(raw, list) or len(raw) != len(batch):
                        raise ValueError('Invalid embedding batch.')
                    vectors = [_vector(vector) for vector in raw]
                    dimension = len(vectors[0])
                    if any(len(vector) != dimension for vector in vectors):
                        raise ValueError('Embedding dimensions changed within a batch.')
                    with db:
                        previous = db.execute('SELECT dimension FROM embedding_spaces WHERE identity=?', (identity,)).fetchone()
                        if previous and previous['dimension'] != dimension:
                            db.execute('DELETE FROM embeddings WHERE identity=?', (identity,))
                        db.execute('INSERT OR REPLACE INTO embedding_spaces VALUES (?,?)', (identity, dimension))
                        for row, vector in zip(batch, vectors):
                            db.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)',
                                       (row['id'], identity, dimension, struct.pack(f'<{dimension}f', *vector)))
                    result['indexed'] += len(batch)
                    result['dimension'] = dimension
                except Exception:
                    result['status'] = 'unavailable'
                    break
            result['pending'] = db.execute('SELECT count(*) ' + missing, (*args, identity)).fetchone()[0]
        if result['status'] == 'ready':
            result['status'] = 'partial' if result['pending'] else 'indexed'
        return result

    def search(self, query, *, limit=5, include_history=False, semantic=True):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError('Use a document query of 1–4000 characters.')
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Search result limit must be 1–20.')
        terms = list(dict.fromkeys(word.casefold() for word in re.findall(r'[^\W_]+', query)
                                   if word.casefold() not in _STOP))[:24]
        if not terms:
            raise ValueError('The query needs at least one meaningful search term.')
        match = ' OR '.join('"' + term + '"' for term in terms)
        with closing(self._connect()) as db:
            keyword = db.execute('''SELECT p.id,bm25(passage_search,2.0,1.0) AS rank
                FROM passage_search JOIN passages p ON p.id=passage_search.rowid JOIN versions v ON v.id=p.version_id
                WHERE passage_search MATCH ? AND (? OR v.id=(SELECT max(v2.id) FROM versions v2 WHERE v2.document_id=v.document_id))
                ORDER BY rank,p.id LIMIT 60''', (match, bool(include_history))).fetchall()
            identity, semantic_status = self._embedding_identity() if semantic else (None, 'disabled')
            semantic_ranked = []
            scanned = 0
            if identity:
                try:
                    stored = db.execute('SELECT dimension FROM embedding_spaces WHERE identity=?', (identity,)).fetchone()
                    if not stored:
                        semantic_status = 'not_indexed'
                    else:
                        raw = self.embedding_client.embed([query])
                        if not isinstance(raw, list) or len(raw) != 1:
                            raise ValueError('Invalid query embedding.')
                        vector = _vector(raw[0])
                        if len(vector) != stored['dimension']:
                            with db:
                                db.execute('DELETE FROM embeddings WHERE identity=?', (identity,))
                                db.execute('DELETE FROM embedding_spaces WHERE identity=?', (identity,))
                            semantic_status = 'dimension_changed_reindex_required'
                        else:
                            rows = db.execute('''SELECT e.passage_id,e.dimension,e.vector FROM embeddings e
                                JOIN passages p ON p.id=e.passage_id JOIN versions v ON v.id=p.version_id
                                WHERE e.identity=? AND (? OR v.id=(SELECT max(v2.id) FROM versions v2 WHERE v2.document_id=v.document_id))
                                ORDER BY e.passage_id DESC LIMIT ?''', (identity, bool(include_history), MAX_SEMANTIC_PASSAGES))
                            for row in rows:
                                if row['dimension'] != len(vector) or len(row['vector']) != 4 * len(vector):
                                    continue
                                other = struct.unpack(f'<{len(vector)}f', row['vector'])
                                score = sum(a * b for a, b in zip(vector, other))
                                scanned += 1
                                if math.isfinite(score) and score >= 0.25:
                                    semantic_ranked.append((row['passage_id'], score))
                            semantic_ranked.sort(key=lambda item: (-item[1], item[0]))
                            semantic_ranked = semantic_ranked[:60]
                            semantic_status = 'ready' if scanned else 'not_indexed'
                except Exception:
                    semantic_status = 'unavailable'
            scores, signals = {}, {}
            for index, row in enumerate(keyword, 1):
                scores[row['id']] = 1 / (60 + index)
                signals[row['id']] = {'keyword_score': float(row['rank']), 'semantic_score': None}
            for index, (passage_id, similarity) in enumerate(semantic_ranked, 1):
                scores[passage_id] = scores.get(passage_id, 0) + 1 / (60 + index)
                signals.setdefault(passage_id, {'keyword_score': None, 'semantic_score': None})['semantic_score'] = similarity
            results = []
            for passage_id in sorted(scores, key=lambda key: (-scores[key], key))[:limit]:
                row = db.execute('''SELECT v.*,d.name AS title,p.location,p.body,
                    v.id=(SELECT max(v2.id) FROM versions v2 WHERE v2.document_id=v.document_id) AS is_latest
                    FROM passages p JOIN versions v ON v.id=p.version_id JOIN documents d ON d.id=v.document_id WHERE p.id=?''', (passage_id,)).fetchone()
                record = self._record(row)
                body = record.pop('body')
                signal = signals[passage_id]
                mode = 'hybrid' if signal['keyword_score'] is not None and signal['semantic_score'] is not None else (
                    'semantic' if signal['semantic_score'] is not None else 'keyword')
                results.append({**record, **signal, 'excerpt': body, 'score': scores[passage_id], 'retrieval': mode})
        retrieval = 'hybrid' if keyword and semantic_ranked else 'semantic' if semantic_ranked else 'keyword'
        return {'query': query, 'search_terms': terms, 'retrieval': retrieval, 'semantic_status': semantic_status,
                'model_identity': identity, 'semantic_passages_scanned': scanned,
                'semantic_scan_limit': MAX_SEMANTIC_PASSAGES, 'include_history': bool(include_history),
                'generated_answer': False, 'evidence_is_untrusted_data': True, 'results': results}


if __name__ == '__main__':
    # Add only this release's signed source directory for its bundled pypdf.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--extract-pdf':
            raise ValueError('Unsupported document helper operation.')
        print(json.dumps({'sections': _pdf_worker(sys.argv[2])}))
    except Exception as error:
        print(json.dumps({'error': str(error)[:500]}))
        raise SystemExit(1)
