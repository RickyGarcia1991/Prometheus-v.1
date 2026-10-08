import json
import pytest
from urllib.parse import urlsplit, parse_qs
from prometheus_assistant.web_research import PublicResearchProvider, normalize_query, plain_text, _source
from prometheus_assistant.research import ResearchResult, ResearchSource
from prometheus_assistant import cli

def fixture_fetch(url):
    host=urlsplit(url).hostname
    if host=="api.mwmbl.org":
        return [{"url":"https://en.wikipedia.org/wiki/Robot","title":[{"value":"Robot"}],"extract":[{"value":"Web robot snippet"}]},
                {"url":"https://example.test/robot","title":[{"value":"Robot design"}],"extract":[{"value":"Technical details"}]}]
    if host=="en.wikipedia.org":
        return {"query":{"pages":{"1":{"title":"Robot","fullurl":"https://en.wikipedia.org/wiki/Robot","extract":"Robotics reference","index":1}}}}
    return {"message":{"items":[{"title":["Robot research"],"URL":"https://doi.org/10.123/robot",
                               "publisher":"Fixture","abstract":"<jats:p>Robot paper abstract.</jats:p>"}]}}

def test_federated_search_merges_distinct_sources_and_labels_scope():
    result=PublicResearchProvider(fetch=fixture_fetch).search("Search the web for robot")
    assert result.query=="robot"
    assert len(result.sources)==3
    assert len({s.url for s in result.sources})==3
    assert any("full paper not fetched" in s.excerpt for s in result.sources)
    assert any("full page not fetched" in s.excerpt for s in result.sources)
    assert len(result.diagnostics)==3
    assert all(len(s.content_hash)==64 for s in result.sources)

def test_partial_provider_outage_keeps_results_with_visible_diagnostics():
    def fetch(url):
        if urlsplit(url).hostname=="api.mwmbl.org": raise TimeoutError("fixture")
        return fixture_fetch(url)
    result=PublicResearchProvider(fetch=fetch).search("robot")
    assert result.sources
    assert "mwmbl: unavailable (TimeoutError)" in result.diagnostics
    assert "unavailable" in result.context()

def test_complete_outage_fails_closed():
    def fail(url): raise OSError("offline")
    with pytest.raises(RuntimeError,match="no usable evidence"):
        PublicResearchProvider(fetch=fail).search("robot")

def test_specific_provider_does_not_contact_other_services():
    seen=[]
    PublicResearchProvider("crossref",fetch=lambda url:seen.append(url) or fixture_fetch(url)).search("robot")
    assert len(seen)==1 and urlsplit(seen[0]).hostname=="api.crossref.org"

@pytest.mark.parametrize("query",[""," "*3,"x"*4001,"Search the web for"])
def test_invalid_query_never_contacts_network(query):
    with pytest.raises(ValueError):
        PublicResearchProvider(fetch=lambda _:pytest.fail("network")).search(query)

def test_query_strips_command_but_preserves_search_syntax():
    assert normalize_query("Please search the web for site:nasa.gov Mars?")=="site:nasa.gov Mars"

def test_markup_and_invalid_links():
    assert plain_text("<p>A &amp; <b>B</b></p>")=="A & B"
    assert _source("file:///secret","Title","body","label") is None
    assert _source("https://user:secret@example.test/x","Title","body","label") is None

def test_context_strictly_obeys_budget_with_status_and_multiple_sources():
    sources=tuple(ResearchSource.from_content(url=f"https://example.test/{i}",title="x",content="a"*100) for i in range(8))
    for budget in (1,100,500,1000,4000):
        assert len(ResearchResult("q",sources,("service: partial",)).context(budget)) <= budget

def test_direct_research_does_not_load_ollama(tmp_path,monkeypatch,capsys):
    monkeypatch.delenv("PROMETHEUS_RESEARCH_CONFIG",raising=False)
    monkeypatch.delenv("PROMETHEUS_RESOURCE_ROOT",raising=False)
    monkeypatch.setattr(cli,"OllamaClient",lambda *a,**k:pytest.fail("model loaded"))
    monkeypatch.setattr(cli,"PublicResearchProvider",lambda *a,**k:PublicResearchProvider(fetch=fixture_fetch))
    assert cli.main(["research","robot","--json"])==0
    assert json.loads(capsys.readouterr().out)["sources"]

def test_offline_override_blocks_direct_research_with_enabled_configuration(tmp_path,monkeypatch):
    path=tmp_path/"research.json"; path.write_text(json.dumps({"online_enabled":True,"provider":"federated"}))
    monkeypatch.setenv("PROMETHEUS_RESEARCH_CONFIG",str(path))
    monkeypatch.setattr(cli,"PublicResearchProvider",lambda *a,**k:pytest.fail("network constructed"))
    assert cli.main(["--offline","research","robot"])==1

def test_status_reads_configuration_without_network(tmp_path,monkeypatch,capsys):
    path=tmp_path/"research.json"; path.write_text(json.dumps({"online_enabled":True,"provider":"wikipedia","retain_research":True}))
    monkeypatch.setenv("PROMETHEUS_RESEARCH_CONFIG",str(path))
    monkeypatch.setattr(cli,"PublicResearchProvider",lambda *a,**k:pytest.fail("network"))
    assert cli.main(["research-status","--json"])==0
    result=json.loads(capsys.readouterr().out)
    assert result["provider"]=="wikipedia" and result["online_enabled"] and result["retain_research"]

@pytest.mark.parametrize("payload",[{"online_enabled":"yes"},{"provider":"unknown"},{"extra":True},[]])
def test_invalid_configuration_fails_closed(tmp_path,monkeypatch,payload):
    from prometheus_assistant.research_settings import load_research_settings
    path=tmp_path/"research.json"; path.write_text(json.dumps(payload))
    monkeypatch.setenv("PROMETHEUS_RESEARCH_CONFIG",str(path))
    with pytest.raises(ValueError):load_research_settings()

def test_offline_chat_web_command_does_not_search(tmp_path,monkeypatch):
    from prometheus_assistant.memory import MemoryStore
    prompts=iter(["/web robots","/exit"])
    monkeypatch.setattr(cli,"_console_input_or_shutdown",lambda *a:next(prompts))
    monkeypatch.setattr(cli,"_research_context_for_prompt",lambda *a,**k:pytest.fail("network"))
    class Client:model="fixture"
    with MemoryStore(tmp_path/"memory.db") as memory:
        session=memory.create_session("fixture")
        assert cli.interactive_chat(memory,Client(),session,online_research=False)==0

def test_prefetched_research_skips_unrelated_planning(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    class Client:
        model="fixture"
        def chat(self,messages,**kwargs):
            assert not kwargs.get("json_format"),"research unexpectedly planned tools"
            return "Grounded answer.",1
    source=ResearchSource.from_content(url="https://example.test/robot",title="Robot",content="robot evidence")
    with MemoryStore(tmp_path/"memory.db") as memory:
        session=memory.create_session("fixture")
        result=cli.agent_exchange(memory,Client(),session,"Search online for robot",research_context=ResearchResult("robot",(source,)).context())
    assert result["agent_attempts"]==0 and result["tools"]==["evidence"]

def test_research_numbered_citation_failure_returns_evidence_instead_of_draft(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    class Client:
        model="fixture"
        def chat(self,messages,**kwargs):
            assert kwargs["num_predict"]==160
            assert "Bibliographic metadata without an abstract" in messages[0]["content"]
            return "Invented improvement [2].",1
    source=ResearchSource.from_content(url="https://example.test/robot",title="Robot",content="Actual evidence")
    with MemoryStore(tmp_path/"memory.db") as memory:
        session=memory.create_session("fixture")
        result=cli.agent_exchange(memory,Client(),session,"Search online for robot",research_context=ResearchResult("robot",(source,)).context())
    assert "Invented improvement" not in result["reply"]
    assert "Actual evidence" in result["reply"]
    assert "unsupported citation formatting" in result["reply"]
