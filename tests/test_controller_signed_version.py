"""Exercise actual Get-Package with synthetic signing and isolated state only."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'tools/Prometheus.HostAgent/Prometheus-Host-Agent.ps1'

SCRIPT = r'''
param([string]$Source,[string]$Scratch)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse error'}
foreach($name in @('Convert-Version','Assert-ControllerProvenance','Get-VerifiedControllerPackage','Get-Package')){
 $function=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name
 if(@($function).Count -ne 1){throw 'Function missing'}
 . ([scriptblock]::Create($function.Extent.Text))
}
function Get-SecurityState {return [pscustomobject]@{highest_controller_version=$script:floor}}
function Set-SecurityState {throw 'Forbidden state mutation'}
function Write-Audit {throw 'Forbidden audit mutation'}
function Start-Process {throw 'Forbidden launch'}
$rsa=[Security.Cryptography.RSA]::Create(2048)
$request=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Isolated controller test',$rsa,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
$cert=$request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-1),[DateTimeOffset]::UtcNow.AddHours(1))
$TrustedThumbprint=@($cert.Thumbprint)
$cases=@(
 @{id='legacy';name='0.5.10';p=@{release='Prometheus-Controller-v0.5.10'};ok=$true},
 @{id='modern';name='0.5.11';p=@{release_version='0.5.11'};ok=$true},
 @{id='both';name='0.5.11';p=@{release='Prometheus-Controller-v0.5.11';release_version='0.5.11'};ok=$true},
 @{id='renamed-legacy';name='0.5.99';p=@{release='Prometheus-Controller-v0.5.10'};ok=$false},
 @{id='renamed-modern';name='0.5.99';p=@{release_version='0.5.11'};ok=$false},
 @{id='conflict';name='0.5.11';p=@{release='Prometheus-Controller-v0.5.10';release_version='0.5.11'};ok=$false},
 @{id='legacy-array';name='0.5.11';p=@{release=@('Prometheus-Controller-v0.5.11','Prometheus-Controller-v0.5.11')};ok=$false},
 @{id='version-array';name='0.5.11';p=@{release_version=@('0.5.11','0.5.11')};ok=$false},
 @{id='legacy-null';name='0.5.11';p=@{release=$null;release_version='0.5.11'};ok=$false},
 @{id='version-null';name='0.5.11';p=@{release='Prometheus-Controller-v0.5.11';release_version=$null};ok=$false},
 @{id='version-number';name='0.5.11';p=@{release_version=11};ok=$false},
 @{id='missing';name='0.5.11';p=@{};ok=$false},
 @{id='noncanonical';name='0.5.11';p=@{release_version='00.5.11'};ok=$false},
 @{id='object-array';name='0.5.11';p=@(@{release_version='0.5.11'});ok=$false;array=$true},
 @{id='uncovered';name='0.5.11';p=@{release_version='0.5.11'};ok=$false;omit=$true},
 @{id='duplicate';name='0.5.11';p=@{release_version='0.5.11'};ok=$false;duplicate=$true},
 @{id='tampered';name='0.5.11';p=@{release_version='0.5.11'};ok=$false;tamper=$true},
 @{id='bad-signature';name='0.5.11';p=@{release_version='0.5.11'};ok=$false;bad_signature=$true},
 @{id='lower-rejected';name='0.5.10';p=@{release_version='0.5.10'};ok=$false;floor='0.5.11'},
 @{id='forward-recovery';name='0.5.12';p=@{release_version='0.5.12'};ok=$true;floor='0.5.11'},
 @{id='after-recovery-old-rejected';name='0.5.11';p=@{release_version='0.5.11'};ok=$false;floor='0.5.12'}
)
$results=@()
try {
 foreach($case in $cases){
  $root=Join-Path $Scratch $case.id;$name='Prometheus-Controller-v'+$case.name
  $package=Join-Path $root $name;[IO.Directory]::CreateDirectory($package)|Out-Null
  $meta=Join-Path $root 'PROMETHEUS-SSD-STATUS.json'
  @{controller_release=$name}|ConvertTo-Json|Set-Content $meta
  $provenance=Join-Path $package 'RELEASE-PROVENANCE.json'
  $json=ConvertTo-Json -InputObject $case.p -Depth 5 -Compress
  if($case.array){$json='[{"release_version":"0.5.11"}]'}
  [IO.File]::WriteAllText($provenance,$json)
  $dummy=Join-Path $package 'runtime.txt';[IO.File]::WriteAllText($dummy,'unchanged old runtime fixture')
  $rows=@(@{path='runtime.txt';sha256=(Get-FileHash $dummy).Hash})
  if(!$case.omit){$entry=@{path='RELEASE-PROVENANCE.json';sha256=(Get-FileHash $provenance).Hash};$rows+=@($entry);if($case.duplicate){$rows+=@($entry)}}
  $manifest=Join-Path $package 'SHA256-MANIFEST.json'
  [IO.File]::WriteAllText($manifest,(ConvertTo-Json -InputObject $rows -Depth 5))
  $signature=$rsa.SignData([IO.File]::ReadAllBytes($manifest),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
  if($case.bad_signature){$signature[0]=$signature[0] -bxor 1}
  [IO.File]::WriteAllBytes((Join-Path $package 'SHA256-MANIFEST.sig'),$signature)
  [IO.File]::WriteAllBytes((Join-Path $package 'MANIFEST-PUBLIC.cer'),$cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
  if($case.tamper){[IO.File]::WriteAllText($provenance,'{"release_version":"0.5.99"}')}
  $script:floor=if($case.floor){$case.floor}else{'0.0.0'}
  $before=@(Get-ChildItem $root -Recurse -File|ForEach-Object {(Get-FileHash $_.FullName).Hash}) -join ','
  $accepted=$false;$reason=''
  try{$result=Get-Package $root;$accepted=$true}catch{$reason=$_.Exception.Message}
  if($accepted -and $result.Name -cne $name){throw 'Wrong returned identity'}
  if($accepted -ne $case.ok){throw ('Unexpected outcome '+$case.id+': '+$reason)}
  $pureAccepted=$false
  try{$pure=Get-VerifiedControllerPackage $root;$pureAccepted=$true}catch{}
  $pureExpected=$case.ok -or $case.id -in @('lower-rejected','after-recovery-old-rejected')
  if($pureAccepted -ne $pureExpected){throw ('Pure identity validation mismatch '+$case.id)}
  if($script:floor -ne $(if($case.floor){$case.floor}else{'0.0.0'})){throw 'Pure validation changed floor'}
  $after=@(Get-ChildItem $root -Recurse -File|ForEach-Object {(Get-FileHash $_.FullName).Hash}) -join ','
  if($before -cne $after){throw 'Package read mutated fixture'}
  $results+=@{id=$case.id;accepted=$accepted;passed=$true}
 }
 $results|ConvertTo-Json -Depth 4
}finally{$cert.Dispose();$rsa.Dispose()}
'''

@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell package boundary')
def test_actual_get_package_signed_version_boundary(tmp_path):
    pwsh=shutil.which('pwsh')
    if not pwsh:
        candidate=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
        if candidate.is_file(): pwsh=str(candidate)
    if not pwsh: pytest.skip('PowerShell 7 unavailable')
    script=tmp_path/'check.ps1';script.write_text(SCRIPT, encoding='utf-8')
    result=subprocess.run([pwsh,'-NoProfile','-File',str(script),'-Source',str(SOURCE),'-Scratch',str(tmp_path/'fixtures')],capture_output=True,text=True,timeout=45)
    assert result.returncode == 0, result.stdout+result.stderr
    rows=json.loads(result.stdout)
    assert len(rows)==21 and all(row['passed'] for row in rows)
