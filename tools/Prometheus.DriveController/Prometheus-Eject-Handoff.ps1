param([string]$Drive='D:')
$ErrorActionPreference='SilentlyContinue'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$diag=Join-Path $local 'Diagnostics'
$lock=Join-Path $local 'eject-mode.json'
$log=Join-Path $diag 'eject-history.jsonl'
New-Item -ItemType Directory -Force $diag|Out-Null
function Record($phase,$message,$processes=@()){
 $o=[ordered]@{timestamp=(Get-Date).ToString('o');drive=$Drive;phase=$phase;message=$message;processes=@($processes)}
 ($o|ConvertTo-Json -Compress -Depth 4)|Add-Content -Encoding UTF8 $log
 $o|ConvertTo-Json -Depth 4
}
@{active=$true;drive=$Drive;started=(Get-Date).ToString('o')}|ConvertTo-Json|Set-Content -Encoding UTF8 $lock
Record 'handoff' 'Eject mode locked; stopping host-side Prometheus polling.' | Out-Null
Get-CimInstance Win32_Process|Where-Object {
 $_.Name -eq 'Prometheus.DriveController.exe' -or
 ($_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'Prometheus-Drive-Watcher\.ps1')
}|ForEach-Object {if($_.ProcessId -ne $PID){Stop-Process -Id $_.ProcessId -Force}}
Start-Sleep 2
$dc=@(Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -match 'DesktopCommanderStartup|desktop-commander'})
Record 'disconnecting' 'Prometheus is quiescent. Desktop Commander will stop last; use Windows Safely Remove after disconnect.' ($dc|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) | Out-Null
$dc|Sort-Object {if($_.Name -eq 'node.exe'){0}else{1}}|ForEach-Object {if($_.ProcessId -ne $PID){Stop-Process -Id $_.ProcessId -Force}}
