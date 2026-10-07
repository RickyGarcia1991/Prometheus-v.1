from pathlib import Path

def test_snapshot_builder_uses_tracked_files_and_hash_manifest():
    text=(Path(__file__).parents[1]/"tools"/"BUILD-SOURCE-SNAPSHOT.ps1").read_text(encoding="utf-8")
    assert "git ls-files" in text
    assert "Get-FileHash -Algorithm SHA256" in text
    assert "SOURCE-MANIFEST.json" in text
    assert "git archive" not in text
    assert "Snapshot verification failed" in text

def test_snapshot_builder_refuses_overwrite_and_cleans_failed_new_snapshot():
    text=(Path(__file__).parents[1]/"tools"/"BUILD-SOURCE-SNAPSHOT.ps1").read_text(encoding="utf-8")
    assert "Snapshot already exists" in text
    assert "Remove-Item -LiteralPath $dest -Recurse -Force" in text

def test_snapshot_builder_uses_release_label():
    text=(Path(__file__).parents[1]/"tools"/"BUILD-SOURCE-SNAPSHOT.ps1").read_text(encoding="utf-8")
    assert "ReleaseLabel='v0.7-dev'" in text
    assert '"Prometheus-"+$ReleaseLabel+"-"+$short' in text
