param([string]$Scratch,[string]$Python,[switch]$CleanupOnly,[ValidateSet('none','after-other-launch','after-task-start-wait')][string]$Fault='none')
$ErrorActionPreference='Stop'
$full=[IO.Path]::GetFullPath($Scratch).TrimEnd('\');$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if(!$full.StartsWith($temp+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $full -Leaf) -notlike 'prometheus-active-fixture-*'){throw 'Temporary fixture root required'}
if((Get-Item -LiteralPath $full).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse fixture root refused'}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$statePath=Join-Path $full 'ownership.json';$worker=Join-Path $full 'worker.py';$marker=Join-Path $full 'started.json';$otherMarker=Join-Path $full 'other.json'
if($CleanupOnly){
 $state=Get-Content -LiteralPath $statePath -Raw|ConvertFrom-Json -AsHashtable
 $id=$state.id
 if($id -notmatch '^[a-f0-9]{32}$' -or $state.sid -cne $sid -or $state.python -ine $Python -or $state.root -ine $full){throw 'Recovery ownership record mismatch'}
}else{
 $id=[guid]::NewGuid().ToString('N')
 $state=@{id=$id;sid=$sid;python=$Python;root=$full;created=[DateTime]::UtcNow.ToString('o');identities=@()}
}
$name='Codex-Prometheus-Fixture-'+$id
$arguments='"'+$worker+'" "'+$marker+'" '+$id
$otherArguments='"'+$worker+'" "'+$otherMarker+'" other-'+$id
function Save-State {
 $state.task=$name;$state.arguments=$arguments;$state.other_arguments=$otherArguments
 $bytes=[Text.Encoding]::UTF8.GetBytes(($state|ConvertTo-Json -Depth 8))
 $pending=$statePath+'.pending';
 $stream=[IO.FileStream]::new($pending,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::Read)
 try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
 [IO.File]::Move($pending,$statePath,$true)
}
function Assert-OwnedFixtureTask {
 $t=Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop
 $psid=[string]$t.Principal.UserId
 if($psid -notmatch '^S-1-'){$psid=([Security.Principal.NTAccount]::new($psid)).Translate([Security.Principal.SecurityIdentifier]).Value}
 if($t.Description -cne $id -or @($t.Actions).Count -ne 1 -or [string]$t.Actions[0].Execute -ine $Python -or [string]$t.Actions[0].Arguments -cne $arguments -or $psid -cne $sid){throw 'Fixture task ownership mismatch'}
 return $t
}
function Task-Config {
 $null=Assert-OwnedFixtureTask
 [xml]$xml=Export-ScheduledTask -TaskName $name -TaskPath '\'
 $nodes=$xml.SelectNodes('/*[local-name()="Task"]/*[local-name()="Settings"]/*[local-name()="Enabled"]')
 if($nodes.Count -gt 1){throw 'Ambiguous task enabled setting'}
 foreach($node in $nodes){$null=$node.ParentNode.RemoveChild($node)}
 return $xml.OuterXml
}
function Get-FixtureIdentity([int]$PidValue,[string]$ExpectedArguments){
 $p=[Diagnostics.Process]::GetProcessById($PidValue)
 try{
  $w=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PidValue)
  $cmd=[string]$w.CommandLine
  # Exact complete argument suffix, expected executable, current SID and creation
  # bound are required even when recovering a launch whose capture was interrupted.
  if($p.MainModule.FileName -ine $Python -or !($cmd -ceq ('"'+$Python+'" '+$ExpectedArguments) -or $cmd -ceq ($Python+' '+$ExpectedArguments))){throw 'Fixture command identity mismatch'}
  $owner=Invoke-CimMethod -InputObject $w -MethodName GetOwnerSid
  $createdUtc=if($state.created -is [DateTime]){$state.created.ToUniversalTime()}else{[DateTime]::Parse($state.created).ToUniversalTime()}
  if($owner.ReturnValue -ne 0 -or $owner.Sid -cne $sid -or $p.StartTime.ToUniversalTime() -lt $createdUtc){throw 'Fixture owner/time mismatch'}
  return @{pid=$PidValue;ticks=$p.StartTime.ToUniversalTime().Ticks;path=$p.MainModule.FileName;arguments=$ExpectedArguments;command=$cmd}
 }finally{$p.Dispose()}
}
function Stop-ExactFixtureProcess($identity){
 $w=Get-CimInstance Win32_Process -Filter ('ProcessId='+$identity.pid)
 if(!$w){return}
 $p=[Diagnostics.Process]::GetProcessById($identity.pid)
 try{
  if($p.StartTime.ToUniversalTime().Ticks -ne $identity.ticks -or $p.MainModule.FileName -ine $identity.path -or $identity.path -ine $Python){throw 'Process identity mismatch'}
  $current=Get-FixtureIdentity $identity.pid $identity.arguments
  if($current.ticks -ne $identity.ticks -or $current.command -cne $identity.command){throw 'Process identity mismatch'}
  $p.Kill();if(!$p.WaitForExit(10000)){throw 'Fixture process did not stop'}
 }finally{$p.Dispose()}
}
function Find-ExactFixtureProcesses {
 foreach($w in @(Get-CimInstance Win32_Process)){
  foreach($expected in @($arguments,$otherArguments)){
   if([string]$w.CommandLine -ceq ('"'+$Python+'" '+$expected) -or [string]$w.CommandLine -ceq ($Python+' '+$expected)){
    Get-FixtureIdentity ([int]$w.ProcessId) $expected
   }
  }
 }
}
function Cleanup-Fixture {
 $errors=[Collections.Generic.List[string]]::new()
 # Disable verified task before inventory, preventing another owned task launch.
 try{if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null}}catch{$errors.Add('disable: '+$_.Exception.Message)}
 foreach($identity in @($state.identities)) {try{Stop-ExactFixtureProcess $identity}catch{$errors.Add('recorded process: '+$_.Exception.Message)}}
 try{foreach($identity in @(Find-ExactFixtureProcesses)){try{Stop-ExactFixtureProcess $identity}catch{$errors.Add('recovered process: '+$_.Exception.Message)}}}catch{$errors.Add('inventory: '+$_.Exception.Message)}
 try{if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){$null=Assert-OwnedFixtureTask;Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false -ErrorAction Stop}}catch{$errors.Add('task: '+$_.Exception.Message)}
 $remaining=@();try{$remaining=@(Find-ExactFixtureProcesses)}catch{$errors.Add('final inventory: '+$_.Exception.Message)}
 $taskRemains=[bool](Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue)
 return @{errors=@($errors);task_removed=(!$taskRemains);remaining_processes=$remaining;passed=(!$taskRemains -and $remaining.Count -eq 0 -and $errors.Count -eq 0);task=$name}
}
if($CleanupOnly){$cleanup=Cleanup-Fixture;$cleanup|ConvertTo-Json -Depth 8;if(!$cleanup.passed){exit 1};exit 0}
Save-State
[IO.File]::WriteAllText($worker,"import json,os,sys,time`nfrom pathlib import Path`np=Path(sys.argv[1]);q=p.with_name(p.name+'.'+str(os.getpid())+'.tmp');q.write_text(json.dumps({'pid':os.getpid()}));q.replace(p);time.sleep(120)`n")
function Capture-Started {
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 while(!(Test-Path -LiteralPath $marker) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
 if(!(Test-Path -LiteralPath $marker)){throw 'Fixture task did not start'}
 $identity=Get-FixtureIdentity (Get-Content $marker -Raw|ConvertFrom-Json).pid $arguments
 $state.identities+=@($identity);Save-State;return $identity
}
function Assert-SoleTaskProcess($identity){
 $matches=@(Find-ExactFixtureProcesses|Where-Object {$_.arguments -ceq $arguments})
 if($matches.Count -ne 1 -or $matches[0].pid -ne $identity.pid -or $matches[0].ticks -ne $identity.ticks){throw 'Task process missing or duplicated'}
 if((Get-Content $marker -Raw|ConvertFrom-Json).pid -ne $identity.pid){throw 'Duplicate task start marker'}
}
$checks=@();$failure=$null;$cleanup=$null
try{
 $otherProcess=Start-Process -FilePath $Python -ArgumentList $otherArguments -WindowStyle Hidden -PassThru
 try{if($Fault -eq 'after-other-launch'){throw 'Injected failure before other-process identity capture'};$other=Get-FixtureIdentity $otherProcess.Id $otherArguments;$state.identities+=@($other);Save-State}finally{$otherProcess.Dispose()}
 $action=New-ScheduledTaskAction -Execute $Python -Argument $arguments
 $principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
 $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 $task=New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $id
 Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $task -ErrorAction Stop|Out-Null
 $baseline=Task-Config;$state.task_config=$baseline;Save-State
 Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 if((Task-Config) -cne $baseline){throw 'Disable altered unrelated task configuration'}
 Enable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 if((Task-Config) -cne $baseline){throw 'Enable altered unrelated task configuration'}
 $checks+='exact-task-enable-disable-config-preserved'
 Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask);if($Fault -eq 'after-task-start-wait'){Start-Sleep -Seconds 60};$owned=Capture-Started
 foreach($field in @('ticks','path')){
  $wrong=@{}+$owned;if($field -eq 'ticks'){$wrong.ticks++}else{$wrong.path='C:\foreign.exe'}
  $refused=$false;try{Stop-ExactFixtureProcess $wrong}catch{if($_.Exception.Message -notlike '*identity mismatch*'){throw};$refused=$true}
  if(!$refused){throw ('Changed '+$field+' accepted')}
 }
 $checks+='creation-time-and-path-refusal'
 Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask);Start-Sleep -Seconds 2;Assert-SoleTaskProcess $owned
 $checks+='ignore-new-suppresses-repeat-start'
 Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 Stop-ExactFixtureProcess $owned
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 while((Assert-OwnedFixtureTask).State -eq 'Running' -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
 if((Assert-OwnedFixtureTask).State -eq 'Running'){throw 'Scheduler failed to observe stop'}
 $checks+='bounded-exact-process-stop'
 Remove-Item -LiteralPath $marker
 Enable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 if((Task-Config) -cne $baseline){throw 'Restart altered task configuration'}
 Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask);$restarted=Capture-Started
 if($restarted.pid -eq $owned.pid -and $restarted.ticks -eq $owned.ticks){throw 'Task did not produce a new process'}
 Assert-SoleTaskProcess $restarted;$checks+='same-registered-task-restarts-after-stop'
 # Simulate lost completion: the task already started, but caller retries Start.
 Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask);Start-Sleep -Seconds 2
 Assert-SoleTaskProcess $restarted
 if((Task-Config) -cne $baseline){throw 'Retry altered task configuration'}
 $checks+='lost-completion-start-retry-retains-single-process'
 $currentOther=Get-FixtureIdentity $other.pid $otherArguments
 if($currentOther.ticks -ne $other.ticks){throw 'Unrelated fixture replaced'}
 $checks+='unrelated-fixture-process-preserved'
}catch{$failure=$_.Exception.Message}finally{$cleanup=Cleanup-Fixture}
@{passed=(!$failure -and $cleanup.passed);checks=$checks;failure=$failure;cleanup=$cleanup;scope='Unique generated temporary task and generated processes only'}|ConvertTo-Json -Depth 8
if($failure -or !$cleanup.passed){exit 1}



