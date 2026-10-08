"""Initialize portable SSD memory from an existing host database, without overwrites."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from prometheus_assistant.recovery import backup_memory, validate_database

def initialize(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    original = validate_database(source)
    if destination.exists():
        existing = validate_database(destination)
        return {"status": "EXISTS_UNCHANGED", "portable": str(destination), "records": existing}
    destination.parent.mkdir(parents=True, exist_ok=True)
    meta = backup_memory(source, destination)
    restored = validate_database(destination)
    if original != restored:
        raise RuntimeError("Memory migration verification mismatch")
    return {"status": "MIGRATED_VERIFIED", "portable": str(destination),
            "records": restored, "sha256": meta["sha256"]}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("destination")
    args = ap.parse_args()
    try:
        print(json.dumps(initialize(args.source, args.destination), indent=2))
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
