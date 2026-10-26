"""Resume selects the enrolled v3 host without launching a real process."""
import os
from pathlib import Path
import subprocess
import pytest

@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle provider')
def test_resume_exact_owner_and_v3(tmp_path):
    source = Path(__file__).resolve().parents[1]/'tools/Prometheus.HostAgent/Prometheus-Lifecycle.ps1'
    script = tmp_path/'check.ps1'
    script.write_text(r'''
param([string]$Source,[string]$Root)
$ErrorActionPreference='Stop'
$env:LOCALAPPDATA=$Root
. $Source
$local=Join-Path $Root 'Prometheus';$dc=Join-Path $Root 'DesktopCommanderStartup'
[IO.Directory]::CreateDirectory($local)|Out-Null
[IO.Directory]::CreateDirectory($dc)|Out-Null
$quiet=Join-Path $local 'Prometheus-QuietHost-v3.exe'
[IO.File]::WriteAllText($quiet,'not executable fixture')
$script:started=$null
function Start-Process {param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle)$script:started=@($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle)}
$state=@{id='owner';drive='X:';usb_instance='fixture';phase='disconnected'}
Write-LifecycleJson (Join-Path $local 'eject-mode.json') $state
[IO.File]::WriteAllText((Join-Path $dc 'remote-stop.request'),'Prometheus eject owner')
[IO.File]::WriteAllText((Join-Path $dc 'runner-paused.request'),'another owner')
$refused=$false
try{Resume-Lifecycle @{id='wrong'} 'fixture'}catch{$refused=$true}
if(!$refused -or $script:started){throw 'Wrong owner resumed'}
Resume-Lifecycle $state 'fixture'
if($script:started[0] -cne $quiet -or $script:started[1] -cne 'desktop-commander' -or $script:started[3] -cne 'Hidden'){throw 'Incorrect v3 launch'}
if(Test-Path (Join-Path $dc 'remote-stop.request')){throw 'Owned pause retained'}
if([IO.File]::ReadAllText((Join-Path $dc 'runner-paused.request')) -cne 'another owner'){throw 'Foreign pause changed'}
if(Test-Path (Join-Path $local 'eject-mode.json')){throw 'Completed eject retained'}
''', encoding='utf-8')
    pwsh = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
    result = subprocess.run([str(pwsh), '-NoProfile', '-File', str(script), '-Source', str(source),
                             '-Root', str(tmp_path/'isolated')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
