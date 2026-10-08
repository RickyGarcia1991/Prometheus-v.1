import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest

PATH = Path(__file__).resolve().parents[1] / "tools" / "portable-memory-guard.py"
spec = importlib.util.spec_from_file_location("portable_guard", PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

def test_lock_blocks_concurrent_writer(tmp_path):
    lockpath = tmp_path / "memory.lock"
    first = guard.acquire(lockpath)
    try:
        with pytest.raises(RuntimeError):
            guard.acquire(lockpath)
    finally:
        first.close()
    second = guard.acquire(lockpath)
    second.close()

def test_missing_portable_memory_fails_closed(tmp_path):
    result = subprocess.run([sys.executable, str(PATH), "--root", str(tmp_path), "sessions"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Portable memory not found" in result.stderr
