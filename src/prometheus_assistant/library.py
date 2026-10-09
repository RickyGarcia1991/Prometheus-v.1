"""Read-only, source-attributed local reference search; never executes documents."""
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from .hardware import resource_root

MEDLINE_PATH = 'Knowledge/Medical/MedlinePlus-2026-10-08/medlineplus-2026-10-08.sqlite3'
MEDLINE_SHA256 = '6de16355e8c5dc2a75c5391f189edbf317eb80f35d02bc1e14997b76d204766c'
SNAPSHOT_DATE = '2026-10-08'
ATTRIBUTION = 'Source: MedlinePlus, National Library of Medicine.'


def _database(root=None):
    resources = Path(root) if root is not None else resource_root()
    if resources is None:
        raise ValueError('Portable resource directory is unavailable.')
    resources = Path(resources).resolve()
    path = resources / MEDLINE_PATH
    for item in (path, *path.parents):
        if item == resources: break
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('Reference library links or junctions are not accepted.')
    if not path.resolve().is_relative_to(resources):
        raise ValueError('Reference library is outside the portable resource directory.')
    return path


def _verify(path):
    if not path.is_file():
        raise ValueError('MedlinePlus reference snapshot is not installed.')
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Reference library exceeds its size limit.')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024): digest.update(chunk)
    if digest.hexdigest() != MEDLINE_SHA256:
        raise ValueError('Reference library checksum mismatch; preserve this file for review and restore a verified copy.')


def _connect(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    steps = 0
    def bounded_query():
        nonlocal steps
        steps += 1
        return int(steps > 2000)
    connection.set_progress_handler(bounded_query, 1000)
    return connection


def library_status(root=None):
    path = _database(root)
    result = {'installed': path.is_file(), 'path': str(path), 'snapshot_date': SNAPSHOT_DATE,
              'attribution': ATTRIBUTION, 'integrity_verified': False,
              'days_since_snapshot': max(0, (date.today()-date.fromisoformat(SNAPSHOT_DATE)).days),
              'coverage': 'MedlinePlus health-topic reference; additional subjects use the broader source catalog and Wikimedia archives.',
              'factual_accuracy_guaranteed': False, 'model_training_performed': False}
    if not path.is_file(): return result
    _verify(path)
    with closing(_connect(path)) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Unsupported reference schema.')
        result['topics_by_language'] = dict(db.execute('SELECT language,COUNT(*) FROM topics GROUP BY language'))
        result['topics_without_summary'] = db.execute("SELECT COUNT(*) FROM topics WHERE body='' ").fetchone()[0]
    result.update(integrity_verified=True, size_bytes=path.stat().st_size)
    return result


def search_library(query, *, root=None, language='English', limit=5):
    if not isinstance(query, str) or not query.strip() or len(query) > 400:
        raise ValueError('Library query must contain 1–400 characters.')
    if language not in ('English', 'Spanish') or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('Invalid library language or result limit.')
    # Never pass user FTS operators, column selectors, SQL, or wildcards through.
    words = re.findall(r'[^\W_]+', query, re.UNICODE)
    if not words or len(words) > 24:
        raise ValueError('Use 1–24 search words.')
    match = ' AND '.join('"'+word+'"' for word in words)
    path = _database(root); _verify(path)
    with closing(_connect(path)) as db:
        rows = db.execute('''SELECT t.id,t.title,t.url,t.created,t.body,
                snippet(topic_search,1,'','', ' … ',48) AS excerpt
            FROM topic_search JOIN topics t ON t.id=topic_search.rowid
            WHERE topic_search MATCH ? AND t.language=?
            ORDER BY bm25(topic_search,8.0,1.0,3.0),t.id LIMIT ?''', (match, language, limit)).fetchall()
    return {'query': query, 'language': language, 'snapshot_date': SNAPSHOT_DATE,
            'attribution': ATTRIBUTION, 'evidence_is_untrusted_data': True,
            'generated_answer': False, 'requires_current_source_check_for_clinical_use': True,
            'results': [{'title': r['title'], 'url': r['url'],
                         'topic_created_date': r['created'], 'excerpt': r['excerpt'][:1500],
                         'summary_available': bool(r['body']),
                         'source_id': 'medlineplus:'+str(r['id'])} for r in rows]}
