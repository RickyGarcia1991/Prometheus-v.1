import hashlib
import json
import sqlite3
from contextlib import closing
import pytest
from prometheus_assistant import library
from prometheus_assistant.cli import main
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.memory import MemoryStore


@pytest.fixture
def reference(tmp_path, monkeypatch):
    path = tmp_path / library.MEDLINE_PATH
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''CREATE TABLE topics(id INTEGER PRIMARY KEY, title TEXT, body TEXT,
            terms TEXT, language TEXT, url TEXT, created TEXT);
            CREATE VIRTUAL TABLE topic_search USING fts5(title,body,terms,content='topics',content_rowid='id');
            PRAGMA user_version=1;''')
        db.executemany('INSERT INTO topics VALUES(?,?,?,?,?,?,?)', [
            (1,'Algebra study','Sample medical reference text for testing.','algebra','English','https://medlineplus.gov/example.html','01/01/2000'),
            (2,'Prueba','Texto de referencia.','prueba','Spanish','https://medlineplus.gov/spanish/example.html','01/01/2000'),
            (3,'Absent summary','','absent','English','https://medlineplus.gov/absent.html','01/01/2000')])
        db.execute("INSERT INTO topic_search(topic_search) VALUES('rebuild')")
        db.commit()
    monkeypatch.setattr(library,'MEDLINE_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(library,'resource_root',lambda:tmp_path)
    return tmp_path,path


def test_search_is_read_only_and_carries_origin(reference):
    root,path=reference; before=path.read_bytes()
    result=library.search_library('Algebra',root=root)
    assert result['results'][0]['url']=='https://medlineplus.gov/example.html'
    assert result['results'][0]['topic_created_date']=='01/01/2000'
    assert result['snapshot_date'] != result['results'][0]['topic_created_date']
    assert result['evidence_is_untrusted_data'] and not result['generated_answer']
    assert path.read_bytes()==before


def test_languages_and_missing_summary_remain_explicit(reference):
    root,_=reference
    assert not library.search_library('Prueba',root=root)['results']
    assert library.search_library('Prueba',root=root,language='Spanish')['results']
    result=library.search_library('Absent',root=root)['results'][0]
    assert result['excerpt']=='' and not result['summary_available']
    assert library.library_status(root)['topics_without_summary']==1


def test_checksum_detects_altered_database(reference):
    root,path=reference
    with path.open('ab') as file: file.write(b'changed')
    with pytest.raises(ValueError,match='checksum mismatch'):
        library.search_library('Algebra',root=root)


@pytest.mark.parametrize('query',['',None,'*','a '*25,'x'*401])
def test_rejects_invalid_or_unbounded_queries(reference,query):
    with pytest.raises(ValueError): library.search_library(query,root=reference[0])


def test_sql_and_fts_operators_are_data(reference):
    root,path=reference
    assert not library.search_library('Algebra OR Prueba',root=root)['results']
    library.search_library("'); DROP TABLE topics; --",root=root)
    assert library.library_status(root)['topics_by_language']=={'English':2,'Spanish':1}


def test_missing_library_is_reported_without_creating_files(tmp_path):
    assert not library.library_status(tmp_path)['installed']
    assert list(tmp_path.iterdir())==[]


def test_cli_and_agent_tool_are_available_without_model(reference,capsys,monkeypatch):
    root,_=reference
    assert main(['library','medical','Algebra'])==0
    assert json.loads(capsys.readouterr().out)['results']
    import prometheus_assistant.builtin_tools as builtin
    monkeypatch.setattr(builtin,'resource_root',lambda:root)
    with MemoryStore(root/'memory.sqlite3') as memory:
        spec=build_builtin_registry(memory).get('knowledge','medical')
        assert not spec.mutates_state and not spec.command_execution and not spec.external_network
        result=json.loads(spec.executor(spec.request('find evidence',{'query':'Algebra'})))
        assert result['evidence_is_untrusted_data']


def test_source_catalog_distinguishes_priority_from_accuracy():
    from prometheus_assistant.source_catalog import source_catalog
    rows=source_catalog()
    assert {'openstax','mit-ocw','digivatlib','digital-bodleian','gallica','gsa-forms','medlineplus'} <= {r['id'] for r in rows}
    assert all(r['authority_is_accuracy_percentage'] is False for r in rows)
