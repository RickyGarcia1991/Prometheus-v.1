param([ValidatePattern('^[A-Za-z]:$')][string]$Drive='D:',[ValidateSet('status','start','stop')][string]$Action='status')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Prometheus-Controller-Common.ps1')
. (Join-Path $PSScriptRoot 'Prometheus-Lifecycle.ps1')
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$stateDir=Join-Path $env:LOCALAPPDATA 'RemovableMediaWorkStatus'
$checks=@()
function Save($phase,$message,$activity,$errors=@()) {
 $state=[ordered]@{phase=$phase;message=$message;activity=$activity;checks=@($script:checks);errors=@($errors);updated=(Get-Date).ToString('o')}
 New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
 $json=$state | ConvertTo-Json -Depth 5
 $json | Set-Content -LiteralPath (Join-Path $stateDir ('state-'+$Drive[0]+'.json')) -Encoding UTF8
 $json
}
try {
 $root=Get-PrometheusRoot $Drive
 New-Item -ItemType Directory -Force -Path $local | Out-Null
 $targets=@(Get-DriveProcesses $Drive)
 if($Action -eq 'start'){
  $lock=Join-Path $local 'eject-mode.json'
  if(Test-Path -LiteralPath $lock){Resume-Lifecycle (Get-EjectState) 'explicit-user-start'}
  $launcher=Join-Path $root 'START-PROMETHEUS-SSD.cmd'
  if(!(Test-Path -LiteralPath $launcher)){throw 'Portable launcher is missing'}
  if(!@($targets | Where-Object {Test-PrometheusChat $_}).Count){
   $child=Start-LifecycleScript (Join-Path $root 'Prometheus-Resources\Tools\Start-Prometheus-Interface.ps1');$child.Dispose()
  }
  $deadline=(Get-Date).AddSeconds(20)
  do {
   $chat=@(Get-DriveProcesses $Drive | Where-Object {Test-PrometheusChat $_})
   if($chat.Count){Save 'yellow' 'Prometheus started; model readiness is still being checked.' 'Chat process detected.';exit 0}
   Start-Sleep -Milliseconds 500
  }while((Get-Date) -lt $deadline)
  throw 'No Prometheus chat process appeared before the startup deadline'
 }
 if($Action -eq 'stop'){
  $request=Join-Path $local 'active-chat.shutdown'
  Set-Content -LiteralPath $request -Value (Get-Date).ToString('o') -Encoding ASCII
  $checks += 'Request graceful chat shutdown'
  Stop-PrometheusStudios $Drive
  $deadline=(Get-Date).AddSeconds(130)
  do {
   $chat=@(Get-DriveProcesses $Drive | Where-Object {(Test-PrometheusChat $_) -or (Test-PrometheusStudio $_ $Drive)})
   if(!$chat.Count){break}
   Start-Sleep -Milliseconds 250
  }while((Get-Date) -lt $deadline)
  if($chat.Count){throw 'Chat is still finishing a request. No chat process was forcibly terminated.'}
  $checks += 'Confirm zero active chat clients'
  Test-PortableMemory $root
  $checks += 'Verify SQLite integrity'
  $models=@(Get-DriveProcesses $Drive | Where-Object {Test-PrometheusModel $_ $Drive})
  # Only the known inference runtime, after chat exit and database verification.
  foreach($p in $models){if(Test-ProcessIdentity $p){Stop-Process -Id $p.ProcessId -ErrorAction Stop}}
  $deadline=(Get-Date).AddSeconds(10)
  do {
   $remaining=@(Get-DriveProcesses $Drive | Where-Object {(Test-PrometheusModel $_ $Drive) -or (Test-PrometheusChat $_)})
   if(!$remaining.Count){break}
   Start-Sleep -Milliseconds 250
  }while((Get-Date) -lt $deadline)
  if($remaining.Count){throw 'Prometheus runtime has not released the drive'}
  $checks += 'Stop Ollama and model workers'
  # Remaining drive references are verified below, including companion applications.
  Remove-Item -LiteralPath $request -ErrorAction SilentlyContinue
  $others=@(Get-DriveProcesses $Drive)
  if($others.Count){throw ('Drive references remain: '+(($others|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))}
  $checks += 'Confirm no Prometheus process references SSD'
  Save 'green' 'Prometheus stopped; Windows has not released the drive.' ('Other drive references: '+(($others|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))
  exit 0
 }
 if($targets.Count){Save 'yellow' 'Drive-related programs are active.' (($targets|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', ')}
 else{Save 'green' 'No drive-backed program was detected. Windows removal is not yet confirmed.' 'Use Safely Eject to request removal.'}
 exit 0
}catch{
 Save 'red' 'Controller check could not complete.' $_.Exception.Message @($_.Exception.Message)
 exit 2
}
