"""PowerShell launcher control tests: every Scheduler cmdlet is synthetic."""
import hashlib,json,os,subprocess
from pathlib import Path
import pytest

BASE=Path(__file__).resolve().parent
PS7=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
HARNESS=r'''
param([string]$Scenario,[string]$WorkerPin)
$ErrorActionPreference='Stop'
$global:registered=$false;$global:started=0;$global:removed=0;$global:exports=0;$global:task=$null
function New-ScheduledTaskAction {param($Execute,$Argument,$WorkingDirectory) return @{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory}}
function New-ScheduledTaskPrincipal {param($UserId,$LogonType,$RunLevel) return @{UserId=$UserId;LogonType=3;RunLevel=0}}
function New-ScheduledTaskSettingsSet {param($MultipleInstances,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) if($ExecutionTimeLimit -ne [TimeSpan]::Zero){throw 'Autokill requested'};return @{ExecutionTimeLimit='PT0S'}}
function New-ScheduledTask {param($Action,$Principal,$Settings,$Description) return @{Actions=@($Action);Principal=$Principal;Settings=$Settings;Description=$Description;Triggers=@();State='Ready'}}
function Register-ScheduledTask {param($TaskName,$TaskPath,$InputObject) $global:registered=$true;$global:task=$InputObject}
function Get-ScheduledTask {param($TaskName,$TaskPath,$ErrorAction) if($global:registered){return $global:task}}
function Export-ScheduledTask {param($TaskName,$TaskPath) $global:exports++;if($Scenario -eq 'drift' -and $global:exports -gt 1){return '<changed/>'};return '<fixed/>'}
function Unregister-ScheduledTask {param($TaskName,$TaskPath,$Confirm) if($global:task.State -ne 'Ready'){throw 'Unregister of active task'};$global:removed++;$global:registered=$false}
function Start-ScheduledTask {
 param($InputObject)
 $global:started++
 if($Scenario -eq 'missing'){return}
 $global:task.State=if($Scenario -in @('Running','Queued')){$Scenario}else{'Ready'}
 $run=$global:task.Description
 $record=@{run_id=$run;operation='initialize';contract_sha256=('c'*64);entry_sha256=('e'*64);worker_finished=$true;passed=($Scenario -ne 'failure')}
 if($Scenario -eq 'mismatch'){$record.run_id='wrong'}
 $record|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $PSScriptRoot ('production-migration-profile-'+$run+'.private.json'))
}
& (Join-Path $PSScriptRoot 'invoke-active-migration-execution.ps1') -Operation initialize -WorkerSha256 $WorkerPin -EntrySha256 ('e'*64) -ContractSha256 ('c'*64) -WaitSeconds 0 | Out-Null
$exit=$LASTEXITCODE
$launch=Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.launch.private.json'|Select-Object -First 1
@{exit=$exit;started=$global:started;removed=$global:removed;registered=$global:registered;launch=(Get-Content -LiteralPath $launch.FullName -Raw|ConvertFrom-Json)}|ConvertTo-Json -Depth 12 -Compress
'''

@pytest.mark.skipif(os.name!='nt',reason='PowerShell control harness')
@pytest.mark.parametrize('scenario',['success','failure','Running','Queued','missing','mismatch','drift'])
def test_launcher_never_retries_or_cleans_unsettled_task(tmp_path,scenario):
    for name in ('invoke-active-migration-execution.ps1','run-active-migration-execution.ps1'):
        (tmp_path/name).write_bytes((BASE/name).read_bytes())
    harness=tmp_path/'harness.ps1';harness.write_text(HARNESS)
    pin=hashlib.sha256((tmp_path/'run-active-migration-execution.ps1').read_bytes()).hexdigest()
    run=subprocess.run([str(PS7),'-NoProfile','-NonInteractive','-File',str(harness),scenario,pin],capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert result['started']==(0 if scenario=='drift' else 1)
    assert result['removed']==(1 if scenario in ('success','failure') else 0)
    assert result['exit']==(1 if scenario in ('failure','mismatch','drift') else 0)
    assert result['launch']['automatic_retry'] is False and result['launch']['automatic_rollback'] is False
    if scenario=='missing':assert result['launch']['status']=='pending_or_refused'
