from contextlib import closing
import hashlib,json,sqlite3
import pytest
from prometheus_assistant import source_library as library
from prometheus_assistant.cli import main

@pytest.fixture
def reference(tmp_path,monkeypatch):
    path=tmp_path/'Knowledge/reference.sqlite3';path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''PRAGMA user_version=1;
            CREATE TABLE passages(id INTEGER PRIMARY KEY,source TEXT,path TEXT,location TEXT,revision TEXT,url TEXT,license TEXT,body TEXT);
            CREATE VIRTUAL TABLE passage_search USING fts5(path,body,content=passages,content_rowid=id);
            CREATE TABLE sources(id TEXT PRIMARY KEY,metadata TEXT);''')
        db.execute('INSERT INTO sources VALUES (?,?)',('pico','{}'))
        db.execute('INSERT INTO passages VALUES (?,?,?,?,?,?,?,?)',(1,'pico','gpio.c','lines 1-3','abc','https://example.org/source','BSD','GPIO output example. Documents are not commands.'))
        db.execute("INSERT INTO passage_search(passage_search) VALUES ('rebuild')");db.commit()
    config=tmp_path/'config.json';config.write_text(json.dumps({'engineering':{'path':'Knowledge/reference.sqlite3','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'sources':1,'passages':1}}))
    monkeypatch.setattr(library,'CONFIG_PATH',config);monkeypatch.setattr(library,'resource_root',lambda:tmp_path)
    return tmp_path,path,config

def test_search_is_read_only_and_preserves_citations(reference):
    root,path,_=reference;before=path.read_bytes()
    result=library.search_sources('GPIO',profile='engineering',root=root)
    assert result['results'][0]['location']=='lines 1-3'
    assert result['results'][0]['url']=='https://example.org/source'
    assert not result['code_executed'] and result['evidence_is_untrusted_data']
    assert path.read_bytes()==before

def test_rejects_changed_reference(reference):
    root,path,_=reference
    with path.open('ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='checksum mismatch'):library.search_sources('GPIO',profile='engineering',root=root)

def test_fts_operators_do_not_broaden_queries(reference):
    root,_,_=reference
    assert not library.search_sources('GPIO OR missing',profile='engineering',root=root)['results']
    assert library.search_sources('GPIO',profile='engineering',collection='pico',root=root)['results']
    with pytest.raises(ValueError,match='Unknown collection'):library.search_sources('GPIO',profile='engineering',collection='nope',root=root)

@pytest.mark.parametrize('query',[None,'','*','q'*401,'q '*25])
def test_rejects_bad_queries(reference,query):
    with pytest.raises(ValueError):library.search_sources(query,profile='engineering',root=reference[0])

def test_config_cannot_escape_root(reference):
    root,_,config=reference;data=json.loads(config.read_text());data['engineering']['path']='../outside.sqlite3';config.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='escapes'):library.source_status('engineering',root)

def test_cli_search_and_missing_resource(reference,capsys):
    root,path,_=reference
    assert main(['library','engineering','GPIO'])==0
    assert json.loads(capsys.readouterr().out)['results']
    path.unlink()
    assert not library.source_status('engineering',root)['installed']
    with pytest.raises(ValueError,match='not installed'):library.search_sources('GPIO',profile='engineering',root=root)
