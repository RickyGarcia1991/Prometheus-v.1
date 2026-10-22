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
    assert set(json.loads(result.stdout))=={'Start-ReconnectEnrollment','Initialize-ReconnectAuthorization','Repair-ReconnectEnrollment'}

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
 if($case -eq 'runner'){
  $legacy=Join-Path $root 'DesktopCommanderStartup\Run-DesktopCommander.ps1';$current=Join-Path $root 'DesktopCommander\Start-AtSignIn.ps1'
  if((Get-EnrollmentRunnerPath $root) -ne $current){throw 'Missing legacy did not choose fixed current route'}
  [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($legacy))|Out-Null;[IO.File]::WriteAllText($legacy,'generated')
  if((Get-EnrollmentRunnerPath $root) -ne $legacy){throw 'Existing legacy not preferred'}
  function Get-EnrollmentAttributes($path){throw [UnauthorizedAccessException]::new('generated unreadable path')}
  $bad=$false;try{Get-EnrollmentRunnerPath $root|Out-Null}catch{$bad=$true};if(!$bad){throw 'Unreadable path allowed fallback'}
  'pass';return
 }
 if($case -eq 'journal'){
  $provider=Get-ReconnectEnrollmentProvider;$c=@{Root=$root}
  foreach($phase in @('prepared','copying','enrolled-disabled')){
   & $provider.Journal $c @{id=('1'*32);phase=$phase;package_identity=('a'*64)}
   $saved=Read-EnrollmentJournal $c ('1'*32) ('a'*64)
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


@pytest.mark.parametrize('case',['communication','bounds','schema','task','journal','runner'])
def test_production_provider_boundaries_without_live_apis(tmp_path,case):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    script=tmp_path/'provider.ps1';script.write_text(PROVIDER_SCRIPT)
    result=subprocess.run([shell,'-NoProfile','-File',str(script),str(MODULE),str(tmp_path),case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='pass'

RECOVERY_SCRIPT = r"""
param($Module,$Root,$Case)
$ErrorActionPreference='Stop';Import-Module $Module -Force;$m=Get-Module ReconnectEnrollment
& $m {
 param($root,$case)
 $c=@{Root=$root;Local=$root;Package=$root;Sid='generated';Fingerprint=('b'*64)}
 $p=Get-ReconnectEnrollmentProvider
 $script:hash=Get-EnrollmentHash ([byte[]](1,2,3));$script:checks=0
 $p.Verify={param($c)$script:checks++;if($case -like 'auth-*' -and $c.ContainsKey('PackageOnly') -and !$c.PackageOnly){$a=Read-EnrollmentBytes (Join-Path $c.Root 'authorized-host.json') 65536;$h=[IO.File]::ReadAllText((Join-Path $c.Root 'authorized-host.json.sha256'));if((Get-EnrollmentHash $a) -ne $h){throw 'authorization integrity'}};if($case -eq 'bad-signature'){throw 'signature refused'};[pscustomobject]@{Identity=('a'*64);Files=@([pscustomobject]@{Name='Prometheus-QuietHost-v3.exe';Hash=$script:hash;Bytes=[byte[]](1,2,3)})}}
 $p.State={param($c)[pscustomobject]@{Complete=$true;Paused=$false;Active=$false;Tasks=@()}}
 $p.Lock={param($c)'generated-lock'};$p.Unlock={param($c,$locks)}
 if($case -like 'auth-*' -or $case -eq 'bad-signature'){
  if($case -eq 'auth-half'){
   $script:originalWrite=${function:Write-EnrollmentExclusive}
   function Write-EnrollmentExclusive($path,$bytes){if($path.EndsWith('.sha256')){throw 'generated fault after primary flush'};& $script:originalWrite $path $bytes}
  }
  if($case -eq 'auth-history'){[IO.Directory]::CreateDirectory((Join-Path $root 'Enrollment\prior'))|Out-Null}
  if($case -eq 'auth-state'){[IO.File]::WriteAllText((Join-Path $root 'security-state.json.bak'),'retained')}
  $bad=$false;try{$result=Invoke-FreshReconnectAuthorization $p $c ('b'*64)}catch{$bad=$true}
  if($case -eq 'auth-half'){
   if(!$bad -or ![IO.File]::Exists((Join-Path $root 'authorized-host.json'))){throw 'Expected retained half-pair'}
   $before=[IO.File]::ReadAllText((Join-Path $root 'authorized-host.json'));$refused=$false;try{Invoke-FreshReconnectAuthorization $p $c ('b'*64)|Out-Null}catch{$refused=$true}
   if(!$refused -or [IO.File]::ReadAllText((Join-Path $root 'authorized-host.json')) -ne $before){throw 'Half pair overwritten'}
  }elseif($case -in @('auth-state','auth-history','bad-signature')){if(!$bad -or [IO.File]::Exists((Join-Path $root 'authorized-host.json'))){throw 'Unsafe authorization write'}}
  else{
   if($bad -or $result.status -ne 'authorized'){throw 'Authorization failed'}
   $auth=Read-EnrollmentBytes (Join-Path $root 'authorized-host.json') 65536
   $check=[IO.File]::ReadAllText((Join-Path $root 'authorized-host.json.sha256'))
   if((Get-EnrollmentHash $auth) -ne $check){throw 'Pair mismatch'}
   $before=$check;$again=Invoke-FreshReconnectAuthorization $p $c ('b'*64)
   if($again.status -ne 'existing-authorization-preserved' -or [IO.File]::ReadAllText((Join-Path $root 'authorized-host.json.sha256')) -ne $before){throw 'Identity overwritten'}
  }
  'pass';return
 }
 if($case -in @('foreign-task','remove-fault','owned-task')){
  $p.State={param($c)[pscustomobject]@{Complete=$true;Paused=$false;Active=$false;Tasks=@([pscustomobject]@{TaskName='Prometheus USB Reconnect Supervisor';TaskPath='\'})}}
  $p.OwnedTask={param($c,$id)$case -ne 'foreign-task'}
  $p.RemoveTask={param($c,$id)if($case -eq 'remove-fault'){throw 'generated unregister failure'};[IO.File]::WriteAllText((Join-Path $root 'removed-task'),'removed')}
 }
 $id='1'*32;$name='Prometheus-QuietHost-v3.exe'
 [IO.File]::WriteAllBytes((Join-Path $root $name),[byte[]](1,2,3))
 $j=[ordered]@{id=$id;phase='copying';task_was_absent=$true;created_files=@(@{name=$name;hash=$script:hash});completed_files=@(@{name=$name;hash=$script:hash});package_identity=('a'*64);task_enabled=$false;rollback_errors=@()}
 if($case -eq 'intent-only'){$j.completed_files=@()}
 if($case -eq 'changed-file'){[IO.File]::WriteAllBytes((Join-Path $root $name),[byte[]](4))}
 if($case -eq 'wrong-package'){$j.package_identity='c'*64}
 if($case -eq 'path-injection'){$j.created_files[0].name='../outside'}
 if($case -eq 'completed'){$j.phase='enrolled-disabled'}
 & $p.Journal $c $j
 if($case -eq 'plaintext'){
  [IO.File]::WriteAllText((Join-Path $root ('Enrollment\'+$id+'\journal.json')),($j|ConvertTo-Json -Depth 8))
 }
 if($case -eq 'tamper'){
  $path=Join-Path $root ('Enrollment\'+$id+'\journal.json');$envelope=Get-Content $path -Raw|ConvertFrom-Json
  $blob=[Convert]::FromBase64String($envelope.sealed);$blob[20]=$blob[20] -bxor 1;$envelope.sealed=[Convert]::ToBase64String($blob)
  [IO.File]::WriteAllText($path,($envelope|ConvertTo-Json))
 }
 if($case -eq 'swapped'){
  $newid='2'*32;$dir=Join-Path $root ('Enrollment\'+$newid);[IO.Directory]::CreateDirectory($dir)|Out-Null
  [IO.File]::Copy((Join-Path $root ('Enrollment\'+$id+'\journal.json')),(Join-Path $dir 'journal.json'));$id=$newid
 }
 $bad=$false;try{$result=Invoke-ReconnectRecovery $p $c $id}catch{$bad=$true}
 if($case -notin @('recovery','owned-task')){
  if(!$bad -or ![IO.File]::Exists((Join-Path $root $name))){throw 'Uncertain file was removed'}
 }else{
  if($bad -or $result.status -ne 'rolled-back' -or [IO.File]::Exists((Join-Path $root $name))){throw 'Exact recovery failed'}
  $p.State={param($c)[pscustomobject]@{Complete=$true;Paused=$false;Active=$false;Tasks=@()}}
  if($case -eq 'owned-task' -and ![IO.File]::Exists((Join-Path $root 'removed-task'))){throw 'Task not removed'}
  $again=Invoke-ReconnectRecovery $p $c $id;if($again.status -ne 'rolled-back'){throw 'Recovery not idempotent'}
 }
 'pass'
} $Root $Case
"""

@pytest.mark.parametrize('case',['auth-success','auth-state','bad-signature','recovery','intent-only','changed-file','wrong-package','path-injection','completed','auth-half','auth-history','foreign-task','remove-fault','owned-task','plaintext','tamper','swapped'])
def test_authorization_recovery_native_files(tmp_path,case):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    script=tmp_path/'recovery.ps1';script.write_text(RECOVERY_SCRIPT)
    root=tmp_path/'host';root.mkdir()
    result=subprocess.run([shell,'-NoProfile','-File',str(script),str(MODULE),str(root),case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='pass'

AUTH_RECOVERY_SCRIPT = r"""
param($Module,$Root,$Case)
$ErrorActionPreference='Stop';Import-Module $Module -Force;$m=Get-Module ReconnectEnrollment
& $m {
 param($root,$case)
 $c=@{Root=$root;Local=$root;Package=$root;Sid='generated';Fingerprint=('b'*64)}
 $p=Get-ReconnectEnrollmentProvider
 $p.Verify={param($c)[pscustomobject]@{Identity=('a'*64);Files=@()}}
 $p.State={param($c)[pscustomobject]@{Complete=$true;Paused=$false;Active=$false;Tasks=@()}}
 $p.Lock={param($c)'generated'};$p.Unlock={param($c,$l)}
 $result=Invoke-FreshReconnectAuthorization $p $c $c.Fingerprint;$id=$result.id
 $j=Read-EnrollmentJournal $c $id ('a'*64)
 if($case -eq 'already-complete'){$p.Journal={param($c,$j)throw 'Completed journal must not be rewritten'}}else{$j.phase='authorization-intent';& $p.Journal $c $j}
 $auth=Join-Path $root 'authorized-host.json';$sha=$auth+'.sha256'
 if($case -in @('primary-only','changed','protected','wrong-machine')){[IO.File]::Delete($sha)}
 if($case -eq 'checksum-only'){[IO.File]::Delete($auth)}
 if($case -eq 'neither'){[IO.File]::Delete($auth);[IO.File]::Delete($sha)}
 if($case -eq 'changed'){[IO.File]::WriteAllText($auth,'changed')}
 if($case -eq 'protected'){[IO.File]::WriteAllText((Join-Path $root 'security-state.json.bak'),'retained')}
 if($case -eq 'wrong-machine'){$c.Fingerprint='c'*64}
 $before=if([IO.File]::Exists($auth)){[IO.File]::ReadAllText($auth)}else{$null}
 $bad=$false;try{$result=Invoke-ReconnectRecovery $p $c $id}catch{$bad=$true}
 if($case -in @('changed','protected','wrong-machine')){
  if(!$bad -or [IO.File]::Exists($sha) -or [IO.File]::ReadAllText($auth) -ne $before){throw 'Unsafe recovery mutation'}
 }else{
  if($bad -or $result.status -ne $(if($case -eq 'already-complete'){'authorized-preserved'}else{'authorized'}) -or (Get-EnrollmentHash (Read-EnrollmentBytes $auth 65536)) -ne [IO.File]::ReadAllText($sha)){throw 'Pair recovery failed'}
 }
 'pass'
} $Root $Case
"""

@pytest.mark.parametrize('case',['primary-only','checksum-only','neither','changed','protected','wrong-machine','already-complete'])
def test_authenticated_authorization_recovery(tmp_path,case):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    script=tmp_path/'auth-recovery.ps1';script.write_text(AUTH_RECOVERY_SCRIPT)
    root=tmp_path/'host';root.mkdir()
    result=subprocess.run([shell,'-NoProfile','-File',str(script),str(MODULE),str(root),case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='pass'


def test_loaded_module_identity_cannot_follow_disk_mutation(tmp_path):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    copy=tmp_path/'ReconnectEnrollment.psm1';copy.write_bytes(MODULE.read_bytes())
    script=tmp_path/'identity.ps1'
    script.write_text(r"""
param($Module)
$ErrorActionPreference='Stop';Import-Module $Module -Force
& (Get-Module ReconnectEnrollment) {
 $hash=$script:EnrollmentLoadedHash;Assert-LoadedEnrollmentModule $hash
 $original=[IO.File]::ReadAllBytes($script:EnrollmentModulePath)
 [IO.File]::AppendAllText($script:EnrollmentModulePath,"`n# generated mutation")
 $bad=$false;try{Assert-LoadedEnrollmentModule $hash}catch{$bad=$true};if(!$bad){throw 'Disk mutation accepted'}
 [IO.File]::WriteAllBytes($script:EnrollmentModulePath,$original)
 $script:EnrollmentLoadedHash='0'*64
 $bad=$false;try{Assert-LoadedEnrollmentModule $hash}catch{$bad=$true};if(!$bad){throw 'Loaded identity mismatch accepted'}
 'pass'
}
""")
    result=subprocess.run([shell,'-NoProfile','-File',str(script),str(copy)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='pass'
