from pathlib import Path
from types import SimpleNamespace
import pytest
from prometheus_assistant import kiwix

def _root(tmp_path: Path) -> Path:
    root=tmp_path/"resources"
    (root/"Tools"/"Kiwix"/"bin").mkdir(parents=True)
    (root/"Tools"/"Kiwix"/"bin"/"kiwix-search.exe").write_bytes(b"tool")
    item=kiwix.ARCHIVES[0]
    archive=root/"Knowledge"/"Kiwix"/item.project/item.filename
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"zim")
    return root

def test_archive_path_rejects_unknown(tmp_path):
    with pytest.raises(kiwix.KiwixError,match="Unknown archive"):
        kiwix.archive_path(tmp_path,"missing")

def test_search_uses_portable_tool_and_limits_results(tmp_path,monkeypatch):
    root=_root(tmp_path); seen={}
    def fake_run(args,**kwargs):
        seen["args"]=args
        return SimpleNamespace(returncode=0,stdout="Alpha\nBeta\nGamma\n",stderr="")
    monkeypatch.setattr(kiwix.subprocess,"run",fake_run)
    rows=kiwix.search_archive(root,kiwix.ARCHIVES[0].id," artificial intelligence ",limit=2)
    assert rows==["Alpha","Beta"]
    assert Path(seen["args"][0]).name=="kiwix-search.exe"
    assert Path(seen["args"][1]).name==kiwix.ARCHIVES[0].filename
    assert seen["args"][2]=="artificial intelligence"

@pytest.mark.parametrize(("query","limit"),[("",10),("x",0)])
def test_search_validates_inputs(tmp_path,query,limit):
    with pytest.raises(ValueError):
        kiwix.search_archive(tmp_path,kiwix.ARCHIVES[0].id,query,limit)

def test_read_article_fetches_text_and_cleans_up(tmp_path,monkeypatch):
    root=_root(tmp_path)
    (root/"Tools"/"Kiwix"/"bin"/"kiwix-serve.exe").write_bytes(b"tool")
    class Proc:
        def __init__(self): self.terminated=False
        def terminate(self): self.terminated=True
        def wait(self,timeout=None): return 0
        def kill(self): pass
    proc=Proc(); seen={}
    monkeypatch.setattr(kiwix.subprocess,"Popen",lambda args,**kw:(seen.setdefault("args",args),proc)[1])
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return b"<html><body><h1>Alpha</h1><p>Useful article text.</p><script>ignore()</script></body></html>"
    monkeypatch.setattr(kiwix,"urlopen",lambda url,timeout=2:Response())
    text=kiwix.read_article(root,kiwix.ARCHIVES[0].id,"Alpha",100)
    assert text=="Alpha Useful article text."
    assert "--address=127.0.0.1" in seen["args"]
    assert "--blockexternal" in seen["args"]
    assert proc.terminated

def test_offline_context_can_ground_prompts(monkeypatch):
    from prometheus_assistant import cli
    monkeypatch.setattr(cli,"search_archive",lambda *a,**k:["Artificial intelligence"])
    monkeypatch.setattr(cli,"read_article",lambda *a,**k:"AI article body")
    text=cli._offline_context_for_prompt("What is AI?",enabled=True)
    assert "Artificial intelligence" in text
    assert "AI article body" in text
    assert cli._offline_context_for_prompt("What is AI?",enabled=False) is None
