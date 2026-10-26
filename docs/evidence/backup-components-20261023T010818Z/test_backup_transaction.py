import copy
import pytest
import backup_transaction as t

def setup(tmp_path):
    identities={}
    for n,role in enumerate(('ssd','flash','destination')):
        identities[role]={'Volume':dict(UniqueId=str(n),FileSystem='NTFS',FileSystemLabel=role,Size=100), 'Partition':dict(DiskNumber=n,PartitionNumber=1,Guid=str(n),Offset=1,Size=100,Type='Basic'), 'Disk':dict(Number=n,UniqueId=str(n),SerialNumber=str(n),Size=100,PartitionStyle='GPT',BusType='USB',IsBoot=False,IsSystem=False),'Layout':[]}
    entries=[dict(source='ssd',path=str(n),streams=[dict(name='::$DATA',bytes=1,sha256=str(n)*64)]) for n in range(8)]
    for row in entries:row['metadata']=dict(path=row['path'],kind='file',streams=copy.deepcopy(row['streams']))
    refs={'ssd/'+str(n):dict(bytes=1,sha256=str(n)*64,archive_plan_sha256='pin',chunks=['chunk']) for n in range(7)}
    plan=dict(entries=entries,references=refs,identities=identities,maintenance_authority='a'*64)
    class Gates:
        def identities(self):return identities
        def maintenance(self,plan):return 'a'*64
    class IO:
        present=set();copies=0
        def allocations(self):return {}
        def cluster_size(self):return 4096
        def free_bytes(self):return t.RESERVE+10**9
        def verify(self,row,ref):return bool(ref) or row['path'] in self.present
        def copy(self,row,ref):self.present.add(row['path']);self.copies+=1
        def reconcile(self,plan):return True
        def restore_sample(self,plan):return True
    return plan,Gates(),IO()

def test_interrupted_intent_and_corrupt_resume(tmp_path):
    plan,gates,io=setup(tmp_path);tx=t.BatchTransaction(tmp_path,plan,gates,io)
    def fail(point):
        if point=='copied':raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):tx.run(fault=fail)
    tx.close();io.present.clear()
    tx=t.BatchTransaction(tmp_path,plan,gates,io)
    assert tx.run()['verified'] and io.copies==2
    io.present.clear();assert tx.run()['verified'] and io.copies==3
    tx.close()

def test_identity_and_maintenance_rechecked_each_batch(tmp_path):
    plan,gates,io=setup(tmp_path);tx=t.BatchTransaction(tmp_path,plan,gates,io)
    gates.maintenance=lambda plan:'lost'
    with pytest.raises(t.Refusal):tx.run(1)
    assert io.copies==0;tx.close()

@pytest.mark.parametrize('change',['path','stream','identity','reserve','restore'])
def test_fail_closed(tmp_path,change):
    plan,gates,io=setup(tmp_path)
    if change=='path':plan['entries'][0]['path']='../escape'
    if change=='stream':plan['entries'][0]['streams'].append(dict(name=':ads:$DATA',bytes=1,sha256='x'))
    if change=='identity':
        other=copy.deepcopy(plan['identities']);other['ssd']['Disk']['SerialNumber']='other';gates.identities=lambda:other
    if change=='reserve':io.free_bytes=lambda:t.RESERVE
    if change=='restore':io.restore_sample=lambda plan:False
    with pytest.raises(t.Refusal):
        tx=t.BatchTransaction(tmp_path,plan,gates,io)
        try:tx.run()
        finally:tx.close()

def test_foreign_journal_preserved(tmp_path):
    plan,gates,io=setup(tmp_path);(tmp_path/'batches.private.sqlite').write_bytes(b'foreign')
    with pytest.raises((t.Refusal, t.sqlite3.DatabaseError)):t.BatchTransaction(tmp_path,plan,gates,io)
    assert (tmp_path/'batches.private.sqlite').read_bytes()==b'foreign'



@pytest.mark.parametrize('field',['streams','path'])
def test_archive_reference_cannot_hide_different_source_metadata(tmp_path,field):
    plan,gates,io=setup(tmp_path)
    if field=='streams':plan['entries'][0]['metadata']['streams'][0]['sha256']='f'*64
    else:plan['entries'][0]['metadata']['path']='different-source'
    with pytest.raises(t.Refusal,match='binding'):t.BatchTransaction(tmp_path,plan,gates,io)
    assert not (tmp_path/'manifest.private.json').exists() and io.copies==0
