from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import zipfile

from .manifest import build_manifest, digest_file

DEFAULT_EXCLUDES = {".git", ".pytest_cache", "__pycache__", ".venv", "venv"}


def _iter_files(source: Path, excluded_roots: tuple[Path, ...] = ()) -> list[Path]:
    source = source.resolve()
    excluded = tuple(path.resolve() for path in excluded_roots)
    files: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in excluded):
            continue
        rel_parts = path.relative_to(source).parts
        if any(part in DEFAULT_EXCLUDES for part in rel_parts):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        files.append(path)
    return files


def create_snapshot(source: Path, output_dir: Path, label: str = "snapshot") -> tuple[Path, Path]:
    source = source.resolve()
    output_dir = output_dir.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {source}")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final_zip = output_dir / f"{label}-{timestamp}.zip"
    excluded_roots = (output_dir,) if output_dir.is_relative_to(source) else ()
    files = _iter_files(source, excluded_roots)
    manifest = build_manifest(source, files)
    payload = {
        "format": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_name": source.name,
        "files": [
            {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
            for entry in manifest
        ],
    }

    fd, temp_name = tempfile.mkstemp(prefix="prometheus-", suffix=".zip", dir=output_dir)
    os.close(fd)
    temp_zip = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, path.relative_to(source).as_posix())
            zf.writestr("PROMETHEUS_MANIFEST.json", json.dumps(payload, indent=2, sort_keys=True))
        temp_zip.replace(final_zip)
    except Exception:
        temp_zip.unlink(missing_ok=True)
        raise

    checksum = final_zip.with_suffix(final_zip.suffix + ".sha256")
    checksum.write_text(f"{digest_file(final_zip)}  {final_zip.name}\n", encoding="utf-8")
    return final_zip, checksum


def verify_checksum(archive_path: Path, checksum_path: Path | None = None) -> list[str]:
    archive_path = archive_path.resolve()
    checksum_path = (checksum_path or archive_path.with_suffix(archive_path.suffix + ".sha256")).resolve()
    if not checksum_path.exists():
        return []
    parts = checksum_path.read_text(encoding="utf-8").strip().split()
    if not parts:
        return [f"Checksum file is empty: {checksum_path}"]
    expected = parts[0].lower()
    actual = digest_file(archive_path)
    if actual != expected:
        return ["Archive SHA-256 sidecar mismatch"]
    return []


def verify_snapshot(archive_path: Path, verify_sidecar: bool = True) -> list[str]:
    archive_path = archive_path.resolve()
    errors: list[str] = []
    if verify_sidecar:
        errors.extend(verify_checksum(archive_path))
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            try:
                manifest = json.loads(zf.read("PROMETHEUS_MANIFEST.json"))
            except KeyError:
                return errors + ["Archive is missing PROMETHEUS_MANIFEST.json"]
            expected = {entry["path"]: entry for entry in manifest.get("files", [])}
            members = {name for name in zf.namelist() if name != "PROMETHEUS_MANIFEST.json"}
            if members != set(expected):
                missing = sorted(set(expected) - members)
                extra = sorted(members - set(expected))
                if missing:
                    errors.append(f"Missing files: {missing}")
                if extra:
                    errors.append(f"Unexpected files: {extra}")
            for name, meta in expected.items():
                if name not in members:
                    continue
                data = zf.read(name)
                actual = __import__("hashlib").sha256(data).hexdigest()
                if len(data) != meta["size"]:
                    errors.append(f"Size mismatch: {name}")
                if actual != meta["sha256"]:
                    errors.append(f"SHA-256 mismatch: {name}")
    except zipfile.BadZipFile:
        errors.append("Archive is not a valid ZIP file")
    return errors
