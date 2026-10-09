import json
import io
from pathlib import Path
from prometheus_assistant import builtin_tools
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.memory import MemoryStore

def test_builtin_registry_is_read_only(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        registry=build_builtin_registry(memory)
        for key in [("memory","lookup"),("knowledge","articles"),
                    ("knowledge","resources"),("system","summary")]:
            tool=registry.get(*key)
            assert not tool.mutates_state
            assert not tool.external_network
            assert not tool.command_execution

def test_memory_tool_preserves_provenance(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        memory.remember("decision","robot body","hybrid actuation",
                        source_type="user",source_ref="session:test",confidence=.9)
        registry=build_builtin_registry(memory)
        request=registry.get("memory","lookup").request("robot body",{"query":"robot body"})
        rows=json.loads(registry.executors()[("memory","lookup")](request))
    assert rows[0]["value"]=="hybrid actuation"
    assert rows[0]["source_ref"]=="session:test"


def test_project_list_is_bounded(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        registry=build_builtin_registry(memory)
        result=json.loads(registry.get("project","list").executor(registry.get("project","list").request("list",{})))
    assert any(row["path"]=="src" for row in result)

def test_project_replace_is_state_changing_and_requires_guardrail_approval(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        spec=build_builtin_registry(memory).get("project","replace")
        req=spec.request("change",{"path":"README.md","old":"Prometheus","new":"Prometheus"})
    assert req.mutates_state and not req.external_network and not req.command_execution


def test_project_search_prioritizes_exact_code_tokens(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        spec=build_builtin_registry(memory).get("project","search")
        result=json.loads(spec.executor(spec.request("find run_agent",{"query":"def run_agent"})))
    assert any(row["path"].endswith("agent.py") and "run_agent" in row["excerpt"] for row in result["results"][:5])


def test_project_search_skips_oversized_unreadable_and_disallowed_candidates(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir()
    oversized=root/'00-oversized.py';oversized.write_bytes(b'x'*(builtin_tools.MAX_PROJECT_FILE+1))
    unreadable=root/'01-unreadable.py';unreadable.write_text('def run_agent(): pass')
    wrong_type=root/'02-binary.exe';wrong_type.write_text('def run_agent(): pass')
    unavailable=root/'03-deleted.py'
    outside=tmp_path/'outside.py';outside.write_text('def run_agent(): SECRET_OUTSIDE')
    partial=root/'04-partial.py';partial.write_text('def run_agent_extra(): pass')
    exact=root/'05-exact.py';exact.write_text('def run_agent(): pass')
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:root)
    monkeypatch.setattr(builtin_tools,'_project_files',lambda _:iter([oversized,unreadable,wrong_type,unavailable,outside,partial,exact]))
    original_open=Path.open
    def open_candidate(path,*args,**kwargs):
        if path==unreadable:raise PermissionError('File cannot be read.')
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_candidate)
    with MemoryStore(tmp_path/'m.sqlite3') as memory:
        tool=build_builtin_registry(memory).get('project','search')
        result=json.loads(tool.executor(tool.request('find',{'query':'def run_agent'})))
    assert result['files_scanned']==2
    assert [row['path'] for row in result['results']]==['05-exact.py','04-partial.py']
    assert [row['score'] for row in result['results']]==[2,1]
    assert 'SECRET_OUTSIDE' not in json.dumps(result)


def test_project_search_bounds_a_file_that_grows_after_validation(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir()
    changed=root/'00-growing.py';changed.write_text('def run_agent(): pass')
    valid=root/'01-valid.py';valid.write_text('def run_agent(): pass')
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:root)
    original_open=Path.open
    read_sizes=[]
    class GrowingStream(io.BytesIO):
        def read(self,size=-1):
            read_sizes.append(size)
            return super().read(size)
    def open_candidate(path,*args,**kwargs):
        if path==changed:return GrowingStream(b'x'*(builtin_tools.MAX_PROJECT_FILE+50))
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_candidate)
    with MemoryStore(tmp_path/'m.sqlite3') as memory:
        tool=build_builtin_registry(memory).get('project','search')
        result=json.loads(tool.executor(tool.request('find',{'query':'run_agent'})))
    assert read_sizes==[builtin_tools.MAX_PROJECT_FILE+1]
    assert result['files_scanned']==1 and result['results'][0]['path']=='01-valid.py'


def test_project_search_preserves_scan_result_and_excerpt_limits(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir()
    for index in range(305):
        (root/f'{index:03}.py').write_text('def run_agent(): '+('x '*300))
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:root)
    with MemoryStore(tmp_path/'m.sqlite3') as memory:
        tool=build_builtin_registry(memory).get('project','search')
        result=json.loads(tool.executor(tool.request('find',{'query':'def run_agent'})))
    assert result['files_scanned']==300
    assert len(result['results'])==20
    assert all(len(row['excerpt'])==500 for row in result['results'])
    assert [row['path'] for row in result['results']]==[f'{index:03}.py' for index in range(20)]


def test_project_tests_requires_command_approval(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        spec=build_builtin_registry(memory).get("project","tests")
        req=spec.request("run focused tests",{"target":"tests/test_builtin_tools.py"})
    assert req.command_execution and not req.mutates_state and not req.external_network

def test_project_tests_rejects_escape_target(tmp_path):
    with MemoryStore(tmp_path/"m.sqlite3") as memory:
        spec=build_builtin_registry(memory).get("project","tests")
        req=spec.request("bad",{"target":"../outside"})
        import pytest
        with pytest.raises(ValueError,match="Invalid bounded test target"):
            spec.executor(req)
