from contextlib import closing
import hashlib
import json
import sqlite3
import pytest
from prometheus_assistant import mathematics as math
from prometheus_assistant.cli import main


@pytest.fixture
def reference(tmp_path,monkeypatch):
    path=tmp_path/math.MATH_PATH;path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''PRAGMA user_version=1;
            CREATE TABLE sources(id TEXT PRIMARY KEY,repository TEXT,commit_sha TEXT,commit_date TEXT,license TEXT);
            CREATE TABLE passages(id INTEGER PRIMARY KEY,source TEXT,path TEXT,line_start INTEGER,line_end INTEGER,sha256 TEXT,body TEXT);
            CREATE VIRTUAL TABLE passage_search USING fts5(path,body,content=passages,content_rowid=id);''')
        db.executemany('INSERT INTO sources VALUES (?,?,?,?,?)',[
            ('stacks-project','https://github.com/stacks/stacks-project','a'*40,'2026-10-09','GNU FDL'),
            ('mathlib4','https://github.com/leanprover-community/mathlib4','b'*40,'2026-10-09','Apache-2.0')])
        db.executemany('INSERT INTO passages VALUES (?,?,?,?,?,?,?)',[
            (1,'stacks-project','algebra.tex',10,30,'1'*64,'Polynomial ring example. Preserve original TeX notation: $x^2$.'),
            (2,'mathlib4','Mathlib/Algebra/Test.lean',2,20,'2'*64,'Theorem about a polynomial and a ring. Original proof source.')])
        db.execute("INSERT INTO passage_search(passage_search) VALUES ('rebuild')");db.commit()
    monkeypatch.setattr(math,'MATH_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(math,'resource_root',lambda:tmp_path)
    return tmp_path,path


def test_original_notation_citations_and_read_only_search(reference):
    root,path=reference;before=path.read_bytes()
    result=math.search_math('polynomial ring',root=root)
    assert len(result['results'])==2
    row=next(r for r in result['results'] if r['source']=='stacks-project')
    assert '$x^2$' in row['excerpt']
    assert row['url'].endswith('/'+'a'*40+'/algebra.tex#L10-L30')
    assert not result['local_lean_proof_check_performed'] and result['evidence_is_untrusted_data']
    assert path.read_bytes()==before


def test_collection_filter_and_literal_fts_operators(reference):
    root,_=reference
    assert len(math.search_math('polynomial',root=root,collection='mathlib4')['results'])==1
    assert not math.search_math('polynomial OR missing',root=root)['results']
    assert math.math_status(root)['passages']==2


def test_changed_index_is_rejected(reference):
    root,path=reference
    with path.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='checksum mismatch'):math.search_math('polynomial',root=root)


@pytest.mark.parametrize('query',['',None,'*','x'*401,'word '*25])
def test_invalid_queries_are_rejected(reference,query):
    with pytest.raises(ValueError):math.search_math(query,root=reference[0])


def test_unknown_collection_is_rejected(reference):
    with pytest.raises(ValueError):math.search_math('polynomial',root=reference[0],collection='../other')


def test_absent_index_creates_nothing(tmp_path):
    assert not math.math_status(tmp_path)['installed']
    assert not list(tmp_path.iterdir())


def test_cli_works_without_model_and_tool_does_not_execute(reference,capsys):
    from prometheus_assistant.builtin_tools import build_builtin_registry
    root,_=reference
    assert main(['library','math','polynomial','--collection','stacks-project'])==0
    assert json.loads(capsys.readouterr().out)['results'][0]['source']=='stacks-project'
    tool=build_builtin_registry(None).get('knowledge','mathematics')
    assert not tool.command_execution and not tool.external_network and not tool.mutates_state
