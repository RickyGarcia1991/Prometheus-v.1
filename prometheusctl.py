"""Offline-friendly launcher; no package installation required."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prometheus_control_center.cli import main  # noqa: E402

raise SystemExit(main())
