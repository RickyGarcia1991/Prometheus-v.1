from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    worker: str
    action: str
    network: bool = False
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()


def permission_request(request_id: str, worker: str, action: str, *,
                       network: bool = False, read_paths: Iterable[str] = (),
                       write_paths: Iterable[str] = ()) -> PermissionRequest:
    return PermissionRequest(
        request_id=request_id, worker=worker, action=action, network=network,
        read_paths=tuple(read_paths), write_paths=tuple(write_paths),
    )


class PendingApprovalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, request: PermissionRequest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(request), sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> PermissionRequest | None:
        if not self.path.is_file():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return PermissionRequest(
            request_id=raw["request_id"], worker=raw["worker"], action=raw["action"],
            network=bool(raw.get("network", False)),
            read_paths=tuple(raw.get("read_paths", ())),
            write_paths=tuple(raw.get("write_paths", ())),
        )

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
