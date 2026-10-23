import base64,hashlib,json
from pathlib import Path
import pytest
import backup_executor as b
from backup_chunk_archive import ChunkArchive
from backup_final_closure import FinalClosure,tree_inventory
from backup_windows_batch_io import WindowsBatchIO

def fixture(tmp_path):
    sources={role:tmp_path/role for role in ('ssd','flash')}
    for root in sources.values():root.mkdir()
    destination=tmp_path/'backup';destination.mkdir();archive=tmp_path/'archive';archive.mkdir()
    files=[];refs={};entries=[]
    for i in range(7):
        data=('blob'+str(i)).encode();pin=hashlib.sha256(data).hexdigest();files.append(dict(digest=pin,size=len(data)))
        folder=archive/'archive'/pin;folder.mkdir(parents=True);(folder/'part-00000').write_bytes(data)
        (sources['ssd']/str(i)).write_bytes(data)
    model=b'manifest';plan=dict(schema=1,chunk_size=1024,files=files,manifest_base64=base64.b64encode(model).decode(),manifest_sha256=hashlib.sha256(model).hexdigest())
    (archive/'backup-plan.json').write_text(json.dumps(plan));chunks=ChunkArchive(archive,b.sha(archive/'backup-plan.json'))
    empty=sources['flash']/'empty';empty.mkdir();Path(str(empty)+':directory').write_bytes(b'directory-ads')
    ordinary=sources['flash']/'data';ordinary.write_bytes(b'ordinary');Path(str(ordinary)+':ads').write_bytes(b'file-ads')
    baseline={role:dict(entries=tree_inventory(root),excluded=[],metadata_policy='NTFS') for role,root in sources.items()}
    for role,spec in baseline.items():
        for row in spec['entries']:
            if row['kind']=='file':entries.append(dict(source=role,path=row['path'],streams=row['streams'],metadata=row))
    for i,file in enumerate(files):refs['ssd/'+str(i)]=chunks.reference(file['digest'])
    transaction=dict(entries=entries,references=refs)
    io=WindowsBatchIO(sources,destination,entries,chunks.verify,lambda p:False,lambda p:False)
    for row in entries:
        if row['source']=='flash':io.copy(row,None)
    closure=FinalClosure(sources,destination,baseline,chunks,lambda:True,sample_parent=tmp_path)
    return closure,transaction,sources,destination

def test_directory_ads_full_reconciliation_and_isolated_sample(tmp_path):
    closure,plan,sources,destination=fixture(tmp_path)
    assert closure.reconcile(plan)
    assert Path(str(destination/'flash/empty')+':directory').read_bytes()==b'directory-ads'
    assert closure.restore_sample(plan)
    assert (closure.sample/'archive-sample').read_bytes().startswith(b'blob')
    assert Path(str(closure.sample/'ordinary/data')+':ads').read_bytes()==b'file-ads'

@pytest.mark.parametrize('change',['source','foreign','metadata','gate'])
def test_final_refusal(tmp_path,change):
    closure,plan,sources,destination=fixture(tmp_path)
    if change=='source':(sources['flash']/'extra').write_bytes(b'drift')
    if change=='foreign':(destination/'flash/foreign').write_bytes(b'preserve')
    if change=='metadata':plan['entries'][0]['metadata']['path']='other'
    if change=='gate':
        def fail():raise ValueError('maintenance lost')
        closure.gate=fail
    with pytest.raises(ValueError):closure.reconcile(plan)
    if change=='foreign':assert (destination/'flash/foreign').read_bytes()==b'preserve'


def test_matching_archive_reference_cannot_override_baseline_content(tmp_path):
    closure,plan,sources,destination=fixture(tmp_path)
    row=next(r for r in plan['entries'] if r['source']=='ssd' and r['path']=='0')
    other=plan['references']['ssd/1']
    row['metadata']=dict(row['metadata'],streams=[dict(name='::$DATA',bytes=other['bytes'],sha256=other['sha256'])])
    row['streams']=row['metadata']['streams'];plan['references']['ssd/0']=other
    with pytest.raises(ValueError,match='baseline'):closure.reconcile(plan)


def test_sample_reserve_and_false_maintenance_refuse_before_write(tmp_path,monkeypatch):
    closure,plan,sources,destination=fixture(tmp_path)
    import backup_final_closure as f
    from types import SimpleNamespace
    assert closure.reconcile(plan)
    monkeypatch.setattr(f.shutil,'disk_usage',lambda p:SimpleNamespace(free=f.RESERVE))
    with pytest.raises(ValueError,match='reserve'):closure.restore_sample(plan)
    assert closure.sample is None
    closure.gate=lambda:False
    with pytest.raises(ValueError,match='maintenance'):closure.reconcile(plan)
