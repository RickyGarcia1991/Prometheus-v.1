"""Portable memory migration must preserve data and never overwrite existing SSD memory."""
import importlib.util
from pathlib import Path
import sqlite3

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "portable-memory-setup.py"
spec = importlib.util.spec_from_file_location("portable_setup", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def test_migrate_and_repeat_without_overwrite(tmp_path):
    source = tmp_path / "host.sqlite3"
    dest = tmp_path / "ssd" / "memory.sqlite3"
    from prometheus_assistant.memory import MemoryStore
    with MemoryStore(source) as store:
        session = store.create_session("test-model")
        store.save_exchange(session, "hello", "world")
    first = module.initialize(source, dest)
    assert first["status"] == "MIGRATED_VERIFIED"
    assert first["records"]["turns"] == 2
    with sqlite3.connect(dest) as db:
        db.execute("INSERT INTO sessions VALUES ('ssd-only','test','2026-01-01')")
        db.commit()
    again = module.initialize(source, dest)
    assert again["status"] == "EXISTS_UNCHANGED"
    assert again["records"]["sessions"] == 2

def test_missing_source_fails_without_creating_destination(tmp_path):
    dest = tmp_path / "ssd" / "memory.sqlite3"
    try:
        module.initialize(tmp_path / "missing.sqlite3", dest)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected missing source to fail")
    assert not dest.exists()
