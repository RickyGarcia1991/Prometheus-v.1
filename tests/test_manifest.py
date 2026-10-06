from pathlib import Path

from prometheus_control_center.manifest import build_manifest, digest_file


def test_manifest_is_deterministic(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    entries = build_manifest(tmp_path, [b, a])
    assert [e.path for e in entries] == ["a.txt", "b.txt"]
    assert entries[0].sha256 == digest_file(a)
