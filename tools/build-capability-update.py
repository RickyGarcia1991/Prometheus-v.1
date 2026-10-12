"""Build a deterministic, explicitly unsigned side-by-side core update.

This tool never changes a deployed SSD, signs code, starts an application, or
copies user memory/models. The original signing account must review the result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tomllib
import zipfile

RELEASE = "Prometheus-v0.8.0-dev3-main-workstation-20261010"
VERSION = "0.8.0.dev3"
BASE_RELEASE = "Prometheus-v0.8.0-dev1-remote-20261010"
TRUSTED_SIGNER = "898F702114B0F889763589C4057DC19CF1657CD5"
VERIFIER_SHA256 = "710d37bf2eb0318e9c20835a9deff5942377cb316c1b5d3bd934be6810419f53"
BASE_VERIFIER_SHA256 = "1632df638f0cdf171820c1bc80407b4fda6794bcf03f59bb7f292e3a0f57a2f8"
SIGNATURE_FILES = {"SHA256-MANIFEST.json", "SHA256-MANIFEST.sig", "MANIFEST-PUBLIC.cer"}
ROOT_FILES = {
    ".coveragerc", ".env.example", ".gitattributes", ".gitignore",
    "prometheus.py", "prometheusctl.py", "pyproject.toml", "vocabulary.json",
    "README.md", "RELEASE_NOTES.md", "RELEASE_NOTES_0.3.0.md", "REPAIR-NOTES.md",
    "START_PROMETHEUS.cmd", "END_PROMETHEUS.cmd",
    "PREVIOUS-RELEASE-PROVENANCE.json", "QUALITY-RELEASE-PROVENANCE.json",
    "EXPANSION-BASE-PROVENANCE.json",
    "LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE", "NOTICE.md",
}
SOURCE_DIRS = {".github", "config", "controller", "docs", "src", "tests", "tools"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "bin", "obj", "build", "dist", "runtime",
    "repair-history", "data", "memory", "models", ".ollama", ".agents",
    ".codex", ".aws", ".ssh", ".ai-devkit", ".worktrees", "outputs",
}
SOURCE_SUFFIXES = {
    ".py", ".ps1", ".cmd", ".md", ".toml", ".json", ".css", ".js",
    ".cjs", ".html", ".cs", ".csproj", ".xaml", ".yml", ".yaml",
    ".png", ".svg", ".ico", ".typed", ".txt", ".xml", ".sln",
}
SOURCE_NAMES = {"LICENSE", "COPYING", "NOTICE", "WHEEL", "REQUESTED", "RECORD", "METADATA", "INSTALLER"}
PRIVATE_NAMES = {
    "credentials.json", "secrets.json", "token.json", "tokens.json",
    "authorized-host.json", "host-identity.json", "client_secret.json",
}
MAX_FILES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")


def plain_path(path: Path) -> None:
    """Refuse symlinks and Windows junctions, including any existing ancestor."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError(f"Linked paths are refused: {candidate}")
        if candidate.exists():
            info = candidate.lstat()
            if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError(f"Reparse paths are refused: {candidate}")


def relative_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or any(ord(c) < 32 for c in name):
        raise ValueError("Unsafe manifest path")
    parts = name.split("/")
    if PurePosixPath(name).is_absolute() or any(
        not part or part in {".", ".."} or part.endswith((".", " "))
        or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
        for part in parts
    ):
        raise ValueError("Unsafe manifest path")
    return name


def read_bounded(path: Path) -> bytes:
    plain_path(path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Missing or oversized input: {path}")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Input grew beyond its limit: {path}")
    return data


def source_files(source: Path) -> dict[str, bytes]:
    plain_path(source)
    files: dict[str, bytes] = {}
    for current, directories, names in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        directories[:] = sorted(
            name for name in directories
            if name.casefold() not in SKIP_DIRS
            and not name.casefold().endswith(".egg-info")
            and (relative != Path(".") or name in SOURCE_DIRS)
        )
        for name in directories:
            plain_path(current_path / name)
        for name in sorted(names):
            path = current_path / name
            rel = path.relative_to(source).as_posix()
            if name in SIGNATURE_FILES or name == "RELEASE-PROVENANCE.json":
                continue
            if relative == Path(".") and name not in ROOT_FILES:
                continue
            if relative != Path("."):
                lower = name.casefold()
                if lower in PRIVATE_NAMES or (lower.startswith(".env") and lower != ".env.example"):
                    raise ValueError(f"Private configuration is not distributable: {rel}")
                if path.suffix.casefold() in {".pem", ".key", ".pfx", ".p12"}:
                    raise ValueError(f"Key material is not distributable: {rel}")
                if path.suffix.casefold() not in SOURCE_SUFFIXES and name not in SOURCE_NAMES:
                    continue
                if relative.parts[0] == "config" and "example" not in lower:
                    raise ValueError(f"Only example configuration is distributable: {rel}")
            relative_name(rel)
            if rel.casefold() in {key.casefold() for key in files}:
                raise ValueError(f"Case-colliding source path: {rel}")
            files[rel] = read_bounded(path)
            if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
                raise ValueError("Source exceeds the bounded core package limits")
    return files


def manifest_rows(files: dict[str, bytes]) -> list[dict]:
    return [{"path": name, "size_bytes": len(files[name]), "sha256": digest(files[name])}
            for name in sorted(files)]


def verify_candidate(candidate: Path, expected_manifest_sha256: str) -> list[dict]:
    """Checksum validation only; it deliberately does not authenticate a signer."""
    manifest = read_bounded(candidate / "SHA256-MANIFEST.json")
    if len(manifest) > 1_000_000 or digest(manifest) != expected_manifest_sha256:
        raise ValueError("Candidate manifest digest mismatch or size limit exceeded")
    rows = json.loads(manifest)
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_FILES:
        raise ValueError("Invalid candidate manifest")
    expected = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid manifest entry")
        name = relative_name(row.get("path"))
        if name.casefold() in expected or Path(name).name in SIGNATURE_FILES:
            raise ValueError("Duplicate or reserved manifest entry")
        if not isinstance(row.get("size_bytes"), int) or row["size_bytes"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256"))):
            raise ValueError("Invalid manifest size or hash")
        expected.add(name.casefold())
        data = read_bounded(candidate.joinpath(*name.split("/")))
        total += len(data)
        if total > MAX_TOTAL_BYTES or len(data) != row["size_bytes"] or digest(data) != row["sha256"]:
            raise ValueError(f"Candidate payload mismatch: {name}")
    if not {"prometheus.py", "src/prometheus_assistant/cli.py", "tools/verify-repair-core.ps1"}.issubset(expected):
        raise ValueError("Candidate omits a required core entry")
    actual = set()
    scanned = 0
    for current, directories, names in os.walk(candidate, followlinks=False):
        scanned += len(directories) + len(names)
        if scanned > 8192:
            raise ValueError("Candidate tree exceeds its scan limit")
        for name in directories + names:
            plain_path(Path(current) / name)
        for name in names:
            actual.add((Path(current) / name).relative_to(candidate).as_posix().casefold())
    if actual != expected | {"sha256-manifest.json"}:
        raise ValueError("Candidate contains unsigned, missing, or stale signature files")
    return rows


def build_bundle(source: Path, baseline: Path, output: Path) -> dict:
    source, baseline, output = (Path(os.path.abspath(path)) for path in (source, baseline, output))
    for path in (source, baseline, output):
        plain_path(path)
    if output.exists():
        raise ValueError("Output already exists; use a new directory instead of replacing it")
    if output.is_relative_to(source) or output.is_relative_to(baseline):
        raise ValueError("Output must be outside both source and deployed baseline")
    if baseline.name != BASE_RELEASE:
        raise ValueError("Unexpected baseline release")
    files = source_files(source)
    for required in ("prometheus.py", "src/prometheus_assistant/cli.py", "tools/Verify-Repair-Core.ps1",
                     "tools/Sign-Capability-Update.ps1", "docs/CAPABILITY-UPDATE.md"):
        if required not in files:
            raise ValueError(f"Source omits a required core or handoff file: {required}")
    version = tomllib.loads(files.get("pyproject.toml", b"").decode("utf-8-sig")).get("project", {}).get("version")
    if version != VERSION:
        raise ValueError(f"Expected package version {VERSION}; found {version!r}")
    verifier = files.get("tools/Verify-Repair-Core.ps1", b"")
    if digest(verifier) != VERIFIER_SHA256 or digest(read_bounded(baseline / "tools/Verify-Repair-Core.ps1")) != BASE_VERIFIER_SHA256:
        raise ValueError("The source or baseline verifier differs from its reviewed trust pin")
    baseline_manifest = read_bounded(baseline / "SHA256-MANIFEST.json")
    if len(baseline_manifest) > 1_000_000:
        raise ValueError("Baseline manifest too large")
    baseline_rows = json.loads(baseline_manifest.decode("utf-8-sig"))
    if not isinstance(baseline_rows, list) or not 0 < len(baseline_rows) <= MAX_FILES:
        raise ValueError("Invalid baseline manifest")
    baseline_hashes = {}
    for row in baseline_rows:
        name = relative_name(str(row["path"]).replace("\\", "/"))
        if name.casefold() in {key.casefold() for key in baseline_hashes}:
            raise ValueError("Duplicate baseline entry")
        baseline_hashes[name] = row["sha256"].lower()
    source_digest = digest(json_bytes(manifest_rows(files)))
    files["RELEASE-PROVENANCE.json"] = json_bytes({
        "release": RELEASE, "version": VERSION, "base_release": BASE_RELEASE,
        "baseline_manifest_sha256": digest(baseline_manifest),
        "source_snapshot_sha256": source_digest,
        "validation": "Read the accompanying test report; the bundle builder does not run tests.",
        "signing": "Unsigned until reviewed and signed; the original signer must authorize the separate main-PC trust transition.",
        "limits": ["SSD activation pending; bootstrap and recovery need a coordinated stopped-session update."],
    })
    manifest = json_bytes(manifest_rows(files))
    if len(manifest) > 1_000_000 or len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise ValueError("Generated candidate exceeds core package limits")
    changes = {"added": [], "modified": [], "removed": []}
    for name, data in sorted(files.items()):
        if name not in baseline_hashes:
            changes["added"].append(name)
        elif digest(data) != baseline_hashes[name]:
            changes["modified"].append(name)
    changes["removed"] = sorted(set(baseline_hashes) - set(files))
    metadata = {
        "schema_version": 1, "state": "unsigned_candidate_activation_pending",
        "release": RELEASE, "version": VERSION, "base_release": BASE_RELEASE,
        "baseline_manifest_sha256": digest(baseline_manifest),
        "baseline_signature_validation": "Not performed by this builder; validate using the pinned verifier.",
        "candidate_manifest_sha256": digest(manifest), "source_snapshot_sha256": source_digest,
        "required_signer_thumbprint": TRUSTED_SIGNER, "verifier_sha256": VERIFIER_SHA256,
        "file_count": len(files), "payload_bytes": sum(map(len, files.values())),
        "changes": changes, "validation": "Not run by the bundle builder.",
    }
    bundle_files = {f"{RELEASE}/{name}": data for name, data in files.items()}
    bundle_files[f"{RELEASE}/SHA256-MANIFEST.json"] = manifest
    bundle_files["UPDATE-BUNDLE.json"] = json_bytes(metadata)
    bundle_files["Sign-Capability-Update.ps1"] = files["tools/Sign-Capability-Update.ps1"]
    bundle_files["CAPABILITY-UPDATE.md"] = files["docs/CAPABILITY-UPDATE.md"]
    output.mkdir(parents=True)
    for name, data in sorted(bundle_files.items()):
        target = output.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    verify_candidate(output / RELEASE, metadata["candidate_manifest_sha256"])
    archive = output / f"{RELEASE}-unsigned.zip"
    # ZIP_STORED avoids zlib-version differences and gives byte-for-byte builds.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as package:
        for name, data in sorted(bundle_files.items()):
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            package.writestr(item, data)
    (output / "BUNDLE-CHECKSUMS.txt").write_text(
        f"{digest(bundle_files['UPDATE-BUNDLE.json'])}  UPDATE-BUNDLE.json\n"
        f"{digest(archive.read_bytes())}  {archive.name}\n", encoding="ascii", newline="\n")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    try:
        result = build_bundle(options.source, options.baseline, options.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Bundle refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
