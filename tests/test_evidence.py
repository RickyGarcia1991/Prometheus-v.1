from prometheus_assistant.context import Evidence
from prometheus_assistant.evidence import collect_evidence, memory_evidence, offline_evidence
from prometheus_assistant.memory import MemoryStore

def test_memory_becomes_first_class_evidence(tmp_path):
    with MemoryStore(tmp_path/"m.db") as m:
        m.remember("decision","robot actuator","Use electric hands.",source_type="user",source_ref="t")
        rows=memory_evidence(m,"robot actuator")
        assert rows[0].source_type=="memory"
        assert rows[0].source_ref=="decision:robot actuator"

def test_offline_sources_are_separate_evidence(monkeypatch):
    import prometheus_assistant.evidence as e
    monkeypatch.setattr(e,"search_archive",lambda root,archive,q,n:["Alpha"])
    monkeypatch.setattr(e,"read_article",lambda root,archive,title,n:f"{archive} Alpha robotics")
    rows=offline_evidence("robotics",root="X")
    assert {r.source_type for r in rows}=={"wikipedia","wiktionary","wikisource"}
    assert len(rows)==3

def test_collection_filters_unrelated_memory(tmp_path,monkeypatch):
    import prometheus_assistant.evidence as e
    monkeypatch.setattr(e,"offline_evidence",lambda *a,**k:[Evidence("wikipedia","robotics","robot actuator joint",60)])
    with MemoryStore(tmp_path/"m.db") as m:
        m.remember("fact","paint","car blue",source_type="user",source_ref="t")
        rows=collect_evidence("robot actuator",memory=m,use_offline=True)
        assert len(rows)==1 and rows[0].source_type=="wikipedia"
