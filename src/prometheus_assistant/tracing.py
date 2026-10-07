from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


@dataclass(frozen=True)
class TraceEvent:
    trace_id: str
    kind: str
    actor: str
    action: str
    decision: str
    reason: str
    created_at: str


def new_trace_id() -> str:
    return "trace_" + uuid.uuid4().hex


class LocalTraceLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def record(self, *, trace_id: str, kind: str, actor: str, action: str,
               decision: str = "", reason: str = "") -> TraceEvent:
        event = TraceEvent(
            trace_id=trace_id, kind=kind, actor=actor, action=action,
            decision=decision, reason=reason,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")
        return event
