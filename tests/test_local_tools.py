import pytest
from prometheus_assistant.local_tools import default_local_registry

def test_default_tools_are_read_only_and_executable(monkeypatch):
    import prometheus_assistant.local_tools as lt
    monkeypatch.setattr(lt,"resource_root",lambda:None)
    r=default_local_registry()
    assert r.execute("assistant","system_status","inspect").status=="completed"
    assert r.execute("assistant","resource_inventory","inspect").status=="completed"

def test_text_reader_is_confined_to_approved_root(tmp_path):
    root=tmp_path/"safe"; root.mkdir()
    inside=root/"a.txt"; inside.write_text("hello",encoding="utf-8")
    outside=tmp_path/"outside.txt"; outside.write_text("secret",encoding="utf-8")
    r=default_local_registry(read_roots=[root])
    assert r.execute("assistant","read_text","read",str(inside)).result=="hello"
    blocked=r.execute("assistant","read_text","read",str(outside))
    assert blocked.status=="failed" and "PermissionError" in blocked.reason

def test_text_reader_requires_an_approved_root(tmp_path):
    p=tmp_path/"a.txt"; p.write_text("hello",encoding="utf-8")
    r=default_local_registry()
    assert r.execute("assistant","read_text","read",str(p)).status=="failed"

def test_unknown_local_tool_remains_denied():
    assert default_local_registry().execute("assistant","shell","run").status=="denied"
