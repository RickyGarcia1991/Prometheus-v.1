param([switch]$NoStart)
$ErrorActionPreference='Stop'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$quiet=Join-Path $local 'Prometheus-QuietHost-v2.exe'
if(!(Test-Path -LiteralPath $quiet)){throw 'Verified quiet host is missing'}
$backup=Join-Path $local ('LifecycleBackups\tasks-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
[IO.Directory]::CreateDirectory($backup)|Out-Null
$modes=[ordered]@{
 'Desktop Commander Remote'='desktop-commander'
 'Prometheus Memory Backup'='backup'
 'Prometheus SSD Memory Backup'='ssd-backup'
 'Prometheus Recovery Evidence Recorder'='recorder'
 'Prometheus Recovery Watchdog'='watchdog'
 'Prometheus Recovery Watchdog At Logon'='watchdog'
 'Prometheus USB Reconnect Supervisor'='reconnect'
 'Prometheus Volume Watcher'='volume-watcher'
}
foreach($name in $modes.Keys){
 $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
 if(!$task){continue}
 Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath (Join-Path $backup ($name+'.xml')) -Encoding UTF8
 # Stop only our identified background monitors. Never stop Desktop Commander
 # or an in-progress memory backup during installation.
 if($modes[$name] -in @('reconnect','recorder','volume-watcher','watchdog')){Stop-ScheduledTask -TaskName $name -ErrorAction Stop}
 $task.Actions=@(New-ScheduledTaskAction -Execute $quiet -Argument $modes[$name] -WorkingDirectory $local)
 Set-ScheduledTask -InputObject $task | Out-Null
}
$supervisor=Get-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -ErrorAction SilentlyContinue
if(!$supervisor){
 $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
 $action=New-ScheduledTaskAction -Execute $quiet -Argument 'reconnect' -WorkingDirectory $local
 $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
 $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
 Register-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -Action $action -Trigger $trigger -Settings $settings -User $user | Out-Null
}
# The unified supervisor owns insertion/removal. Preserve the old definition for rollback.
if(Get-ScheduledTask -TaskName 'Prometheus Volume Watcher' -ErrorAction SilentlyContinue){Disable-ScheduledTask -TaskName 'Prometheus Volume Watcher'|Out-Null}
if(!$NoStart){
 Start-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor'
 if(Get-ScheduledTask -TaskName 'Prometheus Recovery Evidence Recorder' -ErrorAction SilentlyContinue){Start-ScheduledTask -TaskName 'Prometheus Recovery Evidence Recorder'}
}
Write-Output ('Console-free lifecycle tasks configured. Previous definitions: '+$backup)
