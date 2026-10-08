"""Create a verified retained memory backup and prune only managed old copies."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from prometheus_assistant.recovery import backup_memory, restore_memory, validate_database
from tempfile import TemporaryDirectory

PREFIX = "prometheus-memory-auto-"

def run(source: Path, directory: Path, keep: int = 14):
    if keep < 2:
        raise ValueError("keep must be at least 2")
    source = source.expanduser().resolve()
    directory = directory.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if directory == source.parent:
        raise ValueError("Backup directory must differ from live database directory")
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = directory / f"{PREFIX}{timestamp}.sqlite3"
    before = validate_database(source)
    metadata = backup_memory(source, dest)
    try:
        with TemporaryDirectory(prefix="prometheus-restore-check-") as tmp:
            restored = Path(tmp) / "restored.sqlite3"
            restore_memory(dest, restored)
            if validate_database(restored) != before:
                raise ValueError("Restore verification failed: record counts differ")
    except Exception:
        # Do not prune on failure. Keep the backup for investigation.
        raise
    backups = sorted(directory.glob(f"{PREFIX}*.sqlite3"), key=lambda p: p.name, reverse=True)
    removed = []
    for old in backups[keep:]:
        manifest = old.with_suffix(old.suffix + ".json")
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data.get("sha256"), str):
                continue
        except (OSError, ValueError):
            continue
        old.unlink()
        manifest.unlink()
        removed.append(old.name)
    return {"result": "PASS", "backup": str(dest), "records": before,
            "sha256": metadata["sha256"], "pruned": removed, "retention": keep}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--destination", type=Path, required=True)
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()
    try:
        print(json.dumps(run(args.source, args.destination, args.keep), indent=2))
    except Exception as exc:
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
