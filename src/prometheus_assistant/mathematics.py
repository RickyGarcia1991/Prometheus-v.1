"""Read-only mathematics source retrieval, with commit and original line citations."""
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
from .hardware import resource_root

MATH_PATH='Knowledge/Mathematics/2026-10-09/mathematics-2026-10-09.sqlite3'
MATH_SHA256='4f4bc2a308499611210fdb83bf00ddadfe0682d53141c7930a3e7eeea77e41a5'
COLLECTIONS=('all','stacks-project','mathlib4')


def _database(root=None):
    root=Path(root) if root is not None else resource_root()
    if root is None: raise ValueError('Portable resource directory is unavailable.')
    root=Path(root).resolve(); path=root/MATH_PATH
    if not path.resolve().is_relative_to(root): raise ValueError('Mathematics index escapes the resource directory.')
    for component in (path,*path.parents):
        if component==root:break
        if component.is_symlink() or getattr(component,'is_junction',lambda:False)():
            raise ValueError('Mathematics index links and junctions are not accepted.')
    return path


def _verify(path):
    if not path.is_file():raise ValueError('Offline mathematics source index is not installed.')
    if path.stat().st_size>768*1024*1024:raise ValueError('Mathematics index exceeds the size limit.')
    with path.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=MATH_SHA256:raise ValueError('Mathematics index checksum mismatch; preserve it for review.')


def _connect(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    db.execute('PRAGMA cache_size=-4096')
    steps=0
    def bounded_query():
        nonlocal steps
        steps+=1
        return int(steps>10000)
    db.set_progress_handler(bounded_query,1000)
    if db.execute('PRAGMA user_version').fetchone()[0]!=1:
        db.close();raise ValueError('Unsupported mathematics index schema.')
    return db


def math_status(root=None):
    path=_database(root)
    result={'installed':path.is_file(),'path':str(path),'integrity_verified':False,
            'local_lean_proof_check_performed':False,'model_training_performed':False,
            'coverage':'Advanced mathematical reference sources; school mathematics uses LibreTexts and Wikibooks.'}
    if not path.is_file():return result
    _verify(path)
    with closing(_connect(path)) as db:
        result['sources']=[dict(r) for r in db.execute('SELECT * FROM sources ORDER BY id')]
        result['passages']=db.execute('SELECT COUNT(*) FROM passages').fetchone()[0]
    result.update(integrity_verified=True,size_bytes=path.stat().st_size)
    return result


def search_math(query,*,root=None,collection='all',limit=5):
    if not isinstance(query,str) or not query.strip() or len(query)>400:
        raise ValueError('Mathematics query must contain 1–400 characters.')
    if collection not in COLLECTIONS or type(limit) is not int or not 1<=limit<=10:
        raise ValueError('Invalid mathematics collection or result limit.')
    words=re.findall(r'[^\W_]+',query,re.UNICODE)
    if not words or len(words)>24:raise ValueError('Use 1–24 mathematics search words.')
    match=' AND '.join('"'+word+'"' for word in words)
    path=_database(root);_verify(path)
    with closing(_connect(path)) as db:
        rows=db.execute('''SELECT p.source,p.path,p.line_start,p.line_end,p.sha256,
                s.repository,s.commit_sha,s.commit_date,s.license,
                snippet(passage_search,1,'','', ' … ',56) AS excerpt
            FROM passage_search JOIN passages p ON p.id=passage_search.rowid
            JOIN sources s ON s.id=p.source
            WHERE passage_search MATCH ? AND (?='all' OR p.source=?)
            ORDER BY bm25(passage_search,5.0,1.0),p.id LIMIT ?''',
            (match,collection,collection,limit)).fetchall()
    results=[]
    for row in rows:
        item=dict(row)
        item['excerpt']=item['excerpt'][:2400]
        item['url']=item['repository']+'/blob/'+item['commit_sha']+'/'+quote(item['path'],safe='/')+f"#L{item['line_start']}-L{item['line_end']}"
        results.append(item)
    return {'query':query,'collection':collection,'offline':True,'results':results,
            'evidence_is_untrusted_data':True,'generated_answer':False,
            'notation':'Original TeX or Lean source; keyword search is not a symbolic solver.',
            'local_lean_proof_check_performed':False}
