import pytest
from prometheus_assistant.prediction_accuracy import AccuracyHistory


def test_bounded_restart_deduplication_and_category_isolation(tmp_path):
    path=tmp_path/'accuracy.sqlite3';h=AccuracyHistory(path,limit=2)
    assert h.record('a'*32,'host.family',{'direct_ram_gib':1},{'direct_ram_gib':2})['recorded']
    assert not h.record('a'*32,'host.family',{'direct_ram_gib':9},{'direct_ram_gib':8})['recorded']
    h.record('b'*32,'host.family',{'direct_ram_gib':3},{'direct_ram_gib':0})
    out=AccuracyHistory(path,limit=2).summary('host.family')['direct_ram_gib']
    assert out==dict(samples=2,mean_absolute_error=2,underpredictions=1)
    assert h.summary('other.family')['direct_ram_gib']['samples']==0
    h.record('c'*32,'host.family',{'direct_ram_gib':2},{'direct_ram_gib':2})
    assert h.summary('host.family')['direct_ram_gib']['samples']==2


@pytest.mark.parametrize('bad',[None,True,-1,float('inf'),float('nan'),10**1000,'2'])
def test_invalid_values_remain_unknown(tmp_path,bad):
    h=AccuracyHistory(tmp_path/'a.db')
    assert not h.record('a'*32,'host',{'direct_ram_gib':1},{'direct_ram_gib':bad})['recorded']
    assert not h.record('a'*32,'host',{'direct_ram_gib':bad},{'direct_ram_gib':1})['recorded']


def test_every_sqlite_connection_is_closed(tmp_path,monkeypatch):
    import sqlite3
    from prometheus_assistant import prediction_accuracy,resource_scheduler
    native=sqlite3.connect;connections=[]
    def tracked(*args,**kwargs):
        db=native(*args,**kwargs);connections.append(db);return db
    monkeypatch.setattr(sqlite3,'connect',tracked)
    accuracy=AccuracyHistory(tmp_path/'a.db')
    accuracy.record('a'*32,'host',{'direct_ram_gib':1},{'direct_ram_gib':2})
    accuracy.summary('host')
    history=resource_scheduler.WorkloadHistory(tmp_path/'h.db')
    history.record_observation('host',{'cpu_seconds':1})
    history.predict('host',resource_scheduler.Cost(ram_gib=0))
    assert len(connections)==6
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')
