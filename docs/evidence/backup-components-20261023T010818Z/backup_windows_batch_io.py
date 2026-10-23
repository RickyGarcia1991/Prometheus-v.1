"""Physical single-file copy/readback adapter; no launcher or production authority.

Directory closure, archive reconstruction and final restore sampling are explicit
callbacks supplied by the reviewed full-backup composition, never assumed passed.
"""
import shutil
from pathlib import Path
import backup_executor as b
import backup_transaction as t

class WindowsBatchIO:
    def __init__(self,sources,destination,entries,archive_verify,reconcile,restore_sample):
        self.sources={key:b.physical(value) for key,value in sources.items()}
        self.destination=b.physical(destination);self.entries=entries
        if set(self.sources)!={'ssd','flash'} or not self.destination.is_dir():raise t.Refusal('Exact physical source roles/destination')
        for source in self.sources.values():
            if self.destination.is_relative_to(source) or source.is_relative_to(self.destination):raise t.Refusal('Source/destination overlap')
        self.archive_verify=archive_verify;self._reconcile=reconcile;self._sample=restore_sample
    def paths(self,row):
        t.item_key(row)
        return b.relative(self.sources[row['source']],row['path']),b.relative(self.destination,row['source']+'/'+row['path'])
    def verify(self,row,reference):
        source,target=self.paths(row)
        if b.entry(source,self.sources[row['source']])!=row['metadata']:raise t.Refusal('Source content/metadata changed')
        if reference is not None:return self.archive_verify(row,reference) is True
        return target.exists() and b.entry(target,self.destination/row['source'],row['metadata']['metadata_policy'])==row['metadata']
    def copy(self,row,reference):
        if reference is not None:
            if not self.verify(row,reference):raise t.Refusal('Archive dependency readback failed')
            return
        source,target=self.paths(row)
        if row['metadata']['kind']!='file':raise t.Refusal('Directory metadata requires final closure callback')
        # Parents are scoped and checked before each mutation. No recursive deletion.
        target.parent.mkdir(parents=True,exist_ok=True);b.physical(target.parent)
        b.robocopy(source.parent,target.parent,filename=source.name)
        if not self.verify(row,None):raise t.Refusal('Native item readback')
    def allocations(self):
        result={}
        for row in self.entries:
            _,target=self.paths(row)
            if target.exists():
                for stream in b.streams(target):
                    path=target if stream['name']=='::$DATA' else Path(str(target)+stream['name'])
                    result[(t.item_key(row),stream['name'])]=b.allocated(path)
        return result
    def free_bytes(self):return shutil.disk_usage(self.destination).free
    def cluster_size(self):return b.cluster_size(self.destination)
    def reconcile(self,plan):return self._reconcile(plan)
    def restore_sample(self,plan):return self._sample(plan)
