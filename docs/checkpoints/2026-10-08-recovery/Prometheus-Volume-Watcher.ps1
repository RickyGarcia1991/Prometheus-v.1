$ErrorActionPreference='Stop'
$HostRoot=Join-Path $env:LOCALAPPDATA 'Prometheus'
$Agent=Join-Path $HostRoot 'Prometheus-Host-Agent.ps1'
$EjectLock=Join-Path $HostRoot 'eject-mode.json'
$Log=Join-Path $HostRoot 'volume-watcher.log'
function Log($m){Add-Content $Log ((Get-Date -Format o)+' '+$m)}
function IsPrometheus($drive){if(!$drive){return $false};try{$d=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$drive.TrimEnd('\\')+"'");return ($d.VolumeName -eq 'Prometheus-2TB' -and $d.DriveType -eq 3)}catch{return $false}}
function RunAgent {if(Test-Path (Join-Path $HostRoot 'controller-intentional-close.flag')){Log 'arrival-suppressed-intentional-close';return};if(Test-Path $EjectLock){Log 'arrival-suppressed-eject-lock';return};Start-Process powershell.exe -ArgumentList '-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',('"'+$Agent+'"'),'-Mode','Run' -WindowStyle Hidden}
# Clear only locks from a previous boot. A live-session eject lock is cleared after a real removal/arrival cycle.
if(Test-Path $EjectLock){try{$l=Get-Content $EjectLock -Raw|ConvertFrom-Json;$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime;if([datetime]$l.started -lt $boot){Remove-Item $EjectLock -Force;Log 'cleared-stale-eject-lock-after-reboot'}}catch{}}
$present=@(Get-CimInstance Win32_LogicalDisk|? {$_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -eq 3}).Count -gt 0
Log ('watcher-started present='+$present)
if($present -and !(Test-Path $EjectLock)){RunAgent}
$sub=Register-WmiEvent -Query "SELECT * FROM Win32_VolumeChangeEvent WHERE EventType = 2 OR EventType = 3" -SourceIdentifier 'PrometheusVolumeChange'
try{
 while($true){$ev=Wait-Event -SourceIdentifier 'PrometheusVolumeChange' -Timeout 60;if(!$ev){continue};$drive=[string]$ev.SourceEventArgs.NewEvent.DriveName;$type=[int]$ev.SourceEventArgs.NewEvent.EventType;Remove-Event -EventIdentifier $ev.EventIdentifier -ErrorAction SilentlyContinue
  if($type -eq 3){$presentNow=@(Get-CimInstance Win32_LogicalDisk|? {$_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -eq 3}).Count -gt 0;if(!$presentNow -and $present){$present=$false;Log 'prometheus-volume-removed'}}
  elseif($type -eq 2 -and (IsPrometheus $drive)){$present=$true;if(Test-Path $EjectLock){Remove-Item $EjectLock -Force -ErrorAction SilentlyContinue;Log 'cleared-eject-lock-on-new-arrival'};RunAgent;Log ('prometheus-volume-arrived '+$drive)}
 }
}finally{Unregister-Event -SourceIdentifier 'PrometheusVolumeChange' -ErrorAction SilentlyContinue}
