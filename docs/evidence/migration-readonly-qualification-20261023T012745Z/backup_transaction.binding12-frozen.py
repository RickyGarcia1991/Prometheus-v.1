"""Reusable bounded backup transaction components; no production CLI or authority factory.

Callers supply reviewed physical copy/readback and retained maintenance adapters.
A completed batch journal is not a complete backup until whole-tree reconciliation
and an isolated restore sample have passed under the same maintenance authority.
"""
import hashlib,json,sqlite3,re
from pathlib import PurePosixPath,Path
RESERVE=32*1024**3
class Refusal(ValueError): pass

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()

def identity(target):
    fields={'Volume':('UniqueId','FileSystem','FileSystemLabel','Size'),
            'Partition':('DiskNumber','PartitionNumber','Guid','Offset','Size','Type'),
            'Disk':('Number','UniqueId','SerialNumber','Size','PartitionStyle','BusType','IsBoot','IsSystem')}
    result={section:{key:target[section][key] for key in keys} for section,keys in fields.items()}
    if not result['Volume']['UniqueId'] or not result['Disk']['UniqueId'] or not result['Disk']['SerialNumber']:raise Refusal('Incomplete physical identity')
    result['Layout']=target['Layout']
    return result

def validate_identities(expected,current):
    if set(expected)!=set(current) or set(expected)!={'ssd','flash','destination'}:raise Refusal('Exactly three bound roles required')
    if {role:identity(row) for role,row in expected.items()}!={role:identity(row) for role,row in current.items()}:raise Refusal('Physical volume identity changed')
    if len({row['Disk']['UniqueId'] for row in current.values()})!=3:raise Refusal('Aliased physical roles')

def item_key(item):
    if type(item) is not dict or set(item)!={'source','path','streams','metadata'}:raise Refusal('Exact item schema')
    path=item['path']
    if not isinstance(path,str):raise Refusal('Relative item path')
    p=PurePosixPath(path)
    if not isinstance(path,str) or not path or p.is_absolute() or ':' in path or '\\' in path or any(x in ('','..','.') for x in path.split('/')):raise Refusal('Relative item path')
    if item['source'] not in ('ssd','flash'):raise Refusal('Source role')
    metadata=item['metadata']
    if type(metadata) is not dict or metadata.get('path')!=path or metadata.get('kind')!='file' or metadata.get('streams')!=item['streams']:raise Refusal('Item metadata/stream binding')
    if type(item['streams']) is not list or not item['streams'] or len({x.get('name') for x in item['streams']})!=len(item['streams']):raise Refusal('Stream set')
    for stream in item['streams']:
        if set(stream)!={'name','bytes','sha256'} or not isinstance(stream['name'],str) or type(stream['bytes']) is not int or stream['bytes']<0 or not isinstance(stream['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',stream['sha256']):raise Refusal('Stream schema')
    return item['source']+'/'+path

def archive_map(entries,references):
    """Bind exactly seven explicit source paths; caller verifies chunk readback."""
    if len(references)!=7:raise Refusal('Seven exact archive references required')
    indexed={item_key(row):row for row in entries}
    if len(indexed)!=len(entries):raise Refusal('Duplicate source entries')
    for key,ref in references.items():
        row=indexed.get(key)
        if not row or row['source']!='ssd' or row['streams']!=[{'name':'::$DATA','bytes':ref['bytes'],'sha256':ref['sha256']}]:raise Refusal('Archive path/content/stream mismatch')
        if not ref.get('archive_plan_sha256') or not ref.get('chunks'):raise Refusal('Archive restore dependency missing')
    return references

def remaining(entries,verified_allocations,cluster,references):
    if type(cluster) is not int or cluster<=0 or cluster&(cluster-1):raise Refusal('Cluster geometry')
    total=0
    for row in entries:
        key=item_key(row)
        if key in references:continue
        for stream in row['streams']:
            size=stream['bytes']
            if type(size) is not int or size<0:raise Refusal('Stream size')
            rounded=(size+cluster-1)//cluster*cluster
            credit=verified_allocations.get((key,stream['name']),0)
            if type(credit) is not int or credit<0:raise Refusal('Allocation proof')
            total+=max(0,rounded-min(rounded,credit))
    return total+len(entries)*cluster+max(16*1024**2,len(canonical(entries))*2)

class BatchTransaction:
    """No guessed production authority: supplied adapters must retain ownership.

    gates.maintenance(plan) revalidates a held maintenance owner, returning its
    stable authority digest. gates.identities() freshly queries physical devices.
    io handles physical paths, metadata, hash readback, exact archive dependencies,
    and isolated sample restoration. It must never infer quiescence from a snapshot.
    """
    production_entry_available=False
    def __init__(self,directory,plan,gates,io):
        self.directory=Path(directory);plan=json.loads(canonical(plan));self.plan=plan;self.gates=gates;self.io=io
        if not isinstance(plan.get('maintenance_authority'),str) or not re.fullmatch('[a-f0-9]{64}',plan['maintenance_authority']):raise Refusal('Bound authenticated maintenance digest required')
        self.pin=digest(plan);self.entries=plan['entries'];self.references=archive_map(self.entries,plan['references'])
        self.keys=[item_key(row) for row in self.entries]
        if len(set(self.keys))!=len(self.keys):raise Refusal('Duplicate item')
        self.check()
        manifest=self.directory/'manifest.private.json'
        if manifest.exists():
            if manifest.read_bytes()!=canonical(plan):raise Refusal('Foreign private manifest')
        else:
            with manifest.open('xb') as f:f.write(canonical(plan));f.flush();__import__('os').fsync(f.fileno())
        journal=self.directory/'batches.private.sqlite'
        existing=journal.exists();self.db=sqlite3.connect(journal)
        self.db.execute('PRAGMA synchronous=FULL')
        if existing:
            try:stored=self.db.execute('SELECT pin FROM owner').fetchall()
            except sqlite3.Error as e:raise Refusal('Existing journal has no owner') from e
            if stored!=[(self.pin,)]:raise Refusal('Foreign journal')
        else:
            self.db.execute('CREATE TABLE owner(pin TEXT NOT NULL)');self.db.execute('INSERT INTO owner VALUES(?)',(self.pin,))
            self.db.execute('CREATE TABLE items(name TEXT PRIMARY KEY, state TEXT NOT NULL)');self.db.commit()
    def check(self):
        validate_identities(self.plan['identities'],self.gates.identities())
        if self.gates.maintenance(self.plan)!=self.plan['maintenance_authority']:raise Refusal('Maintenance ownership changed')
    def run(self,batch_limit=64,fault=lambda point:None):
        if type(batch_limit) is not int or not 1<=batch_limit<=256:raise Refusal('Bounded batch count')
        for offset in range(0,len(self.entries),batch_limit):
            self.check()
            needed=remaining(self.entries,self.io.allocations(),self.io.cluster_size(),self.references)
            if self.io.free_bytes()-needed<RESERVE:raise Refusal('32 GiB reserve')
            for row in self.entries[offset:offset+batch_limit]:
                key=item_key(row)
                # Verified journal rows never bypass current source/destination readback.
                if not self.io.verify(row,self.references.get(key)):
                    self.db.execute('INSERT OR REPLACE INTO items VALUES(?,?)',(key,'intent'));self.db.commit();fault('intent')
                    self.io.copy(row,self.references.get(key));fault('copied')
                    if not self.io.verify(row,self.references.get(key)):raise Refusal('Item readback failed')
                self.db.execute('INSERT OR REPLACE INTO items VALUES(?,?)',(key,'verified'));self.db.commit()
            self.check()
        self.check()
        if self.io.reconcile(self.plan) is not True or self.io.restore_sample(self.plan) is not True:raise Refusal('Closure/restore sample failed')
        self.check()
        return {'verified':True,'plan_sha256':self.pin,'items':len(self.entries),'archive_references':7,'production_backup_complete':False}
    def close(self):self.db.close()

