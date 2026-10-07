param([string]$Drive="D:",[string]$Action="status")
$ErrorActionPreference="SilentlyContinue"
$root=$Drive.TrimEnd("\\")+"\\"
$stateDir=Join-Path $env:LOCALAPPDATA "RemovableMediaWorkStatus"
$driveKey=$Drive.Substring(0,1).ToUpper()
$stateFile=Join-Path $stateDir ("state-"+$driveKey+".json")
$logFile=Join-Path $stateDir ("activity-"+$driveKey+".log")
New-Item -ItemType Directory -Force $stateDir|Out-Null
function Save($phase,$message,$activity,$checks=@(),$errors=@()){
 $o=[ordered]@{phase=$phase;message=$message;activity=$activity;checks=@($checks);errors=@($errors);updated=(Get-Date).ToString("o")}
 $o|ConvertTo-Json -Depth 5|Set-Content -Encoding UTF8 $stateFile
 Add-Content $logFile ((Get-Date -Format s)+" ["+$phase+"] "+$activity)
 $o|ConvertTo-Json -Depth 5
}
function DriveProcesses {
 $letter=$Drive.Substring(0,1)+":\"
 @(Get-CimInstance Win32_Process|Where-Object {$_.ExecutablePath -like ($letter+"*") -or ($_.CommandLine -and $_.ProcessId -ne $PID -and $_.CommandLine -match ([regex]::Escape($letter)))})
}
function ExactBlockers {
 $letter=$Drive.Substring(0,1);$logical=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$letter+":'");if(!$logical){return @()}
 $assoc=Get-CimInstance Win32_LogicalDiskToPartition|Where-Object {$_.Dependent.DeviceID -eq ($letter+":")}|Select-Object -First 1;$tokens=@($logical.VolumeName)
 if($assoc){$m=[regex]::Match($assoc.Antecedent.DeviceID,"Disk #(\d+)");if($m.Success){$disk=Get-CimInstance Win32_DiskDrive -Filter ("Index="+$m.Groups[1].Value);$tokens+=@($disk.Model,$disk.PNPDeviceID,$disk.SerialNumber)}}
 $tokens=@($tokens|Where-Object {$_});$events=Get-WinEvent -FilterHashtable @{LogName="System";ProviderName="Microsoft-Windows-Kernel-PnP";Id=225;StartTime=(Get-Date).AddMinutes(-5)} -ErrorAction SilentlyContinue
 @($events|Where-Object {$msg=$_.Message;@($tokens|Where-Object {$msg -like ("*"+$_+"*")}).Count -gt 0})
}
function ExactRemovalVetoes {
 $events=Get-WinEvent -FilterHashtable @{LogName="Microsoft-Windows-Kernel-PnP/Device Management";Id=1000;StartTime=(Get-Date).AddMinutes(-5)} -ErrorAction SilentlyContinue
 @($events|Where-Object {$_.Message -match "could not be query removed" -and $_.Message -match "VID_0BC2&PID_2001|NZ0P54EM|STORAGE\\Volume"})
}
function UaspWarnings {
 $events=Get-WinEvent -FilterHashtable @{LogName="Microsoft-Windows-Kernel-PnP/Driver Watchdog";StartTime=(Get-Date).AddMinutes(-5)} -ErrorAction SilentlyContinue
 @($events|Where-Object {$_.Id -eq 900 -and $_.Message -match "VID_0BC2&PID_2001|NZ0P54EM" -and $_.Message -match "UASPStor"})
}
function Status {
 if(!(Test-Path $root)){return Save "green" "Windows has released the Prometheus volume." "RELEASED / SAFE TO DISCONNECT." @("Volume no longer mounted")}
 $p=@(DriveProcesses);if($p.Count){$owned=@($p|Where-Object {$_.Name -match "^(ollama|llama-server)(\.exe)?$" -or ($_.Name -ieq "cmd.exe" -and $_.CommandLine -match "START-PROMETHEUS-SSD\.cmd") -or ($_.Name -match "^python" -and $_.CommandLine -match "prometheus\.py|prometheus_assistant")});$unknown=@($p|Where-Object {$owned.ProcessId -notcontains $_.ProcessId});if($unknown.Count){return Save "red" "An unexpected process is using the Prometheus drive." ("BLOCKED: "+(($unknown|ForEach-Object {$_.Name+" PID "+$_.ProcessId}) -join ", ")) @() @("Unexpected drive-backed process remains")};return Save "yellow" "Prometheus is running." ("LIVE: "+(($owned|ForEach-Object {$_.Name+" PID "+$_.ProcessId}) -join ", ")+". Do not eject.") @("Only recognized Prometheus processes reference the drive")}
 $e=ExactBlockers;$live=@();foreach($ev in $e){$m=[regex]::Match($ev.Message,"process id (\d+)",[System.Text.RegularExpressions.RegexOptions]::IgnoreCase);if($m.Success){$blockPid=[int]$m.Groups[1].Value;if($blockPid -eq 4){$live+=$ev;continue};$bp=Get-CimInstance Win32_Process -Filter ("ProcessId="+$blockPid);if($bp -and ($bp.ExecutablePath -like ($root+"*") -or $bp.CommandLine -match [regex]::Escape($root))){$live+=$ev}}}
 if($live.Count){$last=$live|Sort-Object TimeCreated -Descending|Select-Object -First 1;return Save "red" "Windows reports an exact-device eject blocker." ("BLOCKED: "+(($last.Message -split "[\r\n]")[0])) @() @("Fresh Windows Kernel-PnP Event 225")}
 $v=@(ExactRemovalVetoes);if($v.Count){$last=$v|Sort-Object TimeCreated -Descending|Select-Object -First 1;return Save "red" "Windows storage stack vetoed device removal." ("BLOCKED: query-remove veto at "+$last.TimeCreated.ToString("HH:mm:ss")+".") @("No Prometheus drive process remains") @("Fresh Kernel-PnP Device Management Event 1000")}
 $u=@(UaspWarnings);if($u.Count){$last=$u|Sort-Object TimeCreated -Descending|Select-Object -First 1;return Save "red" "Windows UASP storage stack is still settling." ("BLOCKED: UASPStor watchdog warning at "+$last.TimeCreated.ToString("HH:mm:ss")+".") @("No Prometheus drive process remains") @("Fresh UASPStor watchdog warning")}
 Save "green" "Prometheus is stopped and the drive is idle." "READY TO TRY WINDOWS SAFELY REMOVE/EJECT." @("No drive-backed process or command reference","No live exact-device Event 225 blocker","No fresh query-remove veto","No fresh UASPStor watchdog warning")
}
if($Action -eq "start"){
 $launcher=Join-Path $root "START-PROMETHEUS-SSD.cmd"
 if(!(Test-Path $launcher)){Save "red" "Prometheus launcher is missing." "Cannot start." @() @("Missing launcher");exit 1}
 Save "yellow" "Starting Prometheus..." "Launching SSD runtime and model. Do not eject." @("Launcher found")|Out-Null
 Start-Process "cmd.exe" -ArgumentList "/c",("`""+$launcher+"`" chat")
 Start-Sleep 2
 $active=@(DriveProcesses)
 if($active.Count){Save "yellow" "Prometheus is starting / active." ("LIVE: "+(($active|ForEach-Object {$_.Name}) -join ", ")+". Do not eject.") @("Prometheus process tree detected")}else{Save "red" "Prometheus did not remain running." "Launcher returned but no Prometheus drive process was detected." @() @("Startup process missing")}
 exit 0
}
if($Action -eq "stop"){
 $initialTargets=@(DriveProcesses)
 if(!$initialTargets.Count){Status;exit 0}
 Save "yellow" "Stopping Prometheus..." "Requesting graceful chat shutdown before releasing model processes." @("Graceful shutdown requested")|Out-Null
 $shutdownDir=Join-Path $env:LOCALAPPDATA "Prometheus"
 $shutdownRequest=Join-Path $shutdownDir "active-chat.shutdown"
 New-Item -ItemType Directory -Force $shutdownDir|Out-Null
 Set-Content -Encoding ASCII $shutdownRequest (Get-Date).ToString("o")
 $deadline=(Get-Date).AddSeconds(20)
 do{
  $targets=@(DriveProcesses)
  $chat=@($targets|Where-Object {$_.Name -match "^python" -and $_.CommandLine -match "prometheus\.py|prometheus_assistant"})
  if(!$chat.Count){break}
  Start-Sleep -Milliseconds 250
 }while((Get-Date) -lt $deadline)
 if($chat.Count){
  Save "red" "Prometheus chat did not close cleanly." ("BLOCKED: "+(($chat|ForEach-Object {$_.Name+" PID "+$_.ProcessId}) -join ", ")) @("20-second graceful shutdown window expired") @("No forced chat termination was performed")
  exit 2
 }
 Save "yellow" "Chat closed cleanly." "Verifying Prometheus databases before model shutdown." @("Zero active Prometheus chat clients")|Out-Null
 $localPrometheus=Join-Path $env:LOCALAPPDATA "Prometheus"
 $dbs=@(
  (Join-Path $localPrometheus "memory.sqlite3"),
  (Join-Path $localPrometheus "resources\lookup-cache.sqlite3")
 )|Where-Object {Test-Path $_}
 if($dbs.Count -lt 1){Save "red" "No active Prometheus database was found for verification." "DO NOT EJECT." @("Chat closed") @("Expected local Prometheus database missing");exit 3}
 $python=Join-Path $root "Prometheus-Resources\Python\python.exe"
 foreach($db in $dbs){
  if(!(Test-Path $python)){Save "red" "Database verification could not run." "DO NOT EJECT." @("Chat closed") @("Portable Python runtime missing");exit 3}
  $verifyCode="import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); r=c.execute('PRAGMA integrity_check').fetchone()[0]; c.close(); raise SystemExit(0 if r=='ok' else 1)"
  & $python -c $verifyCode $db
  if($LASTEXITCODE -ne 0){Save "red" "Prometheus database verification failed." "DO NOT EJECT." @("Chat closed") @("SQLite integrity check failed: "+$db);exit 3}
 }
 Save "yellow" "Database verification passed." "Closing SSD-backed model server after verified chat shutdown." @("Zero active chat clients","SQLite integrity checks passed")|Out-Null
 Start-Sleep 1
 $targets=@(DriveProcesses)
 $owned=@($targets|Where-Object {
   $_.Name -match "^(ollama|llama-server)(\.exe)?$" -or
   ($_.Name -ieq "cmd.exe" -and $_.CommandLine -match "START-PROMETHEUS-SSD\.cmd")
 })
 $unexpected=@($targets|Where-Object {$owned.ProcessId -notcontains $_.ProcessId})
 if($unexpected.Count){Save "red" "Unexpected SSD-backed process remains." ("BLOCKED: "+(($unexpected|ForEach-Object {$_.Name+" PID "+$_.ProcessId}) -join ", ")) @("Chat closed","Database integrity verified") @("Refusing broad force termination");exit 2}
 foreach($proc in $owned){Stop-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue}
 Start-Sleep 2
 $remaining=@(DriveProcesses)
 if($remaining.Count){Save "red" "Prometheus shutdown is incomplete." ("BLOCKED: "+(($remaining|ForEach-Object {$_.Name+" PID "+$_.ProcessId}) -join ", ")) @("Chat closed","Database integrity verified") @("SSD-backed process survived shutdown");exit 2}
 Remove-Item $shutdownRequest -Force -ErrorAction SilentlyContinue
 Status
 exit 0
}
Status
