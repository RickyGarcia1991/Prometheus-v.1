from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str


def digest_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path, files: Iterable[Path]) -> list[ManifestEntry]:
    root = root.resolve()
    entries: list[ManifestEntry] = []
    for file_path in sorted((p.resolve() for p in files), key=lambda p: p.as_posix()):
        relative = file_path.relative_to(root).as_posix()
        entries.append(ManifestEntry(relative, file_path.stat().st_size, digest_file(file_path)))
    return entries
