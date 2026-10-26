from pathlib import Path
import backup_executor as b
from backup_windows_batch_io import WindowsBatchIO

def test_native_file_ads_readback_corrupt_repair_and_source_drift(tmp_path):
    sources={name:tmp_path/name for name in ('ssd','flash')}
    for path in sources.values():path.mkdir()
    destination=tmp_path/'backup';destination.mkdir()
    source=sources['ssd']/'payload';source.write_bytes(b'original')
    Path(str(source)+':test').write_bytes(b'stream')
    metadata=b.entry(source,sources['ssd'])
    row=dict(source='ssd',path='payload',streams=metadata['streams'],metadata=metadata)
    io=WindowsBatchIO(sources,destination,[row],lambda *a:False,lambda p:False,lambda p:False)
    assert not io.verify(row,None)
    io.copy(row,None);assert io.verify(row,None)
    target=destination/'ssd/payload';target.write_bytes(b'corrupted')
    io.copy(row,None);assert io.verify(row,None)
    assert Path(str(target)+':test').read_bytes()==b'stream'
    assert io.allocations()[('ssd/payload','::$DATA')]>0
    source.write_bytes(b'drift')
    import pytest
    with pytest.raises(ValueError):io.verify(row,None)
    assert not io.reconcile({}) and not io.restore_sample({})
