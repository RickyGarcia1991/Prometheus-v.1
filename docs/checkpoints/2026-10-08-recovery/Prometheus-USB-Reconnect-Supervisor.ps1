$ErrorActionPreference='Continue'
$dc=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup'
$pause=Join-Path $dc 'runner-paused.request'
$runner=Join-Path $dc 'Run-DesktopCommander.ps1'
$lock=Join-Path $env:LOCALAPPDATA 'Prometheus\eject-mode.json'
$intentional=Join-Path $env:LOCALAPPDATA 'Prometheus\controller-intentional-close.flag'
$log=Join-Path $env:LOCALAPPDATA 'Prometheus\Diagnostics\usb-supervisor.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Log([string]$message){"$(Get-Date -Format o) $message" | Add-Content -LiteralPath $log -Encoding UTF8}
$retryState=Join-Path $env:LOCALAPPDATA 'Prometheus\Diagnostics\controller-restart-state.json'
$maxAttempts=3
$retryWindowSeconds=300
$cooldownSeconds=300
function AllowControllerRestart {
 $now=Get-Date
 $state=@{attempts=@();blockedUntil=$null}
 if(Test-Path -LiteralPath $retryState){
  try {
   $saved=Get-Content -LiteralPath $retryState -Raw | ConvertFrom-Json
   $state.attempts=@($saved.attempts)
   $state.blockedUntil=$saved.blockedUntil
  } catch {Log 'Restart state unreadable; failing closed for cooldown.';return $false}
 }
 if($state.blockedUntil){
  try {if($now -lt [datetime]$state.blockedUntil){return $false}}catch{return $false}
  $state.attempts=@();$state.blockedUntil=$null
 }
 $recent=@($state.attempts | Where-Object {try{($now-[datetime]$_).TotalSeconds -lt $retryWindowSeconds}catch{$false}})
 if($recent.Count -ge $maxAttempts){
  $state.blockedUntil=$now.AddSeconds($cooldownSeconds).ToString('o')
  $state.attempts=$recent
  $state|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $retryState -Encoding UTF8
  Log 'Controller restart rate limit reached; cooldown 300 seconds.'
  return $false
 }
 $state.attempts=@($recent)+@($now.ToString('o'))
 $state|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $retryState -Encoding UTF8
 return $true
}
Log 'USB reconnect supervisor started on internal disk.'
while($true){
 try {
  if((Test-Path -LiteralPath $pause) -and !(Test-Path -LiteralPath $lock) -and ((Get-Date)-(Get-Item -LiteralPath $pause).LastWriteTime).TotalSeconds -gt 90){
   Remove-Item -LiteralPath $pause -Force -ErrorAction SilentlyContinue
   Log 'Recovered orphaned Desktop Commander pause request after 90 seconds.'
  }
  # Restart the controller only after the correct SSD has returned and the eject handoff is finished.
  if(!(Test-Path -LiteralPath $lock) -and !(Test-Path -LiteralPath $pause)){
   $ssd=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'"
   if($ssd -and $ssd.VolumeName -eq 'Prometheus-2TB'){
    $controller=Join-Path $env:LOCALAPPDATA 'Prometheus\Controllers\Prometheus-Controller-v0.5.5\Prometheus.DriveController.exe'
    $existing=@(Get-CimInstance Win32_Process -Filter "Name='Prometheus.DriveController.exe'")
    if(!$existing.Count -and !(Test-Path -LiteralPath $intentional) -and (Test-Path -LiteralPath $controller) -and (AllowControllerRestart)){
     Start-Process -FilePath $controller -WorkingDirectory (Split-Path $controller)
     Log 'Controller auto-restart requested after verified SSD presence.'
    }
   }
  }
  if(Test-Path -LiteralPath $lock){
   $state=Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
   $drive=[string]$state.drive
   if($drive -match '^[A-Za-z]:$'){
    $volume=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$drive+"'")
    $originalPresent=($volume -and $volume.VolumeName -eq 'Prometheus-2TB')
    $age=if($state.started){((Get-Date)-([datetime]$state.started)).TotalSeconds}else{0}
    if((!$originalPresent -and (Test-Path -LiteralPath $pause)) -or ($age -gt 90 -and (Test-Path -LiteralPath $pause))){
     Remove-Item -LiteralPath $pause -Force -ErrorAction SilentlyContinue
     Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
     $runnerActive=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*Run-DesktopCommander.ps1*' -and $_.ProcessId -ne $PID})
     if(!$runnerActive.Count -and (Test-Path -LiteralPath $runner)){
      Start-Process powershell.exe -ArgumentList @('-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',('"'+$runner+'"')) -WindowStyle Hidden
     }
     Log ('Desktop Commander recovery requested for '+$drive+'; volumePresent='+$originalPresent+'; handoffAgeSeconds='+[int]$age)
    }
   }
  }
 }catch{Log ('Supervisor error: '+$_.Exception.Message)}
 Start-Sleep -Seconds 2
}