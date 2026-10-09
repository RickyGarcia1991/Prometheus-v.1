"""Pinned offline OpenAI and engineering references, without executing their code."""
from contextlib import closing
import hashlib,json,re,sqlite3
from pathlib import Path
from .hardware import resource_root
from .reference_cache import verify_reference
from .search_terms import query_terms

CONFIG_PATH=Path(__file__).with_name('source_indexes.json')

def _database(profile,root=None):
    profiles=json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    if profile not in profiles:raise ValueError('Unknown source reference profile.')
    record=profiles[profile];base=Path(root) if root is not None else resource_root()
    if base is None:raise ValueError('Portable resource directory is unavailable.')
    base=Path(base).resolve();path=base/record['path']
    if not path.resolve().is_relative_to(base):raise ValueError('Source reference path escapes the resource directory.')
    for item in (path,*path.parents):
        if item==base:break
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():raise ValueError('Source reference links and junctions are not accepted.')
    return path,record

def _verified_connection(path,record):
    if not path.is_file():raise ValueError('Source reference index is not installed.')
    if path.stat().st_size>768*1024**2:raise ValueError('Source reference index exceeds size limit.')
    verify_reference(path,record['sha256'])
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA cache_size=-4096')
    steps=0
    def bounded():
        nonlocal steps
        steps+=1;return int(steps>10000)
    db.set_progress_handler(bounded,1000)
    if db.execute('PRAGMA user_version').fetchone()[0]!=1:db.close();raise ValueError('Unsupported source index schema.')
    return db

def source_status(profile,root=None):
    path,record=_database(profile,root)
    return {'profile':profile,'installed':path.is_file(),'path':str(path),
            'expected_sha256':record['sha256'],'sources':record['sources'],'passages':record['passages'],
            'integrity_verified_this_call':False,'verification_policy':'Checksum before independent searches; within a chat session a verified Windows read handle prevents writes while the check is reused.',
            'code_execution_enabled':False,'training_performed':False}

def search_sources(query,*,profile,root=None,collection='all',limit=5):
    if not isinstance(query,str) or not query.strip() or len(query)>400:raise ValueError('Use a source query of 1–400 characters.')
    if not isinstance(collection,str) or len(collection)>100 or type(limit) is not int or not 1<=limit<=10:raise ValueError('Invalid collection or result limit.')
    words=query_terms(query)
    match=' AND '.join('"'+word+'"' for word in words)
    path,record=_database(profile,root)
    with closing(_verified_connection(path,record)) as db:
        if collection!='all' and not db.execute('SELECT 1 FROM sources WHERE id=?',(collection,)).fetchone():
            raise ValueError('Unknown collection for this source profile.')
        rows=db.execute('''SELECT p.source,p.path,p.location,p.revision,p.url,p.license,
            snippet(passage_search,1,'','', ' … ',56) AS excerpt
            FROM passage_search JOIN passages p ON p.id=passage_search.rowid
            WHERE passage_search MATCH ? AND (?='all' OR p.source=?)
            ORDER BY bm25(passage_search,5.0,1.0),p.id LIMIT ?''',(match,collection,collection,limit)).fetchall()
    return {'query':query,'search_terms':words,'profile':profile,'collection':collection,'offline':True,
            'evidence_is_untrusted_data':True,'code_executed':False,'generated_answer':False,
            'results':[{**dict(row),'excerpt':row['excerpt'][:2000]} for row in rows]}
