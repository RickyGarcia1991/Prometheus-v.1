$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Prometheus-Lifecycle.ps1')
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$log=Join-Path $local 'Diagnostics\usb-supervisor.log'
[IO.Directory]::CreateDirectory((Split-Path $log))|Out-Null
function Log([string]$Message){Add-Content -LiteralPath $log -Value ((Get-Date -Format o)+' '+$Message)}
$mutex=[Threading.Mutex]::new($false,'Local\PrometheusUsbLifecycle')
$held=$false;$subscription=$null;$present=@{};$scan=$true
try {
 try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
 if(!$held){exit 0}
 $subscription=Register-WmiEvent -Query 'SELECT * FROM Win32_DeviceChangeEvent WHERE EventType = 2 OR EventType = 3' -SourceIdentifier 'PrometheusUsbLifecycle'
 Log 'Lifecycle supervisor started on internal disk; one arrival launch, physical-disconnect eject latch.'
 while($true){
  try {
   $state=Get-EjectState
   if($state){
    # Device metadata only: no SSD reads, readiness Python, or age-based pause expiry.
    $physical=Get-PhysicalUsbPresence ([string]$state.usb_instance)
    $volume=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$state.drive+"'") -ErrorAction Stop
    if((Get-EjectDecision $state $physical ([bool]$volume)) -eq 'disconnected'){
     Resume-Lifecycle $state 'physical-usb-disconnection'
     Log 'Physical SSD disconnection confirmed; Desktop Commander resumed.'
     $present=@{};$scan=$true
    }
   }elseif($scan){
    $now=@{}
    foreach($volume in @(Get-CimInstance Win32_LogicalDisk -Filter "VolumeName='Prometheus-2TB'" -ErrorAction Stop)){
     $drive=[string]$volume.DeviceID;$now[$drive]=$true
     if(!$present.ContainsKey($drive)){
      Remove-Item -LiteralPath (Join-Path $local 'controller-intentional-close.flag') -ErrorAction SilentlyContinue
      $arrival=Start-LifecycleScript (Join-Path $local 'Prometheus-Arrival.ps1') @('-Drive',$drive)
      Log ('One startup requested for '+$drive+'; PID '+$arrival.Id);$arrival.Dispose()
     }
    }
    $present=$now;$scan=$false
   }
  }catch{Log ('Lifecycle check held safely: '+$_.Exception.Message)}
  $event=Wait-Event -SourceIdentifier 'PrometheusUsbLifecycle' -Timeout 5
  if($event){Remove-Event -EventIdentifier $event.EventIdentifier;$scan=$true}
 }
}finally{
 if($subscription){Unregister-Event -SourceIdentifier 'PrometheusUsbLifecycle' -ErrorAction SilentlyContinue}
 if($held){$mutex.ReleaseMutex()};$mutex.Dispose()
}
