$ErrorActionPreference='SilentlyContinue'
$dc=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup';$pause=Join-Path $dc 'runner-paused.request';$runner=Join-Path $dc 'Run-DesktopCommander.ps1';$local=Join-Path $env:LOCALAPPDATA 'Prometheus';$lock=Join-Path $local 'eject-mode.json';$log=Join-Path $local 'Diagnostics\usb-supervisor.log'
New-Item -ItemType Directory -Force (Split-Path $log)|Out-Null
function Log($m){"$(Get-Date -Format o) $m"|Add-Content -Encoding UTF8 $log}
function DCAlive {@(Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -match 'DesktopCommanderStartup|desktop-commander'}).Count -gt 0}
Log 'USB reconnect supervisor started.'
while($true){
 if(Test-Path $lock){
  try{$state=Get-Content $lock -Raw|ConvertFrom-Json;$drive=[string]$state.drive}catch{$drive='D:'}
  $present=$false
  if($drive){$present=Test-Path ($drive.TrimEnd('\')+'\')}
  if(!$present){
   Remove-Item $pause -Force -ErrorAction SilentlyContinue
   Remove-Item $lock -Force -ErrorAction SilentlyContinue
   if(!(DCAlive) -and (Test-Path $runner)){Start-Process powershell.exe -ArgumentList '-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$runner -WindowStyle Hidden;Log 'Removable volume disappeared; Desktop Commander restart requested.'}
  }
 }
 Start-Sleep 2
}
