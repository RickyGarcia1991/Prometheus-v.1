"""Non-destructive SQLite backup/restore verification for Prometheus."""
import argparse
from contextlib import closing
import json
import sqlite3
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from prometheus_assistant.recovery import backup_memory, restore_memory, validate_database

def drill(source: Path):
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Memory database not found: {source}")
    before = validate_database(source)
    with tempfile.TemporaryDirectory(prefix="prometheus-memory-drill-") as tmp:
        base = Path(tmp)
        backup = base / "memory-backup.sqlite3"
        restored = base / "memory-restored.sqlite3"
        saved = backup_memory(source, backup)
        restored_meta = restore_memory(backup, restored)
        after = validate_database(restored)
        if before != after:
            raise AssertionError(f"Restore differs: {before} != {after}")
        with closing(sqlite3.connect(restored)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AssertionError("Restored database quick_check failed")
        return {"result": "PASS", "source": str(source), "records": after,
                "backup_sha256": saved["sha256"], "restored_sha256": restored_meta["sha256"],
                "temporary_files_removed": True}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing Prometheus SQLite memory database")
    args = parser.parse_args()
    print(json.dumps(drill(args.source), indent=2))
