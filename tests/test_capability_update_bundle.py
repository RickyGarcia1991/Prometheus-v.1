"""Filesystem acceptance tests for unsigned release handoff and refusal paths."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capability_bundle", PROJECT / "tools/build-capability-update.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value.encode() if isinstance(value, str) else value)
    return target


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "BASE_VERIFIER_SHA256", bundle.VERIFIER_SHA256)
    source = tmp_path / "source"
    baseline = tmp_path / bundle.BASE_RELEASE
    write(source, "pyproject.toml", '[project]\nname = "example"\nversion = "0.8.0.dev3"\n')
    write(source, "prometheus.py", "print('candidate')\n")
    write(source, "src/prometheus_assistant/cli.py", "def main(): return 0\n")
    for name in ("tools/Verify-Repair-Core.ps1", "tools/Sign-Capability-Update.ps1", "docs/CAPABILITY-UPDATE.md"):
        write(source, name, (PROJECT / name).read_bytes())
    write(baseline, "tools/Verify-Repair-Core.ps1", (PROJECT / "tools/Verify-Repair-Core.ps1").read_bytes())
    write(baseline, "SHA256-MANIFEST.json", json.dumps([
        {"path": "prometheus.py", "sha256": hashlib.sha256(b"previous").hexdigest(), "size_bytes": 8},
        {"path": "old.py", "sha256": hashlib.sha256(b"old").hexdigest(), "size_bytes": 3},
    ]))
    return source, baseline, tmp_path / "bundle"


def test_repeat_build_is_byte_identical_and_does_not_touch_inputs(inputs):
    source, baseline, output = inputs
    before = {str(p): p.read_bytes() for root in (source, baseline) for p in root.rglob("*") if p.is_file()}
    metadata = bundle.build_bundle(source, baseline, output)
    os.utime(source / "prometheus.py", (1_000_000, 1_000_000))
    second = output.with_name("second")
    assert bundle.build_bundle(source, baseline, second) == metadata
    archive = f"{bundle.RELEASE}-unsigned.zip"
    assert (output / archive).read_bytes() == (second / archive).read_bytes()
    assert (output / "BUNDLE-CHECKSUMS.txt").read_bytes() == (second / "BUNDLE-CHECKSUMS.txt").read_bytes()
    assert all(Path(path).read_bytes() == content for path, content in before.items())
    assert metadata["changes"]["modified"] == ["prometheus.py"]
    assert metadata["changes"]["removed"] == ["old.py"]
    assert metadata["state"] == "unsigned_candidate_activation_pending"
    assert metadata["required_signer_thumbprint"] == bundle.TRUSTED_SIGNER
    bundle.verify_candidate(output / bundle.RELEASE, metadata["candidate_manifest_sha256"])


def test_export_excludes_signatures_cache_runtime_database_and_private_root_files(inputs):
    source, baseline, output = inputs
    excluded = ["SHA256-MANIFEST.sig", "SHA256-MANIFEST.json", "MANIFEST-PUBLIC.cer",
                ".env", ".git/config", "src/__pycache__/x.pyc", "src/state.sqlite3",
                "tools/runtime/key.pem", "tools/bin/private.exe", ".aws/credentials",
                "outputs/report.json", "src/package.egg-info/PKG-INFO"]
    for name in excluded:
        write(source, name, "must not ship")
    write(source, "src/vendor.dist-info/licenses/LICENSE", "Copyright and permission notice")
    write(source, "src/vendor.dist-info/METADATA", "Name: vendor\nVersion: 1\n")
    metadata = bundle.build_bundle(source, baseline, output)
    with zipfile.ZipFile(output / f"{bundle.RELEASE}-unsigned.zip") as archive:
        assert all(archive.read(name) != b"must not ship" for name in archive.namelist())
        assert f"{bundle.RELEASE}/SHA256-MANIFEST.json" in archive.namelist()
        assert not any(name.endswith((".sig", ".cer", ".sqlite3")) for name in archive.namelist())
        assert archive.read(f"{bundle.RELEASE}/src/vendor.dist-info/licenses/LICENSE") == b"Copyright and permission notice"
        assert f"{bundle.RELEASE}/src/vendor.dist-info/METADATA" in archive.namelist()
    provenance = json.loads((output / bundle.RELEASE / "RELEASE-PROVENANCE.json").read_text())
    assert provenance["release"] == bundle.RELEASE
    assert provenance["source_snapshot_sha256"] == metadata["source_snapshot_sha256"]


@pytest.mark.parametrize("private", ["config/production.json", "src/credentials.json", "src/key.pem", "tools/.env.secret"])
def test_private_inputs_refused_before_output(inputs, private):
    source, baseline, output = inputs
    write(source, private, "private")
    with pytest.raises(ValueError, match="distributable"):
        bundle.build_bundle(source, baseline, output)
    assert not output.exists()


@pytest.mark.parametrize("change", ["version", "verifier", "entrypoint", "baseline"])
def test_release_preconditions_are_checked_before_output(inputs, change):
    source, baseline, output = inputs
    if change == "version":
        write(source, "pyproject.toml", '[project]\nversion="0.8.0.dev1"\n')
    elif change == "verifier":
        write(source, "tools/Verify-Repair-Core.ps1", "unsigned replacement")
    elif change == "entrypoint":
        (source / "prometheus.py").unlink()
    else:
        baseline = baseline.with_name("wrong-baseline")
    with pytest.raises(ValueError):
        bundle.build_bundle(source, baseline, output)
    assert not output.exists()


def test_output_cannot_overwrite_or_be_inside_source_or_baseline(inputs):
    source, baseline, output = inputs
    for target in (source, source / "build", baseline / "candidate"):
        with pytest.raises(ValueError):
            bundle.build_bundle(source, baseline, target)
    assert not (source / "build").exists()
    assert not (baseline / "candidate").exists()


@pytest.mark.parametrize("change", ["corrupt", "extra", "stale-signature", "manifest"])
def test_candidate_validation_refuses_corruption_and_injection(inputs, change):
    source, baseline, output = inputs
    metadata = bundle.build_bundle(source, baseline, output)
    candidate = output / bundle.RELEASE
    name = {"corrupt": "prometheus.py", "extra": "src/injected.py",
            "stale-signature": "SHA256-MANIFEST.sig", "manifest": "SHA256-MANIFEST.json"}[change]
    write(candidate, name, "bad")
    with pytest.raises(ValueError):
        bundle.verify_candidate(candidate, metadata["candidate_manifest_sha256"])


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "src/../../x.py", "src//x.py",
                                  "src/NUL.py", "src/trailing. ", "C:/x.py", "src\\x.py"])
def test_malicious_manifest_paths_are_refused_even_with_matching_digest(inputs, path):
    source, baseline, output = inputs
    bundle.build_bundle(source, baseline, output)
    candidate = output / bundle.RELEASE
    manifest = json.dumps([{"path": path, "size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}]).encode()
    write(candidate, "SHA256-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.verify_candidate(candidate, hashlib.sha256(manifest).hexdigest())


def test_linked_source_is_refused(inputs):
    source, baseline, output = inputs
    target = write(source.parent, "external.py", "private")
    try:
        (source / "src/link.py").symlink_to(target)
    except OSError:
        pytest.skip("Creating a symlink is unavailable on this host")
    with pytest.raises(ValueError, match="Linked|Reparse"):
        bundle.build_bundle(source, baseline, output)
    assert not output.exists()


def test_powershell_validation_and_corruption_refusal(inputs):
    # Optional on non-Windows hosts. No certificate access, signing, or SSD I/O.
    engine = os.environ.get("PROMETHEUS_TEST_PWSH") or shutil.which("pwsh")
    if not engine:
        pytest.skip("PowerShell 7 is unavailable; set PROMETHEUS_TEST_PWSH to test the signing handoff")
    source, baseline, output = inputs
    bundle.build_bundle(source, baseline, output)
    expected = hashlib.sha256((output / "UPDATE-BUNDLE.json").read_bytes()).hexdigest()
    command = [engine, "-NoProfile", "-File", str(PROJECT / "tools/Sign-Capability-Update.ps1"),
               "-Bundle", str(output), "-ExpectedBundleSha256", expected, "-Mode", "Validate"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {"checksums_verified": True, "signature_verified": False,
                      "files": 7, "activation": "pending", "writes_performed": False}
    wrong_digest = command.copy()
    wrong_digest[wrong_digest.index("-ExpectedBundleSha256") + 1] = "0" * 64
    assert subprocess.run(wrong_digest, capture_output=True, text=True, timeout=30).returncode != 0
    sign_without_output = command.copy()
    sign_without_output[-1] = "Sign"
    refused_signing = subprocess.run(sign_without_output, capture_output=True, text=True, timeout=30)
    assert refused_signing.returncode != 0
    assert "requires a new, separate Output directory" in refused_signing.stderr
    write(output / bundle.RELEASE, "prometheus.py", "corrupted")
    refused = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0
    assert "mismatch" in refused.stderr.lower() or "verification failed" in refused.stderr.lower()
