"""Append-only, content-addressed public evidence snapshots; never executable."""
import hashlib,json,os,uuid
from pathlib import Path

MAX_SNAPSHOT=2_000_000

def save_document_version(root,url,body,metadata):
    if not isinstance(body,bytes) or len(body)>10_000_000 or not body.startswith(b'%PDF-'):
        raise ValueError('Expected a bounded official PDF.')
    folder=_folder(root,'official-pdf',url,True)
    digest=hashlib.sha256(body).hexdigest();destination=folder/(digest+'.pdf')
    if destination.exists():
        if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest()!=digest:raise ValueError('Saved PDF checksum mismatch.')
    else:
        temporary=folder/('.'+uuid.uuid4().hex+'.partial')
        try:
            with temporary.open('xb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
            if os.name=='nt':temporary.rename(destination)
            else:os.link(temporary,destination)
        finally:temporary.unlink(missing_ok=True)
    description=destination.with_suffix('.metadata.json')
    if not description.exists():
        with description.open('x',encoding='utf-8') as stream:json.dump(dict(source=url,sha256=digest,**metadata),stream,indent=2)
    return dict(path=str(destination),sha256=digest,older_versions_preserved=True,automatic_activation=False)

def _folder(root,provider,query,create=False):
    if not isinstance(provider,str) or not isinstance(query,str) or len(query)>400:raise ValueError('Invalid snapshot key.')
    base=Path(root).absolute()
    key=hashlib.sha256(json.dumps([provider,query.strip()],ensure_ascii=False).encode()).hexdigest()
    folder=base/key
    for p in (folder,*folder.parents):
        if p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise ValueError('Linked snapshot paths are refused.')
    if create:folder.mkdir(parents=True,exist_ok=True)
    return folder

def save_snapshot(root,result):
    if not isinstance(result,dict) or not isinstance(result.get('results'),list) or not result.get('retrieved_utc'):
        raise ValueError('A complete public search response is required.')
    data=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')
    if len(data)>MAX_SNAPSHOT:raise ValueError('Snapshot exceeds its size limit.')
    folder=_folder(root,result['provider'],result['query'],True)
    digest=hashlib.sha256(data).hexdigest();destination=folder/(digest+'.json')
    if destination.exists():
        if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest()!=digest:raise ValueError('Existing snapshot failed verification.')
        return dict(saved=False,path=str(destination),sha256=digest,older_versions_preserved=True)
    temporary=folder/('.'+uuid.uuid4().hex+'.partial')
    try:
        with temporary.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        # Windows rename refuses an existing destination. On POSIX use an
        # exclusive hard-link publication instead of an overwriting rename.
        if os.name=='nt':temporary.rename(destination)
        else:os.link(temporary,destination)
    finally:temporary.unlink(missing_ok=True)
    return dict(saved=True,path=str(destination),sha256=digest,older_versions_preserved=True)

def snapshot_history(root,provider,query):
    folder=_folder(root,provider,query);rows=[]
    if folder.exists():
        files=list(folder.glob('*.json'))
        if len(files)>1000:raise ValueError('More than 1,000 versions; narrow or archive this query history before inspection. No files were deleted.')
        for file in files:
            if file.is_symlink() or file.stat().st_size>MAX_SNAPSHOT:raise ValueError('Unsafe snapshot file.')
            body=file.read_bytes();digest=hashlib.sha256(body).hexdigest()
            if file.stem!=digest:raise ValueError('Historical snapshot checksum mismatch; original file preserved.')
            record=json.loads(body)
            if record.get('provider')!=provider or record.get('query','').strip()!=query.strip():raise ValueError('Historical snapshot key mismatch.')
            rows.append(dict(path=str(file),sha256=digest,retrieved_utc=record['retrieved_utc'],results=record['results'],response_sha256=record['response_sha256']))
    rows.sort(key=lambda r:r['retrieved_utc'],reverse=True)
    for i,row in enumerate(rows):row['version_label']='latest saved; current online status unknown' if i==0 else 'historical'
    return dict(provider=provider,query=query,versions=rows,network_used=False,history_preserved=True,evidence_is_untrusted_data=True)
