$ErrorActionPreference='SilentlyContinue'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus';$state=Join-Path $local 'usb-supervisor.json';$pause=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\runner-paused.request';$dcRunner=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\Run-DesktopCommander.ps1'
New-Item -ItemType Directory -Force $local|Out-Null
function UsbVolumes {@(Get-CimInstance Win32_LogicalDisk|Where-Object {$_.DriveType -eq 2})}
while($true){
 $vols=UsbVolumes;$prom=@($vols|Where-Object {$_.VolumeName -eq 'Prometheus-2TB'});$flash=@($vols|Where-Object {$_.VolumeName -ne 'Prometheus-2TB'})
 $mode=if($prom.Count){'prometheus'}elseif($flash.Count){'flash'}else{'none'}
 @{updated=(Get-Date).ToString('o');mode=$mode;volumes=@($vols|ForEach-Object {$_.DeviceID+' '+$_.VolumeName})}|ConvertTo-Json|Set-Content -Encoding UTF8 $state
 if($mode -ne 'prometheus' -and (Test-Path $pause)){
  Remove-Item $pause -Force
  $running=@(Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -match 'DesktopCommanderStartup.*Run-DesktopCommander\.ps1'})
  if(!$running.Count -and (Test-Path $dcRunner)){Start-Process powershell.exe -ArgumentList '-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$dcRunner -WindowStyle Hidden}
 }
 Start-Sleep 3
}
