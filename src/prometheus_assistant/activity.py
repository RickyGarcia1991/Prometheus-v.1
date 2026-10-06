from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActivityEvent:
    timestamp: str
    stage: str
    status: str
    message: str
    metadata: dict[str, Any]


class ActivityLog:
    """Append-only JSONL audit trail for observable Prometheus work."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, stage: str, status: str, message: str, **metadata: Any) -> ActivityEvent:
        event = ActivityEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            stage=stage.strip(),
            status=status.strip(),
            message=message.strip(),
            metadata=metadata,
        )
        if not event.stage or not event.status or not event.message:
            raise ValueError("Activity stage, status, and message are required.")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("Activity limit must be positive.")
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()]
