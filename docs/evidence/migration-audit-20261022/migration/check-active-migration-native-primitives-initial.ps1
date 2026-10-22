param([string]$Scratch,[string]$Python)
$ErrorActionPreference='Stop'
$full=[IO.Path]::GetFullPath($Scratch).TrimEnd('\');$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if(!$full.StartsWith($temp+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $full -Leaf) -notlike 'prometheus-active-fixture-*'){throw 'Temporary fixture root required'}
$id=[guid]::NewGuid().ToString('N');$name='Codex-Prometheus-Fixture-'+$id
$worker=Join-Path $full 'worker.py';$marker=Join-Path $full 'started.json';$otherMarker=Join-Path $full 'other.json'
[IO.File]::WriteAllText($worker,"import json,os,sys,time`nfrom pathlib import Path`np=Path(sys.argv[1]);p.write_text(json.dumps({'pid':os.getpid()}));time.sleep(120)`n")
$arguments='"'+$worker+'" "'+$marker+'" '+$id
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
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
 $node=$xml.SelectSingleNode('//*[local-name()="Settings"]/*[local-name()="Enabled"]');if($node){$node.InnerText='normalized'}
 return $xml.OuterXml
}
function Get-FixtureIdentity([int]$PidValue,[string]$Token){
 $p=[Diagnostics.Process]::GetProcessById($PidValue)
 try{$w=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PidValue);if([string]$w.CommandLine -notlike ('*'+$Token+'*')){throw 'Fixture token mismatch'};return @{pid=$PidValue;ticks=$p.StartTime.ToUniversalTime().Ticks;path=$p.MainModule.FileName;token=$Token}}finally{$p.Dispose()}
}
function Stop-ExactFixtureProcess($identity){
 $p=[Diagnostics.Process]::GetProcessById($identity.pid)
 try{if($p.StartTime.ToUniversalTime().Ticks -ne $identity.ticks -or $p.MainModule.FileName -ine $identity.path -or $identity.path -ine $Python){throw 'Process identity mismatch'}
 $w=Get-CimInstance Win32_Process -Filter ('ProcessId='+$identity.pid);if([string]$w.CommandLine -notlike ('*'+$identity.token+'*')){throw 'Process token mismatch'}
 $p.Kill();if(!$p.WaitForExit(10000)){throw 'Fixture process did not stop'}
 }finally{$p.Dispose()}
}
$registered=$false;$owned=$null;$other=$null;$checks=@()
try{
 $otherProcess=Start-Process -FilePath $Python -ArgumentList @(('"'+$worker+'"'),('"'+$otherMarker+'"'),('other-'+$id)) -WindowStyle Hidden -PassThru
 try{$other=Get-FixtureIdentity $otherProcess.Id ('other-'+$id)}finally{$otherProcess.Dispose()}
 $action=New-ScheduledTaskAction -Execute $Python -Argument $arguments
 $principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
 $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 $task=New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $id
 Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $task -ErrorAction Stop|Out-Null;$registered=$true
 $baseline=Task-Config
 Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 if((Task-Config) -cne $baseline){throw 'Disable altered unrelated task configuration'}
 Enable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 if((Task-Config) -cne $baseline){throw 'Enable altered unrelated task configuration'}
 $checks+='exact-task-enable-disable-config-preserved'
 Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask)
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 while(!(Test-Path -LiteralPath $marker) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
 if(!(Test-Path -LiteralPath $marker)){throw 'Fixture task did not start'}
 $pidValue=(Get-Content $marker -Raw|ConvertFrom-Json).pid;$owned=Get-FixtureIdentity $pidValue $id
 $wrong=@{}+$owned;$wrong.ticks++
 $refused=$false;try{Stop-ExactFixtureProcess $wrong}catch{if($_.Exception.Message -notlike '*identity mismatch*'){throw};$refused=$true}
 if(!$refused){throw 'Changed creation time accepted'}
 $wrong=@{}+$owned;$wrong.path='C:\foreign.exe'
 $refused=$false;try{Stop-ExactFixtureProcess $wrong}catch{if($_.Exception.Message -notlike '*identity mismatch*'){throw};$refused=$true}
 if(!$refused){throw 'Changed executable path accepted'}
 $checks+='creation-time-and-path-refusal'
 Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask)
 Start-Sleep -Milliseconds 300
 if((Get-Content $marker -Raw|ConvertFrom-Json).pid -ne $owned.pid){throw 'Duplicate task start'}
 $checks+='ignore-new-suppresses-repeat-start'
 $null=Get-FixtureIdentity $other.pid $other.token
 $checks+='unrelated-fixture-process-preserved'
 Stop-ExactFixtureProcess $owned;$owned=$null
 Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null
 $checks+='bounded-exact-process-stop'
}finally{
 if($owned){Stop-ExactFixtureProcess $owned}
 if($registered){$null=Assert-OwnedFixtureTask;Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false -ErrorAction Stop}
 if($other){Stop-ExactFixtureProcess $other}
}
if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){throw 'Fixture task remains'}
@{passed=$true;checks=$checks;task_removed=$true;scope='Unique generated temporary task and two generated processes only'}|ConvertTo-Json
