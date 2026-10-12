"""Trust migration preparation and ephemeral RSA fixtures; no global keys or SSD writes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

PROJECT = Path(__file__).resolve().parents[1]
PREPARER = Path(os.environ.get('PROMETHEUS_TRANSITION_PREPARER', PROJECT/'work/trust-transition/prepare_transition.py'))
if not PREPARER.is_file():
    pytest.skip('The offline migration preparer is not part of the application payload.', allow_module_level=True)
SPEC = importlib.util.spec_from_file_location('trust_transition_preparer', PREPARER)
transition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transition)
NEW = 'A' * 40
PRODUCTION_NEW = '1E0F99439DD072E014D81C5574B325C97EA4AC98'


@pytest.mark.parametrize('value', ['', 'A'*39, 'A'*41, 'G'*40, ' A'*20, transition.OLD, None])
def test_new_pin_requires_distinct_strict_forty_hex(value):
    with pytest.raises(ValueError):
        transition.new_pin(value)


@pytest.mark.parametrize('original', [
    "$trusted='OLD'\nif($cert.Thumbprint -ne $trusted){throw 'untrusted'}",
    "$TrustedThumbprint='OLD'\nif($cert.Thumbprint -ne $TrustedThumbprint){throw 'untrusted'}",
    "if($certificate.Thumbprint -ne 'OLD'){throw 'untrusted'}",
])
def test_dual_pins_replaces_only_exact_existing_guard(original):
    data = original.replace('OLD', transition.OLD).encode()
    updated = transition.dual_pins(data, NEW)
    assert transition.OLD.encode() in updated and NEW.encode() in updated
    assert b'-notcontains' in updated and b'Thumbprint -ne' not in updated
    assert transition.dual_pins(updated, NEW) == updated
    with pytest.raises(ValueError):
        transition.dual_pins(data + b'\n' + data, NEW)


def test_bootstrap_updates_exact_release_recovery_and_verifier_together():
    source = ("if hashlib.sha256(verifier.read_bytes()).hexdigest()!='"+'b'*64+"':\n raise RuntimeError()\n"
              "if release_name!='Prometheus-v0.8.0-dev1-remote-20261010':\n raise RuntimeError()\n"
              "recovery=str(root/'Prometheus-Recovery/Remote-20261010/core')\n").encode()
    updated = transition.bootstrap(source, 'c'*64, 'Prometheus-v0.8.0-dev3-trust', 'Trust-20261010')
    assert b"hexdigest()!='" + b'c'*64 + b"'" in updated
    assert b'Prometheus-v0.8.0-dev3-trust' in updated and b'Trust-20261010/core' in updated
    with pytest.raises(ValueError):
        transition.bootstrap(source, 'c'*64, '../escape', 'Trust-20261010')
    with pytest.raises(ValueError, match='digest'):
        transition.bootstrap(source, "untrusted'", 'Prometheus-v0.8.0-dev3-trust', 'Trust-20261010')


def test_comment_containing_dual_pins_does_not_bypass_exact_guard_validation():
    data = ("# @('"+transition.OLD+"','"+NEW+"') -notcontains\nWrite-Output 'unverified'").encode()
    with pytest.raises(ValueError, match='already-transitioned'):
        transition.dual_pins(data, NEW)


@pytest.fixture(scope='module')
def signed_fixtures(tmp_path_factory):
    engine = os.environ.get('PROMETHEUS_TEST_PWSH') or shutil.which('pwsh')
    if not engine:
        pytest.skip('Set PROMETHEUS_TEST_PWSH to run ephemeral RSA signature fixtures.')
    root = tmp_path_factory.mktemp('ephemeral-signers')
    script = root/'fixtures.ps1'
    script.write_text(r"""
param([string]$Root,[string]$Verifier,[string]$OldThumbprint,[string]$NewThumbprint)
$ErrorActionPreference='Stop'
$keys=@{};$certs=@{};$pins=@{}
function Save-Package($Package,$Files,$Key,$Certificate) {
 [IO.Directory]::CreateDirectory($Package)|Out-Null
 $rows=@();$sha=[Security.Cryptography.SHA256]::Create()
 try {
  foreach($file in ($Files.Keys|Sort-Object)) {
   $bytes=[Text.Encoding]::UTF8.GetBytes($Files[$file]);$path=Join-Path $Package $file
   [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path))|Out-Null
   [IO.File]::WriteAllBytes($path,$bytes)
   $rows+=@{path=$file;size_bytes=$bytes.Length;sha256=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}
  }
 } finally {$sha.Dispose()}
 $manifest=[Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $rows -Depth 5 -Compress))
 [IO.File]::WriteAllBytes((Join-Path $Package 'SHA256-MANIFEST.json'),$manifest)
 [IO.File]::WriteAllBytes((Join-Path $Package 'SHA256-MANIFEST.sig'),$Key.SignData($manifest,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1))
 [IO.File]::WriteAllBytes((Join-Path $Package 'MANIFEST-PUBLIC.cer'),$Certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
}
try {
 foreach($name in @('old','new','rogue')) {
  $keys[$name]=[Security.Cryptography.RSA]::Create(2048)
  $request=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Ephemeral '+$name,$keys[$name],[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
  $certs[$name]=$request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddDays(1))
  $pins[$name]=$certs[$name].Thumbprint
 }
 foreach($name in @('old','new','rogue')) {
  $package=Join-Path $Root $name
  $files=@{
   'INSTALL-PROMETHEUS-HOST-AGENT.ps1'=('function SourceRoot { ''Prometheus-Host-Agent-v0.6.7'' };if($cert.Thumbprint -ne '''+$pins.old+'''){throw ''untrusted''}')
   'Prometheus-Host-Agent.ps1'=('$TrustedThumbprint='''+$pins.old+''';if($cert.Thumbprint -ne $TrustedThumbprint){throw ''untrusted''}')
   'RELEASE-PROVENANCE.json'='{"release":"Prometheus-Host-Agent-v0.6.7","retained_fix":"task ownership"}'
  }
  Save-Package $package $files $keys[$name] $certs[$name]
  $coreFiles=@{
   'prometheus.py'='# ephemeral verification fixture'
   'src/prometheus_assistant/cli.py'='# ephemeral verification fixture'
   'tools/Verify-Repair-Core.ps1'=[IO.File]::ReadAllText($Verifier).Replace($OldThumbprint,$pins.old).Replace($NewThumbprint,$pins.new)
  }
  Save-Package (Join-Path $Root ('core-'+$name)) $coreFiles $keys[$name] $certs[$name]
  $controllerFiles=@{}
  foreach($required in @('Prometheus.DriveController.exe','Prometheus.DriveController.dll','Prometheus.DriveController.deps.json','Prometheus.DriveController.runtimeconfig.json','Prometheus-Drive-Engine.ps1','Prometheus-Eject-Orchestrator.ps1','Prometheus-Safe-Eject.ps1')) {
   $controllerFiles[$required]='ephemeral verification fixture; never executed'
  }
  Save-Package (Join-Path $Root ('controller-'+$name)) $controllerFiles $keys[$name] $certs[$name]
 }
 $pins|ConvertTo-Json -Compress
} finally {foreach($value in $certs.Values){$value.Dispose()};foreach($value in $keys.Values){$value.Dispose()}}
""", encoding='utf-8')
    result = subprocess.run([engine, '-NoProfile', '-File', str(script), '-Root', str(root),
                             '-Verifier', str(PROJECT/'tools/Verify-Repair-Core.ps1'),
                             '-OldThumbprint', transition.OLD, '-NewThumbprint', PRODUCTION_NEW],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return Path(engine), root, json.loads(result.stdout)


def verify_fixture(signed_fixtures, name, *, additional=True):
    engine, root, pins = signed_fixtures
    command = [str(engine), '-NoProfile', '-File', str(PREPARER.with_name('Verify-Package-Signature.ps1')),
               '-Package', str(root/name), '-ExpectedThumbprint', pins['old']]
    if additional:
        command += ['-AdditionalTrustedThumbprint', pins['new']]
    return subprocess.run(command, capture_output=True, text=True, timeout=30)


def test_ephemeral_old_and_new_pass_rogue_and_old_only_new_fail(signed_fixtures):
    for name in ('old', 'new'):
        result = verify_fixture(signed_fixtures, name)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['private_key_accessed'] is False
    assert verify_fixture(signed_fixtures, 'rogue').returncode != 0
    assert verify_fixture(signed_fixtures, 'new', additional=False).returncode != 0


def test_altered_signature_is_rejected(signed_fixtures, tmp_path):
    engine, root, pins = signed_fixtures
    destination = tmp_path/'altered'
    shutil.copytree(root/'old', destination)
    (destination/'SHA256-MANIFEST.sig').write_bytes(b'not a valid signature')
    args = [str(engine), '-NoProfile', '-File', str(PREPARER.with_name('Verify-Package-Signature.ps1')),
            '-Package', str(destination), '-ExpectedThumbprint', pins['old']]
    assert subprocess.run(args, capture_output=True, timeout=30).returncode != 0


@pytest.mark.parametrize('signer,accepted', [('old', True), ('new', True), ('rogue', False)])
def test_actual_core_verifier_old_new_rogue_signatures(signed_fixtures, signer, accepted):
    engine, root, _ = signed_fixtures
    package = root/('core-'+signer)
    result = subprocess.run([str(engine), '-NoProfile', '-File',
                             str(package/'tools/Verify-Repair-Core.ps1'), '-Root', str(package)],
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is accepted, result.stderr
    if accepted:
        assert json.loads(result.stdout)['healthy'] is True


@pytest.mark.parametrize('signer,accepted', [('old', True), ('new', True), ('rogue', False)])
def test_actual_controller_verify_only_old_new_rogue(signed_fixtures, signer, accepted, tmp_path):
    engine, root, pins = signed_fixtures
    script = tmp_path/'Repair.ps1'
    script.write_text((PROJECT/'tools/Repair-Prometheus-Controller.ps1').read_text(encoding='utf-8-sig')
                      .replace(transition.OLD, pins['old']).replace(PRODUCTION_NEW, pins['new']))
    name = 'Prometheus-Controller-v0.5.11'
    shutil.copytree(root/('controller-'+signer), tmp_path/name)
    (tmp_path/'PROMETHEUS-SSD-STATUS.json').write_text(json.dumps({'controller_release': name}))
    cache = tmp_path/'absent-cache'
    result = subprocess.run([str(engine), '-NoProfile', '-File', str(script), '-Root', str(tmp_path),
                             '-CacheRoot', str(cache), '-VerifyOnly'],
                            capture_output=True, text=True, timeout=30)
    if accepted:
        assert result.returncode == 2, result.stderr  # Signed source valid, cache deliberately absent.
        assert json.loads(result.stdout)['signed_source_valid'] is True
    else:
        assert result.returncode != 0 and 'not trusted' in result.stderr
    assert not cache.exists()


def test_preparer_preserves_legacy_and_emits_exact_unsigned_allowlist(signed_fixtures, tmp_path, monkeypatch):
    engine, fixtures, pins = signed_fixtures
    monkeypatch.setattr(transition, 'OLD', pins['old'])
    ssd, repo, output = tmp_path/'ssd', tmp_path/'repo', tmp_path/'prepared'
    ssd.mkdir();(repo/'tools').mkdir(parents=True)
    shutil.copytree(fixtures/'old', ssd/transition.BASE_AGENT)
    guard = ("if hashlib.sha256(verifier.read_bytes()).hexdigest()!='"+'b'*64+"':\n raise RuntimeError()\n"
             "if release_name!='Prometheus-v0.8.0-dev2-capabilities-20261010':\n raise RuntimeError()\n"
             "recovery=str(root/'Prometheus-Recovery/Capabilities-20261010/core')\n").encode()
    for name in transition.SCRIPT_DESTINATIONS:
        data = ("if($certificate.Thumbprint -ne '"+pins['old']+"'){throw 'untrusted'}").encode()
        if name.endswith('portable-memory-guard.py'):
            data = guard
        path = ssd/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data)
        if name in transition.SCRIPT_DESTINATIONS[:2]:
            (repo/'tools'/Path(name).name).write_bytes(data)
    old = (ssd/transition.SCRIPT_DESTINATIONS[0]).read_bytes()
    monkeypatch.setattr(transition, 'OLD_VERIFIER_SHA256', hashlib.sha256(old).hexdigest())
    before = {str(path.relative_to(ssd)): path.read_bytes() for path in ssd.rglob('*') if path.is_file()}
    report = transition.prepare(ssd, repo, output, pins['new'], engine, 'Prometheus-v0.8.0-dev3-trust', 'Trust-20261010')
    assert report['state'] == 'unsigned_transition_not_activated'
    assert (output/'Verify-Repair-Core.legacy.ps1').read_bytes() == old
    destinations = {row['destination'] for row in report['replacements']}
    assert {name for name in destinations if not name.startswith(transition.NEW_AGENT+'/')} == set(transition.SCRIPT_DESTINATIONS)
    assert set(report['required_after_signing']) == {transition.NEW_AGENT+'/'+name for name in ('SHA256-MANIFEST.sig', 'MANIFEST-PUBLIC.cer')}
    for row in report['replacements']:
        data = (output/row['payload']).read_bytes()
        assert row['sha256'] == hashlib.sha256(data).hexdigest() and row['size_bytes'] == len(data)
    staged = output/'payload'/transition.NEW_AGENT
    assert not (staged/'SHA256-MANIFEST.sig').exists() and not (staged/'MANIFEST-PUBLIC.cer').exists()
    assert transition.NEW_AGENT in (staged/'INSTALL-PROMETHEUS-HOST-AGENT.ps1').read_text()
    assert json.loads((staged/'RELEASE-PROVENANCE.json').read_text())['base_provenance']['retained_fix'] == 'task ownership'
    assert {str(path.relative_to(ssd)): path.read_bytes() for path in ssd.rglob('*') if path.is_file()} == before
    with pytest.raises(ValueError, match='new and outside'):
        transition.prepare(ssd, repo, output, pins['new'], engine, 'Prometheus-v0.8.0-dev3-trust', 'Trust-20261010')


def test_source_hash_mismatch_and_unsafe_leaf_refused_before_staging(signed_fixtures, tmp_path, monkeypatch):
    engine, fixtures, pins = signed_fixtures
    monkeypatch.setattr(transition, 'OLD', pins['old'])
    package = tmp_path/'source'
    shutil.copytree(fixtures/'old', package)
    (package/'Prometheus-Host-Agent.ps1').write_text('corrupted')
    with pytest.raises(ValueError, match='mismatch'):
        transition.manifest_files(package, engine)
    for name in ('../outside', 'C:secret', 'CON', 'trailing.', 'nested/file'):
        with pytest.raises(ValueError, match='Unsafe'):
            transition.leaf_name(name)
