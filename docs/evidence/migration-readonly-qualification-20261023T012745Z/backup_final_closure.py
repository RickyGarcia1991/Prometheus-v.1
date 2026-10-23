"""Final directory metadata/reconciliation and isolated sample callbacks.

No CLI, maintenance authority, production root discovery or full-copy entry.
Caller supplies pinned baseline inventories captured under retained maintenance.
"""
import os,subprocess,tempfile,uuid,shutil
from pathlib import Path
import backup_executor as b
from backup_transaction import Refusal,item_key,archive_map,RESERVE

SYSTEM_EXCLUSIONS=frozenset({'$RECYCLE.BIN','System Volume Information'})

def tree_inventory(root,policy=None,excluded=()):
    root=b.physical(root)
    if not set(excluded)<=SYSTEM_EXCLUSIONS:raise Refusal('Only explicit root system exclusions supported')
    paths=[root]
    def error(value):raise value
    for directory,dirs,files in os.walk(root,followlinks=False,onerror=error):
        if Path(directory)==root:dirs[:]=[name for name in dirs if name not in excluded]
        paths.extend(b.physical(Path(directory)/name) for name in dirs+files)
    return [b.entry(path,root,policy) for path in sorted(paths)]

class FinalClosure:
    def __init__(self,sources,destination,baseline,archive,gate,*,sample_parent=None):
        self.sources={role:b.physical(path) for role,path in sources.items()};self.destination=b.physical(destination)
        if set(self.sources)!={'ssd','flash'} or set(baseline)!=set(self.sources):raise Refusal('Closure role binding')
        self.baseline=__import__('json').loads(b.encode(baseline));self.archive=archive;self.gate=gate
        self.sample_parent=b.physical(sample_parent or tempfile.gettempdir());self.sample=None
        for role,source in self.sources.items():
            if self.destination.is_relative_to(source) or source.is_relative_to(self.destination):raise Refusal('Closure root overlap')
            rows=baseline[role]['entries']
            if len({r['path'] for r in rows})!=len(rows) or not any(r['path']=='.' and r['kind']=='directory' for r in rows):raise Refusal('Complete root inventory required')
    def check_sources(self):
        if self.gate() is not True:raise Refusal("Fresh retained maintenance check required")
        for role,source in self.sources.items():
            spec=self.baseline[role]
            if tree_inventory(source,excluded=spec['excluded'])!=spec['entries']:raise Refusal('Final source tree drift')
    def directory_metadata(self):
        self.check_sources()
        for role,source in self.sources.items():
            target=b.physical(self.destination/role)
            spec=self.baseline[role];expected={row['path'] for row in spec['entries']}
            if target.exists() and any(row['path'] not in expected for row in tree_inventory(target)):raise Refusal('Foreign destination before directory reconciliation')
            # Files are excluded. This pass only creates/reconciles directory metadata.
            args=[str(Path(os.environ['WINDIR'])/'System32/robocopy.exe'),str(source),str(target),'/E','/XF','*','/COPY:DATS','/DCOPY:DAT','/R:0','/W:0','/XJ','/NP','/NFL','/NDL','/NJH','/NJS']
            if spec['excluded']:args+=['/XD',*[str(source/name) for name in spec['excluded']]]
            result=subprocess.run(args,capture_output=True,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode>=8:raise Refusal('Directory metadata reconciliation failed')
        self.check_sources()
    def validate_plan(self,plan):
        indexed={item_key(row):row for row in plan['entries']}
        expected={role+'/'+row['path']:row for role,spec in self.baseline.items() for row in spec['entries'] if row['kind']=='file'}
        if len(indexed)!=len(plan['entries']) or set(indexed)!=set(expected) or any(indexed[key]['metadata']!=row for key,row in expected.items()):raise Refusal('Plan does not match complete source baseline')
        archive_map(plan['entries'],plan['references'])
        return indexed
    def reconcile(self,plan):
        indexed=self.validate_plan(plan)
        self.directory_metadata();references=plan['references']
        for role,source in self.sources.items():
            spec=self.baseline[role];target=self.destination/role
            wanted=[row for row in spec['entries'] if role+'/'+row['path'] not in references]
            if tree_inventory(target,spec['metadata_policy'])!=wanted:raise Refusal('Final destination tree/stream/metadata mismatch')
        for key,reference in references.items():
            if key not in indexed or self.archive.verify(indexed[key],reference) is not True:raise Refusal('Archive final reconstruction failed')
        self.check_sources();return True
    def restore_sample(self,plan):
        self.validate_plan(plan);self.check_sources()
        root=self.sample_parent/('prometheus-backup-restore-sample-'+uuid.uuid4().hex)
        ordinary=[row for row in plan['entries'] if item_key(row) not in plan['references']]
        if not ordinary:raise Refusal('Ordinary file restore sample required')
        # Favor ADS coverage without selecting an unbounded largest payload.
        row=min(ordinary,key=lambda r:(not any(s['name']!='::$DATA' for s in r['streams']),sum(s['bytes'] for s in r['streams'])))
        key,reference=min(plan['references'].items(),key=lambda pair:pair[1]['bytes'])
        required=sum(s['bytes'] for s in row['streams'])+reference['bytes']+16*1024**2
        if shutil.disk_usage(self.sample_parent).free-required<RESERVE:raise Refusal('Restore sample would violate reserve')
        root.mkdir(exist_ok=False);self.sample=root
        source=b.relative(self.destination,row['source']+'/'+row['path']);target=root/'ordinary';target.mkdir()
        b.robocopy(source.parent,target,filename=source.name)
        restored=b.entry(target/source.name,target,row['metadata']['metadata_policy']);expected=dict(row['metadata'],path=source.name)
        if restored!=expected:raise Refusal('Isolated ordinary restore readback')
        self.archive.verify({},reference)
        output=root/'archive-sample';digest=__import__('hashlib').sha256();size=0
        with output.open('xb') as writer:
            for part in reference['chunks']:
                path=b.relative(self.archive.root,part['path'])
                with path.open('rb') as reader:
                    while data:=reader.read(4*1024*1024):writer.write(data);digest.update(data);size+=len(data)
            writer.flush();os.fsync(writer.fileno())
        if size!=reference['bytes'] or digest.hexdigest()!=reference['sha256'] or b.sha(output)!=reference['sha256']:raise Refusal('Isolated archive restore readback')
        self.check_sources();return True
