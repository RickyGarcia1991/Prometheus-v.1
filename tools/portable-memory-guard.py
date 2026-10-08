"""Run the portable Prometheus CLI with a cooperative cross-process SSD lock."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

def acquire(path, timeout=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        handle.close()
        raise RuntimeError("Another portable Prometheus session holds the SSD memory lock")
    return handle

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    opts = parser.parse_args()
    root = opts.root.resolve()
    db = root / "Prometheus-Data" / "memory.sqlite3"
    if not db.is_file():
        raise FileNotFoundError(f"Portable memory not found: {db}")
    lock = acquire(root / "Prometheus-Data" / ".memory-session.lock")
    try:
        import sqlite3
        with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SSD memory integrity check failed: {integrity}")
        package = root / "Prometheus-v0.8.0-dev1-8c17eab"
        script = package / "prometheus.py"
        if not script.is_file():
            raise FileNotFoundError(script)
        env = os.environ.copy()
        env["PROMETHEUS_RESOURCE_ROOT"] = str(root / "Prometheus-Resources")
        env["OLLAMA_MODELS"] = str(root / "Prometheus-Resources" / "Ollama" / ".ollama" / "models")
        env["OLLAMA_NO_CLOUD"] = "true"
        command = [sys.executable, "-X", "utf8", str(script), "--memory", str(db)] + opts.args
        return subprocess.call(command, cwd=str(package), env=env)
    finally:
        lock.close()

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(3)
