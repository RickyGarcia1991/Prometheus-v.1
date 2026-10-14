"""Opt-in sqlite-vec integration boundary; no dependency installation or migration.

Python standard library only until a reviewed sqlite-vec extension is explicitly
provided. Caller is responsible for generating embedding vectors and provenance.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3


class VectorExtensionNotConfigured(RuntimeError):
    pass


def connect_vector_db(db_path: str | Path, extension_path: str | Path | None = None) -> sqlite3.Connection:
    """Open an existing DB; load only a trusted, explicitly provided extension.

    Do not call this on an existing Prometheus memory database until a snapshot
    and migration/recovery tests have passed. No schema modification occurs here.
    """
    if extension_path is None:
        raise VectorExtensionNotConfigured("Explicit reviewed sqlite-vec binary path required")
    extension = Path(extension_path).expanduser().resolve(strict=True)
    if extension.suffix.lower() not in (".dll", ".so", ".dylib"):
        raise ValueError("Unexpected extension type")
    db = sqlite3.connect(str(db_path))
    try:
        db.enable_load_extension(True)
        try:
            db.load_extension(str(extension))
        finally:
            db.enable_load_extension(False)
        return db
    except BaseException:
        db.close()
        raise


def pack_embedding(values: list[float]) -> str:
    """Validate and serialize an embedding for sqlite-vec's vec_f32(json) API."""
    import math
    if not values or len(values) > 8192:
        raise ValueError("Embedding dimension must be between 1 and 8192")
    if any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
        raise ValueError("Embedding elements must be finite numbers")
    return json.dumps([float(v) for v in values], separators=(",", ":"), allow_nan=False)
