$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'Prometheus\Diagnostics'
$log=Join-Path $root 'task-scheduler-diagnostics-20261008.log'
function Log([string]$s){"$(Get-Date -Format o) $s"|Add-Content -LiteralPath $log;Write-Output $s}
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "Elevated=$admin"
if(!$admin){throw 'Administrator approval required'}
$before=Get-WinEvent -ListLog 'Microsoft-Windows-TaskScheduler/Operational'
Log "BeforeEnabled=$($before.IsEnabled)"
wevtutil.exe sl Microsoft-Windows-TaskScheduler/Operational /e:true
if($LASTEXITCODE -ne 0){throw "wevtutil failed: $LASTEXITCODE"}
$after=Get-WinEvent -ListLog 'Microsoft-Windows-TaskScheduler/Operational'
Log "AfterEnabled=$($after.IsEnabled)"
if(!$after.IsEnabled){throw 'Event log not enabled'}
Log 'PASS: Task Scheduler operational diagnostics enabled'
