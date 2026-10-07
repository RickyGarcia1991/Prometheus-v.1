$ErrorActionPreference='SilentlyContinue'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus';$state=Join-Path $local 'usb-supervisor.json';$eject=Join-Path $local 'eject-mode.json';$pause=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\runner-paused.request';$dcRunner=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\Run-DesktopCommander.ps1';$log=Join-Path $local 'Diagnostics\usb-supervisor.log'
New-Item -ItemType Directory -Force (Split-Path $log)|Out-Null
function UsbVolumes {@(Get-CimInstance Win32_LogicalDisk|Where-Object {$_.DeviceID -ne $env:SystemDrive -and $_.DriveType -in 2,3})}
function DCAlive {@(Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -match 'DesktopCommanderStartup|desktop-commander'}).Count -gt 0}
function StartDC {if(!(DCAlive) -and (Test-Path $dcRunner)){Start-Process powershell.exe -ArgumentList '-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$dcRunner -WindowStyle Hidden}}
function RestartController {
 # Preserve the successful Windows-release confirmation in a live controller.
 if(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue){return}
 $controllers=Join-Path $local 'Controllers'
 $exe=Get-ChildItem $controllers -Filter 'Prometheus.DriveController.exe' -File -Recurse -ErrorAction SilentlyContinue|Where-Object {$_.Directory.Name -match '^Prometheus-Controller-v[0-9.]+$'}|Sort-Object FullName -Descending|Select-Object -First 1
 if($exe){Start-Process $exe.FullName -WorkingDirectory $exe.DirectoryName}
}
while($true){
 $vols=UsbVolumes;$prom=@($vols|Where-Object {$_.VolumeName -eq 'Prometheus-2TB'});$flash=@($vols|Where-Object {$_.VolumeName -ne 'Prometheus-2TB'})
 $mode=if($prom.Count){'prometheus'}elseif($flash.Count){'flash'}else{'none'}
 @{updated=(Get-Date).ToString('o');mode=$mode;volumes=@($vols|ForEach-Object {$_.DeviceID+' '+$_.VolumeName})}|ConvertTo-Json|Set-Content -Encoding UTF8 $state
 if(Test-Path $eject){
  try{$ej=Get-Content $eject -Raw|ConvertFrom-Json;$ed=[string]$ej.drive}catch{$ed=''}
  $gone=$true;if($ed){$gone=!(@($vols|Where-Object {$_.DeviceID -eq $ed.TrimEnd('\')}).Count)}
  if($gone){Remove-Item $pause -Force -ErrorAction SilentlyContinue;Remove-Item $eject -Force -ErrorAction SilentlyContinue;StartDC;RestartController;"$(Get-Date -Format o) Ejected volume disappeared; Commander and controller recovery requested."|Add-Content -Encoding UTF8 $log}
 } elseif(Test-Path $pause) {
  Remove-Item $pause -Force -ErrorAction SilentlyContinue;StartDC
 } elseif(!(DCAlive)) {StartDC}
 Start-Sleep 2
}
