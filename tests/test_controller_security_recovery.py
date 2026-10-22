"""Exercise protected state recovery in isolated temporary directories."""
from pathlib import Path
import os
import pytest
import subprocess

SOURCE = Path(__file__).resolve().parents[1]/'tools/Prometheus.HostAgent/Prometheus-Host-Agent.ps1'
PWSH = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'

@pytest.mark.skipif(os.name != 'nt', reason='Windows protected-state provider')
def test_missing_primary_does_not_erase_corrupt_history(tmp_path):
    script = tmp_path/'check.ps1'
    script.write_text(r'''
param([string]$Source,[string]$Scratch)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
foreach($name in @('Test-ProtectedFile','Protect-File','Get-SecurityState','Set-SecurityState')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing function'}
 . ([scriptblock]::Create($f[0].Extent.Text))
}
function Write-Audit {param($a,$b,$c)}
$suffixes=@('','.bak1','.bak2','.sha256','.bak1.sha256','.bak2.sha256')
$index=0
foreach($suffix in $suffixes){
 $case=Join-Path $Scratch ('orphan-'+$index);$index++
 [IO.Directory]::CreateDirectory($case)|Out-Null
 $SecurityState=Join-Path $case 'security-state.json'
 $artifact=$SecurityState+$suffix
 [IO.File]::WriteAllText($artifact,'unverified orphan history')
 $before=(Get-FileHash -LiteralPath $artifact).Hash
 $refused=$false
 try{$null=Get-SecurityState}catch{$refused=$true}
 if(!$refused){throw ('Orphan artifact erased history: '+$suffix)}
 $files=@(Get-ChildItem -LiteralPath $case -File)
 if($files.Count -ne 1 -or $files[0].FullName -cne $artifact -or (Get-FileHash -LiteralPath $artifact).Hash -cne $before){throw 'Refusal changed orphan files'}
}
$stateRoot=Join-Path $Scratch 'recovery'
[IO.Directory]::CreateDirectory($stateRoot)|Out-Null
$SecurityState=Join-Path $stateRoot 'security-state.json'
if((Get-SecurityState).highest_controller_version -ne '0.0.0'){throw 'Fresh state incorrect'}
[IO.File]::WriteAllText(($SecurityState+'.bak1'),'broken')
$refused=$false
try{$null=Get-SecurityState}catch{$refused=$true}
if(!$refused){throw 'Missing primary reset corrupt backup history'}
[IO.File]::WriteAllText(($SecurityState+'.bak1'),'{"highest_controller_version":"0.5.12"}')
Protect-File ($SecurityState+'.bak1')
if((Get-SecurityState).highest_controller_version -ne '0.5.12'){throw 'Highest backup not recovered'}
if(!(Test-ProtectedFile $SecurityState)){throw 'Recovered state integrity failed'}
Set-SecurityState ([version]'0.5.13')
if((Get-SecurityState).highest_controller_version -ne '0.5.13'){throw 'New state missing'}
[IO.File]::WriteAllText($SecurityState,'interrupted')
if((Get-SecurityState).highest_controller_version -ne '0.5.13'){throw 'Committed version lost after primary corruption'}
# A newer protected second backup must win over valid older primary/bak1.
$second=$SecurityState+'.bak2'
[IO.File]::WriteAllText($second,'{"highest_controller_version":"0.5.20","fixture":"exact bytes"}')
Protect-File $second
$secondHash=(Get-FileHash -LiteralPath $second).Hash
$secondChecksum=[IO.File]::ReadAllText($second+'.sha256')
$olderBackupHash=(Get-FileHash -LiteralPath ($SecurityState+'.bak1')).Hash
if((Get-SecurityState).highest_controller_version -ne '0.5.20'){throw 'Highest second backup not selected'}
if((Get-FileHash -LiteralPath $SecurityState).Hash -cne $secondHash -or [IO.File]::ReadAllText($SecurityState+'.sha256') -cne $secondChecksum){throw 'Restore did not preserve exact protected bytes'}
if((Get-FileHash -LiteralPath $second).Hash -cne $secondHash -or (Get-FileHash -LiteralPath ($SecurityState+'.bak1')).Hash -cne $olderBackupHash){throw 'Restore modified source backups'}
if(!(Test-ProtectedFile $SecurityState)){throw 'Restored second backup checksum is invalid'}
$expected=@($suffixes|ForEach-Object {'security-state.json'+$_}|Sort-Object)
$actual=@(Get-ChildItem -LiteralPath $stateRoot -File|Select-Object -ExpandProperty Name|Sort-Object)
if(($actual -join '|') -cne ($expected -join '|')){throw 'Unexpected state path created'}
Write-Output 'isolated protected-state recovery passed'
''', encoding='utf-8')
    result = subprocess.run([str(PWSH), '-NoProfile', '-File', str(script), '-Source', str(SOURCE),
                             '-Scratch', str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows protected-state provider')
@pytest.mark.parametrize('host', ['pwsh', 'windows-powershell'])
def test_committed_floor_survives_publication_faults(tmp_path, host):
    executable = PWSH if host == 'pwsh' else Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_file():
        pytest.skip('Requested Windows PowerShell host unavailable')
    script = r'''
param([string]$Source,[string]$Scratch)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse failed'}
foreach($name in @('Test-ProtectedFile','Protect-File','Get-SecurityState','Set-SecurityState')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing function'}
 . ([scriptblock]::Create($f[0].Extent.Text))
}
function Write-Audit {param($a,$b,$c)}
$SecurityState=Join-Path $Scratch 'security-state.json'
Set-SecurityState ([version]'0.5.13')
$originalTest=${function:Test-ProtectedFile}
$originalProtect=${function:Protect-File}
$script:crashAfterBackup=$true
function Test-ProtectedFile([string]$Path){
 $ok=& $originalTest $Path
 if($script:crashAfterBackup -and $ok -and $Path -eq ($SecurityState+'.bak2') -and (Get-Content $Path -Raw|ConvertFrom-Json).highest_controller_version -eq '0.5.14'){
  $script:crashAfterBackup=$false;throw 'Injected interruption after verified backup'
 }
 return $ok
}
$failed=$false
try{Set-SecurityState ([version]'0.5.14')}catch{if($_.Exception.Message -notlike '*Injected interruption*'){throw};$failed=$true}
if(!$failed){throw 'Backup fault was not reached'}
if((Get-SecurityState).highest_controller_version -ne '0.5.14'){throw 'Verified recovery floor lost after interruption'}
$script:checksumLock=$null
function Protect-File([string]$Path){
 & $originalProtect $Path
 if($Path.StartsWith($SecurityState+'.pending-')){
  $script:checksumLock=[IO.File]::Open(($SecurityState+'.sha256'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 }
}
$failed=$false
try{Set-SecurityState ([version]'0.5.15')}catch{$failed=$true}finally{if($script:checksumLock){$script:checksumLock.Dispose()}}
if(!$failed -or !$script:checksumLock){throw 'Checksum replacement fault was not reached'}
if(& $originalTest $SecurityState){throw 'Primary pair should be incomplete before recovery'}
if((Get-SecurityState).highest_controller_version -ne '0.5.15'){throw 'Recovery lost newer floor after checksum publication fault'}
if(!(& $originalTest $SecurityState)){throw 'Recovered primary pair invalid'}
if((Get-FileHash $SecurityState).Hash -ne (Get-FileHash ($SecurityState+'.bak2')).Hash){throw 'Recovery changed committed bytes'}
if(@(Get-ChildItem -LiteralPath $Scratch -Filter '*.pending-*').Count){throw 'Pending state artifacts leaked'}
$before=@(Get-ChildItem -LiteralPath $Scratch -File|Sort-Object Name|ForEach-Object {(Get-FileHash $_.FullName).Hash}) -join ','
$failed=$false
try{Set-SecurityState ([version]'0.5.14')}catch{$failed=$true}
if(!$failed){throw 'Lower floor accepted'}
$after=@(Get-ChildItem -LiteralPath $Scratch -File|Sort-Object Name|ForEach-Object {(Get-FileHash $_.FullName).Hash}) -join ','
if($before -ne $after){throw 'Refused lower floor changed state'}
Write-Output ('publication faults passed on '+$PSVersionTable.PSVersion)
'''
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    # Inline script avoids changing or bypassing the host's file execution policy.
    command = '& {' + script + '} -Source ' + quote(SOURCE) + ' -Scratch ' + quote(tmp_path)
    result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
