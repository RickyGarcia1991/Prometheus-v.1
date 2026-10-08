$ErrorActionPreference='Stop'
$seen=@{}
while($true){
 $ejectLock=Join-Path (Join-Path $env:LOCALAPPDATA 'Prometheus') 'eject-mode.json'
 if(Test-Path $ejectLock){
  $mounted=@(Get-CimInstance Win32_LogicalDisk | Where-Object {$_.VolumeName -eq 'Prometheus-2TB'})
  if(!$mounted.Count){$seen.Clear();Start-Sleep 3;continue}
  # Keep eject mode until volume removal or an explicit Start request.
  Start-Sleep 3;continue
 }
 foreach($d in @(Get-CimInstance Win32_LogicalDisk | Where-Object {$_.VolumeName -eq 'Prometheus-2TB'})){
  # Once a host-cached controller is alive, do not poll the removable volume.
  # This prevents the watcher itself from racing Windows Safely Remove.
  if($seen.ContainsKey($d.DeviceID)){
   $controller=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'Prometheus.DriveController.exe'})
   if($controller.Count){continue}
   $seen.Remove($d.DeviceID)
  }
  $root=$d.DeviceID+'\'
  $status=Join-Path $root 'PROMETHEUS-SSD-STATUS.json'
  try {
   $meta=Get-Content $status -Raw | ConvertFrom-Json
   $releaseName=Split-Path ([string]$meta.controller_release) -Leaf
   if($releaseName -notmatch '^Prometheus-Controller-v[0-9.]+$'){throw 'Invalid controller release'}
   $source=Join-Path $root $releaseName
   $hostDir=Join-Path $env:LOCALAPPDATA ('Prometheus\Controllers\'+$releaseName)
   $exe=Join-Path $hostDir 'Prometheus.DriveController.exe'
   if(!$seen.ContainsKey($d.DeviceID)){
    $already=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'Prometheus.DriveController.exe'})
    if(!$already.Count){
     New-Item -ItemType Directory -Force $hostDir | Out-Null
     foreach($file in Get-ChildItem $source -File){
      $dest=Join-Path $hostDir $file.Name
      Copy-Item $file.FullName $dest -Force
      if((Get-FileHash $file.FullName).Hash -ne (Get-FileHash $dest).Hash){throw 'Controller copy verification failed'}
     }
     Start-Process $exe -WorkingDirectory $hostDir -WindowStyle Hidden
    }
    $seen[$d.DeviceID]=$true
   }
  } catch {Write-Warning $_}
 }
 foreach($key in @($seen.Keys)){if(!(@(Get-CimInstance Win32_LogicalDisk | Where-Object {$_.DeviceID -eq $key}).Count)){$seen.Remove($key)}}
 Start-Sleep 3
}
