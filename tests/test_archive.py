from pathlib import Path
import zipfile

from prometheus_control_center.archive import create_snapshot, verify_checksum, verify_snapshot


def test_snapshot_round_trip(tmp_path: Path):
    source = tmp_path / "source"
    out = tmp_path / "out"
    source.mkdir()
    (source / "hello.txt").write_text("hello", encoding="utf-8")
    archive, checksum = create_snapshot(source, out, "test")
    assert archive.exists() and checksum.exists()
    assert verify_checksum(archive) == []
    assert verify_snapshot(archive) == []


def test_snapshot_excludes_real_env_files(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env").write_text("SECRET=bad", encoding="utf-8")
    (source / ".env.example").write_text("SECRET=", encoding="utf-8")
    archive, _ = create_snapshot(source, tmp_path / "out", "test")
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert ".env" not in names
    assert ".env.example" in names


def test_output_directory_inside_source_is_not_recursively_archived(tmp_path: Path):
    source = tmp_path / "source"
    out = source / "archives"
    source.mkdir()
    out.mkdir()
    (source / "app.txt").write_text("v1", encoding="utf-8")
    (out / "old.zip").write_bytes(b"old archive bytes")
    archive, _ = create_snapshot(source, out, "test")
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert "app.txt" in names
    assert "archives/old.zip" not in names


def test_sidecar_detects_archive_tampering(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")
    archive, _ = create_snapshot(source, tmp_path / "out", "test")
    with archive.open("ab") as handle:
        handle.write(b"tamper")
    assert verify_checksum(archive) == ["Archive SHA-256 sidecar mismatch"]
