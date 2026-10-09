"""Run the portable Prometheus CLI with a cooperative cross-process SSD lock."""
import argparse
import hashlib
import re
from contextlib import closing
import json
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
    if opts.args[:1] == ["--"]:
        opts.args = opts.args[1:]
    root = opts.root.resolve()
    host_state = Path(os.environ.get('LOCALAPPDATA', str(root / 'Prometheus-Data'))) / 'Prometheus'
    if (host_state / 'eject-mode.json').exists():
        raise RuntimeError('An SSD eject is in progress. Finish or cancel it before starting a session.')
    metadata = json.loads((root / "PROMETHEUS-SSD-STATUS.json").read_text(encoding="utf-8-sig"))
    release_name = str(metadata["code_release"]).replace("\\", "/").rstrip("/").split("/")[-1]
    if not re.fullmatch(r"Prometheus-[A-Za-z0-9._-]+",release_name):
        raise ValueError("Invalid core release in SSD metadata")
    package = root / release_name
    script = package / "prometheus.py"
    db = root / "Prometheus-Data" / "memory.sqlite3"
    if not db.is_file():
        raise FileNotFoundError(f"Portable memory not found: {db}")
    if any(arg == "--memory" or arg.startswith("--memory=") for arg in opts.args):
        raise ValueError("The portable launcher owns --memory; use the core CLI for a separate database")
    lock = acquire(root / "Prometheus-Data" / ".memory-session.lock")
    shutdown = None
    try:
        # This bootstrap and the Windows runtime remain part of the trust base.
        verifier=root/'Prometheus-Resources/Tools/Verify-Repair-Core.ps1'
        if verifier.is_symlink() or not verifier.is_file() or hashlib.sha256(verifier.read_bytes()).hexdigest()!='1632df638f0cdf171820c1bc80407b4fda6794bcf03f59bb7f292e3a0f57a2f8':
            raise RuntimeError('Core verifier integrity failed; preserve the files for manual recovery.')
        if release_name!='Prometheus-v0.8.0-dev1-retest-20261009':
            raise RuntimeError('Active core does not match the signed expansion release; use its matching bootstrap.')
        check=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(verifier),
            '-Root',str(package),'-Recovery',str(root/'Prometheus-Recovery/Retest-20261009/core'),'-Repair'],
            capture_output=True,text=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check.returncode:
            raise RuntimeError('Signed core verification/repair stopped: '+(check.stderr or check.stdout)[-1600:])
        report=json.loads(check.stdout)
        if not report.get('signature_verified') or not report.get('healthy'):
            raise RuntimeError('Core verification did not confirm a healthy signed release.')
        if report.get('repaired'):
            print('Restored verified core files; prior contents retained in repair-history.',file=sys.stderr)
        import sqlite3
        with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SSD memory integrity check failed: {integrity}")
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"]="1"
        env["PROMETHEUS_RESOURCE_ROOT"] = str(root / "Prometheus-Resources")
        env["OLLAMA_MODELS"] = str(root / "Prometheus-Resources" / "Ollama" / ".ollama" / "models")
        env["OLLAMA_NO_CLOUD"] = "true"
        command = [sys.executable, "-B", "-X", "utf8", str(script), "--memory", str(db)] + opts.args
        if os.name == "nt" and (not opts.args or any(arg in ('chat', 'voice-chat', 'media', 'ui') for arg in opts.args)) and not any(arg == "--shutdown-request" or arg.startswith("--shutdown-request=") for arg in opts.args):
            shutdown = Path(env["LOCALAPPDATA"]) / "Prometheus" / "active-chat.shutdown"
            shutdown.parent.mkdir(parents=True, exist_ok=True)
            shutdown.unlink(missing_ok=True)
            command = [sys.executable, "-B", "-X", "utf8", str(script), "--shutdown-request", str(shutdown), "--memory", str(db)] + opts.args
        # Keep the SSD lock while a child finishes work. subprocess.call kills
        # its child on KeyboardInterrupt; that could orphan a media renderer.
        if (host_state / 'eject-mode.json').exists():
            raise RuntimeError('SSD eject began during startup verification; session start cancelled.')
        process = subprocess.Popen(command, cwd=str(package), env=env)
        while True:
            try:
                return process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                if shutdown is not None:
                    shutdown.write_text('Finish current work and stop.\n', encoding='utf-8')
                print('Waiting for the active session to exit before releasing the SSD.', flush=True)
    finally:
        lock.close()
        if shutdown is not None:
            shutdown.unlink(missing_ok=True)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(3)
