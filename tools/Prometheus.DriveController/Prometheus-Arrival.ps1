param([ValidatePattern('^[A-Za-z]:$')][string]$Drive='D:')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Prometheus-Lifecycle.ps1')
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$result=Join-Path $local 'arrival-status.json'
$held=$false;$mutex=[Threading.Mutex]::new($false,'Local\PrometheusArrival')
try {
 $held=$mutex.WaitOne(0);if(!$held){exit 0}
 if(Get-EjectState){exit 0}
 $volume=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$Drive'" -ErrorAction Stop
 if(!$volume -or $volume.VolumeName -ne 'Prometheus-2TB'){throw 'Expected SSD is unavailable'}
 Write-LifecycleJson $result @{phase='starting';drive=$Drive;updated=(Get-Date).ToString('o')}
 # The signed host agent selects and verifies the latest controller; no hard-coded old release.
 $agent=Start-LifecycleScript (Join-Path $local 'Prometheus-Host-Agent.ps1') @('-Mode','Run')
 if(!$agent.WaitForExit(45000)){throw 'Controller verification timed out'}
 if($agent.ExitCode -ne 0){throw 'Signed controller verification failed'}
 $agent.Dispose()
 if(Get-EjectState){exit 0}
 $launcher=Join-Path ($Drive+'\') 'Prometheus-Resources\Tools\Start-Prometheus-Interface.ps1'
 $launch=Start-LifecycleScript $launcher @('-NoBrowser')
 if(!$launch.WaitForExit(115000)){throw 'Interface startup timed out'}
 if($launch.ExitCode -ne 0){throw 'Interface startup failed; see Interface logs on the SSD'}
 $launch.Dispose()
 if(Get-EjectState){exit 0}
 $url='http://127.0.0.1:54555'
 $bootstrap=Invoke-RestMethod ($url+'/api/bootstrap') -TimeoutSec 5
 $status=Invoke-RestMethod ($url+'/api/status') -Headers @{'X-Prometheus-Token'=$bootstrap.token} -TimeoutSec 5
 if($status.closing -or !$status.saved_locally -or $null -eq $status.hardware){throw 'Interface readiness was not confirmed'}
 if(Get-EjectState){exit 0}
 $greeting=if($status.hardware.chat_ready){'Welcome back. Prometheus is open, and your saved memory is ready.'}else{'Welcome back. Prometheus is open in reference mode, and your saved memory is ready.'}
 Start-Process ($url+'/')
 $ready=@{phase='ready';drive=$Drive;greeting=$greeting;chat_ready=[bool]$status.hardware.chat_ready;updated=(Get-Date).ToString('o');audio='requested'}
 Write-LifecycleJson $result $ready
 try {
  Add-Type -AssemblyName System.Speech
  $voice=[System.Speech.Synthesis.SpeechSynthesizer]::new()
  try {$voice.SetOutputToDefaultAudioDevice();if(!(Get-EjectState)){$voice.Speak($greeting);$ready.audio='completed'}else{$ready.audio='skipped-during-eject'}}finally{$voice.Dispose()}
 }catch{$ready.audio='unavailable'}
 Write-LifecycleJson $result $ready
}catch{Write-LifecycleJson $result @{phase='blocked';drive=$Drive;message=$_.Exception.Message;updated=(Get-Date).ToString('o')};exit 2}
finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
