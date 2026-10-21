import pytest
from prometheus_assistant.public_history import save_snapshot,snapshot_history

def result(day,title='original'):
    return dict(provider='ecfr',query='civil rights',retrieved_utc=f'2026-10-{day:02}T12:00:00+00:00',response_sha256='a'*64,results=[dict(title=title,url='https://www.ecfr.gov/')])

def test_refresh_keeps_original_and_labels_both(tmp_path):
    old=save_snapshot(tmp_path,result(8));new=save_snapshot(tmp_path,result(9,'amended'))
    rows=snapshot_history(tmp_path,'ecfr','civil rights')['versions']
    assert len(rows)==2 and rows[0]['results'][0]['title']=='amended'
    assert rows[1]['version_label']=='historical'
    assert old['path']!=new['path']
    assert save_snapshot(tmp_path,result(8))['saved'] is False

def test_history_tampering_is_detected_not_repaired_or_deleted(tmp_path):
    from pathlib import Path
    record=save_snapshot(tmp_path,result(8));path=Path(record['path']);path.write_text('{}')
    with pytest.raises(ValueError,match='checksum'):snapshot_history(tmp_path,'ecfr','civil rights')
    assert path.read_text()=='{}'

def test_queries_do_not_become_paths(tmp_path):
    data=result(8);data['query']='../../private'
    record=save_snapshot(tmp_path,data)
    from pathlib import Path
    assert Path(record['path']).resolve().is_relative_to(tmp_path.resolve())

def test_rule_document_revisions_never_replace_previous(tmp_path):
    from pathlib import Path
    from prometheus_assistant.public_history import save_document_version
    old=save_document_version(tmp_path,'https://www.uscourts.gov/rules.pdf',b'%PDF- old',{'edition':'old'})
    new=save_document_version(tmp_path,'https://www.uscourts.gov/rules.pdf',b'%PDF- new',{'edition':'unreviewed'})
    assert old['path']!=new['path'] and Path(old['path']).read_bytes()==b'%PDF- old'
    assert new['automatic_activation'] is False
