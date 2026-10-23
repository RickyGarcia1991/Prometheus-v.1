import base64,hashlib,json
import pytest
import backup_chunk_archive as a

def test_full_reconstruction_detects_same_length_chunk_corruption(tmp_path):
    files=[]
    for i in range(7):
        data=('payload-'+str(i)).encode();pin=hashlib.sha256(data).hexdigest();files.append(dict(digest=pin,size=len(data)))
        folder=tmp_path/'archive'/pin;folder.mkdir(parents=True)
        for n,offset in enumerate(range(0,len(data),4)):(folder/f'part-{n:05d}').write_bytes(data[offset:offset+4])
    plan=dict(schema=1,files=files,chunk_size=4,manifest_base64=base64.b64encode(b'manifest').decode(),manifest_sha256=hashlib.sha256(b'manifest').hexdigest())
    path=tmp_path/'backup-plan.json';path.write_text(json.dumps(plan))
    archive=a.ChunkArchive(tmp_path,a.b.sha(path));reference=archive.reference(files[0]['digest'])
    assert archive.verify({},reference)
    part=tmp_path/reference['chunks'][0]['path'];part.write_bytes(b'evil')
    with pytest.raises(a.Refusal,match='reconstruction'):archive.verify({},reference)
    altered=dict(reference,bytes=reference['bytes']+1)
    with pytest.raises(a.Refusal):archive.verify({},altered)
