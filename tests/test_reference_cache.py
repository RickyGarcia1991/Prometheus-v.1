import hashlib,os
import pytest
from prometheus_assistant.reference_cache import reference_session,verify_reference

@pytest.mark.skipif(os.name!='nt',reason='Windows deny-write handle')
def test_cache_blocks_modification_then_releases_handle(tmp_path,monkeypatch):
    path=tmp_path/'index';path.write_bytes(b'original');digest=hashlib.sha256(b'original').hexdigest()
    real=hashlib.file_digest;calls=[]
    def tracked(*a,**k):calls.append(1);return real(*a,**k)
    monkeypatch.setattr(hashlib,'file_digest',tracked)
    with reference_session():
        verify_reference(path,digest);verify_reference(path,digest)
        assert len(calls)==1
        with pytest.raises(OSError):path.write_bytes(b'tampered')
        with pytest.raises(OSError):path.unlink()
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='checksum'):verify_reference(path,digest)

def test_exception_closes_all_session_handles(tmp_path):
    path=tmp_path/'index';path.write_bytes(b'original');digest=hashlib.sha256(b'original').hexdigest()
    with pytest.raises(RuntimeError):
        with reference_session():
            verify_reference(path,digest);raise RuntimeError('intentional')
    path.write_bytes(b'changed')

def test_natural_questions_keep_meaningful_literal_terms():
    from prometheus_assistant.search_terms import query_terms
    assert query_terms('How do I use GPIO pins?')==['GPIO','pins']
    assert query_terms('GPIO OR missing')==['GPIO','OR','missing']
