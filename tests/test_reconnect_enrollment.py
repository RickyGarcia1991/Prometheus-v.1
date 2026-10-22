"""Fault-injected transaction tests; provider is internal, no live task APIs called."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'tools/Prometheus.HostAgent/ReconnectEnrollment.psm1'
SCRIPT = r'''
param($Module,$Case)
$ErrorActionPreference='Stop'
Import-Module $Module -Force
$m=Get-Module ReconnectEnrollment
& $m {
 param($case)
 $script:s=@{events=[Collections.Generic.List[string]]::new();files=@{};task=$null;state=0;journal=0;phase=$null;failure=$false}
 $file=[pscustomobject]@{Name='candidate.exe';Hash='expected';Bytes=[byte[]](1,2,3)}
 $p=@{
  Verify={param($c)$script:s.events.Add('verify');if($case -eq 'signature'){throw 'signature'};[pscustomobject]@{Identity='fixed';Files=@([pscustomobject]@{Name='candidate.exe';Hash='expected';Bytes=[byte[]](1,2,3)})}}
  State={param($c)$script:s.state++;$tasks=@();if($case -in @('foreign','multiple','owned-collision') -or ($case -eq 'race-collision' -and $script:s.state -eq 2)){$tasks=@([pscustomobject]@{TaskName='Prometheus USB Reconnect Supervisor';Actions=@('foreign','extra')})};if($case -eq 'unreadable'){throw 'state unreadable'};[pscustomobject]@{Complete=($case -ne 'incomplete');Tasks=$tasks;Paused=($case -eq 'paused');Active=($case -eq 'active')}}
  Lock={param($c)$script:s.events.Add('lock');if($case -eq 'mutex'){throw 'mutex held'};return 'held'}
  Unlock={param($c,$l)$script:s.events.Add('unlock')}
  Hash={param($c,$n)if($case -eq 'different') {return 'different'};if($case -eq 'matching'){return 'expected'};if($script:s.files.ContainsKey($n)){return $script:s.files[$n]};return $null}
  Journal={param($c,$j)$script:s.journal++;$script:s.events.Add('journal');$script:s.phase=$j.phase;if($case -eq 'journal-before' -and $script:s.journal -eq 1){throw 'journal fault'};if($case -eq 'final-journal' -and $j.phase -eq 'enrolled-disabled'){throw 'final journal fault'}}
  Stage={param($c,$id,$pkg)$script:s.events.Add('stage');if($case -eq 'stage'){throw 'stage fault'}}
  Copy={param($c,$id,$f)$script:s.events.Add('copy');if($case -eq 'copy-before'){throw 'copy fault'};if($case -eq 'copy-partial'){$script:s.files[$f.Name]='partial';throw 'partial copy'};$script:s.files[$f.Name]=$f.Hash}
  Register={param($c,$id)$script:s.events.Add('register');if($case -eq 'register-before'){throw 'register fault'};$script:s.task=$id;if($case -eq 'register-after'){throw 'register response lost'};if($case -eq 'foreign-race'){$script:s.task='foreign';throw 'foreign replacement'}}
  OwnedTask={param($c,$id)if($case -eq 'ownership'){return $false};return $script:s.task -eq $id}
  RemoveTask={param($c,$id)$script:s.events.Add('remove-task');if($script:s.task -and $script:s.task -ne $id){throw 'different owner'};$script:s.task=$null}
  RemoveFile={param($c,$f)$script:s.events.Add('remove-file');if($script:s.files[$f.Name] -ne $f.Hash){throw 'different file'};$script:s.files.Remove($f.Name)}
 }
 try{$r=Invoke-ReconnectEnrollmentTransaction $p @{};$success=$true;$message=''}catch{$success=$false;$message=$_.Exception.Message}
 [pscustomobject]@{success=$success;message=$message;events=@($script:s.events);files=$script:s.files;task=$script:s.task;phase=$script:s.phase}|ConvertTo-Json -Depth 6 -Compress
} $Case
'''


def invoke(tmp_path, case):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell required')
    script = tmp_path / 'isolated.ps1'
    script.write_text(SCRIPT)
    result = subprocess.run([shell,'-NoProfile','-File',str(script),str(MODULE),case],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize('case', ['signature','foreign','multiple','owned-collision','incomplete','unreadable','paused','active','mutex','race-collision','different'])
def test_preflight_failures_do_not_mutate(tmp_path, case):
    r=invoke(tmp_path,case)
    assert not r['success']
    assert not {'journal','stage','copy','register'}.intersection(r['events'])
    assert r['files']=={} and r['task'] is None


def test_success_is_disabled_no_start_and_journal_before_mutation(tmp_path):
    r=invoke(tmp_path,'success')
    assert r['success'] and r['phase']=='enrolled-disabled'
    assert r['events'].index('journal') < r['events'].index('stage') < r['events'].index('copy') < r['events'].index('register')
    assert r['files']=={'candidate.exe':'expected'} and r['task']
    assert r['events'][-1]=='unlock'


def test_matching_existing_file_not_rewritten(tmp_path):
    r=invoke(tmp_path,'matching')
    assert r['success'] and 'copy' not in r['events']


@pytest.mark.parametrize('case', ['journal-before','stage','copy-before','register-before','register-after','ownership','final-journal'])
def test_faults_rollback_only_transaction_changes(tmp_path, case):
    r=invoke(tmp_path,case)
    assert not r['success']
    assert r['files']=={} and r['task'] is None
    assert r['phase']=='rolled-back'
    assert r['events'][-1]=='unlock'


@pytest.mark.parametrize('case', ['copy-partial','foreign-race'])
def test_uncertain_owner_preserved_with_incomplete_rollback(tmp_path,case):
    r=invoke(tmp_path,case)
    assert not r['success'] and r['phase']=='rollback-incomplete'
    assert r['files']
    assert 'remove-file' not in r['events']
    if case=='foreign-race': assert r['task']=='foreign'


def test_no_provider_bypass_is_exported(tmp_path):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    script=tmp_path/'exports.ps1'
    script.write_text("Import-Module '"+str(MODULE).replace("'","''")+"'; (Get-Module ReconnectEnrollment).ExportedFunctions.Keys | ConvertTo-Json")
    result=subprocess.run([shell,'-NoProfile','-File',str(script)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=='Start-ReconnectEnrollment'

PROVIDER_SCRIPT = r'''
param($Module,$Root,$Case)
$ErrorActionPreference='Stop';Import-Module $Module -Force;$m=Get-Module ReconnectEnrollment
& $m {
 param($root,$case)
 if($case -eq 'communication'){
  function Get-ScheduledTask { @() }
  function Get-CimInstance { [pscustomobject]@{ProcessId=999999;CommandLine=(Join-Path $root 'DesktopCommanderStartup\Run-DesktopCommander.ps1');ExecutablePath='C:\Windows\powershell.exe';SessionId=1;Name='powershell.exe'} }
  $provider=Get-ReconnectEnrollmentProvider
  $state=& $provider.State @{Root=(Join-Path $root 'Prometheus');Local=$root}
  if($state.Active -or !$state.Complete){throw 'Unchanged external communication incorrectly blocked'}
  'pass';return
 }
 if($case -eq 'journal'){
  $provider=Get-ReconnectEnrollmentProvider;$c=@{Root=$root}
  foreach($phase in @('prepared','copying','enrolled-disabled')){
   & $provider.Journal $c @{id='generated';phase=$phase}
   $saved=Get-Content (Join-Path $root 'Enrollment\generated\journal.json') -Raw|ConvertFrom-Json
   if($saved.phase -ne $phase){throw 'Journal replacement lost phase'}
  }
  'pass';return
 }
 if($case -eq 'bounds'){
  $path=Join-Path $root 'generated';[IO.File]::WriteAllBytes($path,[byte[]](1,2,3))
  if((Read-EnrollmentBytes $path 3).Length -ne 3){throw 'Valid bound failed'}
  $refused=$false;try{Read-EnrollmentBytes $path 2|Out-Null}catch{$refused=$true};if(!$refused){throw 'Oversized input accepted'}
  'pass';return
 }
 if($case -eq 'schema'){
  $policy=[pscustomobject]@{schema=1;mode='reconnect';quiet_host='Prometheus-QuietHost-v3.exe';external_runner_sha256=('a'*64)}
  Assert-EnrollmentPolicy $policy
  foreach($field in @('schema','mode','quiet_host','external_runner_sha256')){
   $copy=$policy|ConvertTo-Json|ConvertFrom-Json;$copy.$field=@(1,2);$bad=$false
   try{Assert-EnrollmentPolicy $copy}catch{$bad=$true};if(!$bad){throw ('Array coercion accepted '+$field)}
  }
  $policy|Add-Member extra value;$bad=$false;try{Assert-EnrollmentPolicy $policy}catch{$bad=$true};if(!$bad){throw 'Extra policy field accepted'}
  foreach($size in @('1',1.5,$true,-1,16777217)){$e=[pscustomobject]@{path='f.ps1';sha256=('a'*64);size_bytes=$size};$bad=$false;try{Assert-EnrollmentManifestEntry $e}catch{$bad=$true};if(!$bad){throw 'Size coercion accepted'}}
  'pass';return
 }
 if($case -eq 'task'){
  $script:task=[pscustomobject]@{TaskName='Prometheus USB Reconnect Supervisor';TaskPath='\';Description='Prometheus reconnect enrollment tx';Actions=@([pscustomobject]@{Execute=(Join-Path $root 'Prometheus-QuietHost-v3.exe');Arguments='reconnect';WorkingDirectory=$root});Principal=[pscustomobject]@{UserId='S-1-generated';RunLevel=0;LogonType=3};Settings=[pscustomobject]@{Enabled=$false;MultipleInstances=2;DisallowStartIfOnBatteries=$false;StopIfGoingOnBatteries=$false;StartWhenAvailable=$true;ExecutionTimeLimit='PT0S'};Triggers=@([pscustomobject]@{UserId='S-1-generated';CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}})}
  function Get-ScheduledTask {$script:task}
  $c=@{Root=$root;Sid='S-1-generated'}
  if(!(Test-ReconnectOwnedTask $c tx)){throw 'Exact disabled task rejected'}
  $script:task.Settings.Enabled=$true;if(Test-ReconnectOwnedTask $c tx){throw 'Enabled task accepted'};$script:task.Settings.Enabled=$false
  $script:task.Actions+= $script:task.Actions[0];if(Test-ReconnectOwnedTask $c tx){throw 'Multiple actions accepted'};$script:task.Actions=@($script:task.Actions[0])
  $script:task.Description='foreign';if(Test-ReconnectOwnedTask $c tx){throw 'Foreign transaction accepted'}
  'pass';return
 }
 throw 'unknown fixture'
} $Root $Case
'''


@pytest.mark.parametrize('case',['communication','bounds','schema','task','journal'])
def test_production_provider_boundaries_without_live_apis(tmp_path,case):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    script=tmp_path/'provider.ps1';script.write_text(PROVIDER_SCRIPT)
    result=subprocess.run([shell,'-NoProfile','-File',str(script),str(MODULE),str(tmp_path),case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='pass'
