param([string]$Root=(Split-Path $PSScriptRoot -Parent),[ValidateSet('Install','Uninstall','Status')][string]$Mode='Status')
$ErrorActionPreference='Stop'
$task='Prometheus SSD Migration Auto-Check'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus\MigrationAutoCheck'
$watcher=Join-Path $local 'portable-auto-check-watcher.ps1'
if($Mode -eq 'Status'){
 $found=Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
 Write-Output ('Installed: '+[bool]$found)
 exit 0
}
if($Mode -eq 'Uninstall'){
 Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
 Write-Output 'Auto-check disabled on this Windows laptop.'
 exit 0
}
$source=Join-Path $Root 'Prometheus-Resources\Tools\portable-auto-check-watcher.ps1'
if(!(Test-Path $source)){throw 'Auto-check watcher missing from SSD'}
New-Item -ItemType Directory -Path $local -Force | Out-Null
Copy-Item $source $watcher -Force
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$watcher+'"')
$trigger=New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Description 'Run SSD migration diagnostics once per Prometheus insertion while logged in.' -Force | Out-Null
Write-Output 'Auto-check registered for this Windows user. It starts at next logon; no service or administrator permission requested.'
