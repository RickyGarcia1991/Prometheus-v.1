param([string]$Drive='D:')
$ErrorActionPreference='SilentlyContinue';$local=Join-Path $env:LOCALAPPDATA 'Prometheus';$diag=Join-Path $local 'Diagnostics';$lock=Join-Path $local 'eject-mode.json';$pause=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\runner-paused.request';New-Item -ItemType Directory -Force $diag|Out-Null
@{active=$true;drive=$Drive;started=(Get-Date).ToString('o');phase='portable-eject'}|ConvertTo-Json|Set-Content -Encoding UTF8 $lock;$prefix=$Drive.TrimEnd('\')+'\'
$targets=@(Get-CimInstance Win32_Process|Where-Object {$_.ProcessId -ne $PID -and (($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.Contains($prefix)))})
$targets|ForEach-Object {Stop-Process -Id $_.ProcessId -Force};Start-Sleep 3
$remaining=@(Get-CimInstance Win32_Process|Where-Object {($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.Contains($prefix))})
if($remaining.Count){Remove-Item $lock -Force;exit 2}
Set-Content -Encoding ASCII $pause 'Paused for removable-drive eject'
$dc=@(Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -match 'DesktopCommanderStartup|desktop-commander'})
$dc|Sort-Object {if($_.Name -eq 'node.exe'){0}else{1}}|ForEach-Object {if($_.ProcessId -ne $PID){Stop-Process -Id $_.ProcessId -Force}}
