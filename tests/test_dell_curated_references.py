"""Windows candidate integration: read-only HF and lab-hardware catalog."""
from __future__ import annotations

import json
import inspect

from prometheus_assistant import hf_resources
from prometheus_assistant.security_hardware_catalog import hardware_sources
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.cli import build_parser, main
from prometheus_assistant.ui_server import Interface


def test_curated_catalog_is_pinned_and_safe(monkeypatch):
    data=hf_resources.catalog()
    assert len(data["resources"]) == 8
    assert all(len(x["pinned_revision"]) == 40 for x in data["resources"])
    assert all(not x["auto_download"] and not x["auto_load"] and not x["auto_train"]
               and not x["requires_trust_remote_code"] for x in data["resources"])
    monkeypatch.setattr(hf_resources,"ollama_inventory",lambda:{})
    status=hf_resources.describe(query="MiniLM",limit=8,inspect_host=False)
    assert status["matched"] >= 1
    assert status["remote_model_code_executed"] is False


def test_security_hardware_is_read_only_and_not_certification():
    row=hardware_sources(package="hackrf")
    assert row["package"] == "hackrf"
    assert row["package_references"] == 814
    assert row["host"] == "Dell Windows primary workstation"
    assert row["source_audit_host"] == "Ubuntu Surface portable secondary"
    assert row["hardware_classes_sourced"] >= 30
    assert row["per_package_hardware_certified"] is False
    assert row["purchases_performed"] is False
    assert row["automatic_installation"] is False


def test_dell_core_registry_has_no_side_effects(tmp_path,monkeypatch):
    monkeypatch.setattr(hf_resources,"ollama_inventory",lambda:{})
    with MemoryStore(tmp_path / "temp_memory.sqlite3") as memory:
        registry=build_builtin_registry(memory)
        hf_spec=registry.get("knowledge","huggingface")
        hardware_spec=registry.get("security","hardware")
        for spec in (hf_spec, hardware_spec):
            assert spec.command_execution is False
            assert spec.external_network is False
            assert spec.mutates_state is False
        hf_request=hf_spec.request("Suggest vetted local retrieval",{"query":"MiniLM"})
        hf_data=json.loads(hf_spec.executor(hf_request))
        assert hf_data["known_repositories"] == 8
        hw_request=hardware_spec.request("Find a lab tool",{"package":"openocd"})
        hw_data=json.loads(hardware_spec.executor(hw_request))
        assert hw_data["package"] == "openocd"


def test_dell_commands_are_read_only_by_default(tmp_path,capsys,monkeypatch):
    monkeypatch.setattr(hf_resources,"ollama_inventory",lambda:{})
    parser=build_parser()
    assert "huggingface" in parser._subparsers._group_actions[0].choices
    assert "security-hardware" in parser._subparsers._group_actions[0].choices
    memory=tmp_path / "isolation.sqlite3"
    assert main(["--memory",str(memory),"huggingface","--search","MiniLM"]) == 0
    first=json.loads(capsys.readouterr().out)
    assert first["matched"] >= 1
    assert main(["--memory",str(memory),"security-hardware","--package","nmap"]) == 0
    second=json.loads(capsys.readouterr().out)
    assert second["package"] == "nmap"
    assert not second["purchases_performed"]
    assert not memory.exists()


def test_ui_tools_path_exposes_metadata_without_changing_prior_shape():
    source=inspect.getsource(Interface.execute)
    assert "huggingface_catalog" in source
    assert "security_hardware_catalog" in source
    assert "tool_details = coding_status()" in source
