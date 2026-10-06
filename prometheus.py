"""Run Prometheus local chat directly from this source tree."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from prometheus_assistant.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
