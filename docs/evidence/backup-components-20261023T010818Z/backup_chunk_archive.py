"""Read-only reconstruction verifier for the retained model_transfer chunk format."""
import base64,hashlib,json,re
from pathlib import Path
import backup_executor as b
from backup_transaction import Refusal

class ChunkArchive:
    def __init__(self,root,plan_sha256):
        self.root=b.physical(root);self.pin=plan_sha256
        self.plan_path=self.root/'backup-plan.json'
        self._load()
    def _load(self):
        if b.sha(self.plan_path)!=self.pin:raise Refusal('Archive plan changed')
        plan=json.loads(self.plan_path.read_bytes())
        if plan['schema']!=1 or len(plan['files'])!=7 or type(plan['chunk_size']) is not int or not 0<plan['chunk_size']<=1073741824:raise Refusal('Archive format')
        if hashlib.sha256(base64.b64decode(plan['manifest_base64'],validate=True)).hexdigest()!=plan['manifest_sha256']:raise Refusal('Archive model manifest')
        files={e['digest']:e for e in plan['files']}
        if len(files)!=7 or any(not re.fullmatch('[a-f0-9]{64}',key) or type(e['size']) is not int or e['size']<=0 for key,e in files.items()):raise Refusal('Archive entry schema')
        self.plan=plan;self.files=files
    def reference(self,sha256):
        entry=self.files[sha256]
        chunks=[dict(path=f'archive/{sha256}/part-{i:05d}',bytes=min(self.plan['chunk_size'],entry['size']-offset)) for i,offset in enumerate(range(0,entry['size'],self.plan['chunk_size']))]
        return dict(sha256=sha256,bytes=entry['size'],archive_plan_sha256=self.pin,chunks=chunks)
    def verify(self,row,reference):
        self._load()
        if reference!=self.reference(reference['sha256']):raise Refusal('Archive reference differs from exact pinned plan')
        digest=hashlib.sha256();total=0
        for part in reference['chunks']:
            path=b.relative(self.root,part['path']);before=path.stat()
            if before.st_size!=part['bytes']:raise Refusal('Archive chunk size')
            count=0
            with path.open('rb') as f:
                while data:=f.read(4*1024*1024):digest.update(data);count+=len(data)
            after=path.stat()
            if count!=part['bytes'] or (before.st_ino,before.st_mtime_ns,before.st_size)!=(after.st_ino,after.st_mtime_ns,after.st_size):raise Refusal('Archive chunk changed')
            total+=count
        if total!=reference['bytes'] or digest.hexdigest()!=reference['sha256']:raise Refusal('Full archive reconstruction digest')
        if b.sha(self.plan_path)!=self.pin:raise Refusal('Archive plan changed during readback')
        return True
