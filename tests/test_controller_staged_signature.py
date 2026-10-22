"""Reject changes to the copied signed controller before publishing its cache."""
import json
import os
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
SCRIPT = r'''
param([string]$Source,[string]$Scratch,[string]$Case)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
foreach($name in @('Convert-Version','Assert-ControllerProvenance','Get-VerifiedControllerPackage','Get-Package','Sync-Controller')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing function'}
 . ([scriptblock]::Create($f[0].Extent.Text))
}
$script:floor='0.0.0'
function Get-SecurityState { [pscustomobject]@{highest_controller_version=$script:floor} }
function Set-SecurityState([version]$v){$script:floor=$v.ToString()}
function Write-Audit {}
function Start-Process {throw 'Forbidden launch'}
$ControllerRoot=Join-Path $Scratch 'cache'
$sourceRoot=Join-Path $Scratch 'source'
$package=Join-Path $sourceRoot 'Prometheus-Controller-v0.5.11'
[IO.Directory]::CreateDirectory($package)|Out-Null
@{controller_release='Prometheus-Controller-v0.5.11'}|ConvertTo-Json|Set-Content (Join-Path $sourceRoot 'PROMETHEUS-SSD-STATUS.json')
$rsa=[Security.Cryptography.RSA]::Create(2048)
$request=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Isolated staged controller test',$rsa,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
$cert=$request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-1),[DateTimeOffset]::UtcNow.AddHours(1))
$TrustedThumbprint=@($cert.Thumbprint)
try{
 [IO.File]::WriteAllText((Join-Path $package 'runtime.txt'),'original fixture')
 [IO.File]::WriteAllText((Join-Path $package 'RELEASE-PROVENANCE.json'),'{"release_version":"0.5.11"}')
 $entries=@('runtime.txt','RELEASE-PROVENANCE.json')|ForEach-Object {@{path=$_;sha256=(Get-FileHash (Join-Path $package $_)).Hash}}
 $manifest=Join-Path $package 'SHA256-MANIFEST.json'
 $entries|ConvertTo-Json|Set-Content $manifest
 [IO.File]::WriteAllBytes((Join-Path $package 'SHA256-MANIFEST.sig'),$rsa.SignData([IO.File]::ReadAllBytes($manifest),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1))
 [IO.File]::WriteAllBytes((Join-Path $package 'MANIFEST-PUBLIC.cer'),$cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
 $pkg=Get-Package $sourceRoot
 $copy=Get-Command Copy-Item -CommandType Cmdlet
 function Copy-Item {param($Path,$Destination,[switch]$Recurse,[switch]$Force)
  & $copy -Path $Path -Destination $Destination -Recurse:$Recurse -Force:$Force
  if($Case -eq 'coherent-manifest'){
   [IO.File]::WriteAllText((Join-Path $Destination 'runtime.txt'),'changed fixture')
   $m=Join-Path $Destination 'SHA256-MANIFEST.json';$rows=Get-Content $m -Raw|ConvertFrom-Json
   foreach($row in $rows){if($row.path -eq 'runtime.txt'){$row.sha256=(Get-FileHash (Join-Path $Destination 'runtime.txt')).Hash}}
   $rows|ConvertTo-Json|Set-Content $m
  }elseif($Case -eq 'signature'){[IO.File]::WriteAllBytes((Join-Path $Destination 'SHA256-MANIFEST.sig'),[byte[]]@(0,1,2))}
  elseif($Case -eq 'payload'){[IO.File]::WriteAllText((Join-Path $Destination 'runtime.txt'),'changed fixture')}
 }
 $accepted=$false
 try{$null=Sync-Controller $pkg;$accepted=$true}catch{if($Case -eq 'clean'){throw}}
 if($accepted -ne ($Case -eq 'clean')){throw 'Unexpected publication outcome'}
 if($Case -ne 'clean'){
  if(Test-Path (Join-Path $ControllerRoot $pkg.Name)){throw 'Rejected cache published'}
  if($script:floor -ne '0.0.0'){throw 'Rejected cache raised floor'}
 }elseif($script:floor -ne '0.5.11'){throw 'Clean publication did not commit floor'}
 @{passed=$true;case=$Case}|ConvertTo-Json
}finally{$cert.Dispose();$rsa.Dispose()}
'''

@pytest.mark.skipif(os.name != 'nt', reason='Windows controller cache')
@pytest.mark.parametrize('case', ['clean','coherent-manifest','signature','payload'])
def test_staged_signature_before_cache_publication(tmp_path, case):
    script = tmp_path/'check.ps1'
    script.write_text(SCRIPT, encoding='utf-8')
    result = subprocess.run([str(PWSH),'-NoProfile','-File',str(script),'-Source',str(ROOT/'tools/Prometheus.HostAgent/Prometheus-Host-Agent.ps1'),'-Scratch',str(tmp_path/'fixture'),'-Case',case],capture_output=True,text=True,timeout=40)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout)['passed'] is True
