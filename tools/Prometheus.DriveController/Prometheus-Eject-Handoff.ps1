param([string]$Drive='D:')
$ErrorActionPreference='SilentlyContinue'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus';$diag=Join-Path $local 'Diagnostics';$lock=Join-Path $local 'eject-mode.json';$log=Join-Path $diag 'eject-history.jsonl'
New-Item -ItemType Directory -Force $diag|Out-Null
function Record($phase,$message,$processes=@()){ $o=[ordered]@{timestamp=(Get-Date).ToString('o');drive=$Drive;phase=$phase;message=$message;processes=@($processes)};($o|ConvertTo-Json -Compress -Depth 4)|Add-Content -Encoding UTF8 $log }
@{active=$true;drive=$Drive;started=(Get-Date).ToString('o');phase='handoff'}|ConvertTo-Json|Set-Content -Encoding UTF8 $lock
$drivePrefix=$Drive.TrimEnd('\')+'\'
$targets=@(Get-CimInstance Win32_Process|Where-Object {
 $_.ProcessId -ne $PID -and ($_.Name -eq 'Prometheus.DriveController.exe' -or
 ($_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'Prometheus-Drive-Watcher\.ps1') -or
 ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($drivePrefix,[StringComparison]::OrdinalIgnoreCase)) -or
 ($_.CommandLine -and $_.CommandLine.Contains($drivePrefix)))
})
Record 'quiescing' 'Stopping every process executing from or explicitly referencing the removable drive.' ($targets|ForEach-Object {$_.Name+' PID '+$_.ProcessId})
$targets|ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Start-Sleep 3
$remaining=@(Get-CimInstance Win32_Process|Where-Object {($_.ExecutablePath -and $_.ExecutablePath.StartsWith($drivePrefix,[StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.Contains($drivePrefix))})
if($remaining.Count){Record 'blocked' 'SSD-backed processes remain; refusing remote disconnect.' ($remaining|ForEach-Object {$_.Name+' PID '+$_.ProcessId});exit 2}
$pause=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\runner-paused.request';Set-Content -Encoding ASCII $pause 'Paused for removable-drive eject'
$dc=@(Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -match 'DesktopCommanderStartup|desktop-commander'})
Record 'disconnecting' 'Drive is quiescent; Desktop Commander stopping last. Windows host supervisor will restore it after volume removal.' ($dc|ForEach-Object {$_.Name+' PID '+$_.ProcessId})
$dc|Sort-Object {if($_.Name -eq 'node.exe'){0}else{1}}|ForEach-Object {if($_.ProcessId -ne $PID){Stop-Process -Id $_.ProcessId -Force}}
