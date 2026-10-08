param([string]$Drive='D:',[switch]$InspectOnly)
$ErrorActionPreference='Stop'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$diag=Join-Path $local 'Diagnostics'
$pause=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\runner-paused.request'
$lock=Join-Path $local 'eject-mode.json'
$runner=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\Run-DesktopCommander.ps1'
$helper=Join-Path $local 'Prometheus-Safe-Eject.ps1'
$log=Join-Path $diag 'eject-orchestrator.log'
$ownsHandoff=$false
New-Item -ItemType Directory -Force $diag | Out-Null
function Log($s){"$(Get-Date -Format o) $s" | Add-Content -LiteralPath $log}
function Present { $v=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$Drive'";return ($v -and $v.VolumeName -eq 'Prometheus-2TB') }
function Resume {
 Remove-Item -LiteralPath $pause -Force -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
 $running=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*Run-DesktopCommander.ps1*' -and $_.ProcessId -ne $PID})
 if(!$running.Count -and (Test-Path $runner)){Start-Process powershell.exe -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',('"'+$runner+'"')) -WindowStyle Hidden}
 Log 'Desktop Commander resume requested'
}
try {
 if(!(Present)){throw 'SSD identity mismatch; no eject attempted'}
 if(!(Test-Path $helper)){throw 'Windows safe-eject helper missing'}
 $inspection=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive -InspectOnly 2>&1
 if($LASTEXITCODE -ne 0){throw ('USB identity verification failed: '+($inspection -join ' '))}
 Log ('Identity verified: '+($inspection -join ' '))
 if($InspectOnly){Write-Output 'PASS: target SSD verified; no processes stopped and no eject requested';exit 0}
 $supervisor=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'powershell.exe' -and $_.CommandLine -match '[P]rometheus-USB-Reconnect-Supervisor.ps1'})
 if(!$supervisor.Count){throw 'Reconnect supervisor not running; refusing to interrupt remote access'}
 if(Test-Path $lock){$prior=Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json;if($prior.phase -ne 'stopping' -or $prior.drive -ne $Drive){throw 'Another operation owns the eject lock'}}
 @{active=$true;drive=$Drive;started=(Get-Date).ToString('o');phase='handoff'} | ConvertTo-Json | Set-Content -LiteralPath $lock -Encoding UTF8
 $ownsHandoff=$true
 Start-Sleep -Seconds 2
 $targets=@(Get-CimInstance Win32_Process | Where-Object {
   $_.ProcessId -ne $PID -and ($_.Name -eq 'Prometheus.DriveController.exe' -or
   ($_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'Prometheus-Drive-Watcher\.ps1') -or
   ($_.ExecutablePath -and $_.ExecutablePath.StartsWith(($Drive+'\'),[StringComparison]::OrdinalIgnoreCase)) -or
   ($_.CommandLine -and $_.CommandLine.Contains(($Drive+'\'))))
 })
 foreach($p in $targets){
  Log ('Closing blocker '+$p.Name+' PID '+$p.ProcessId)
  if($p.Name -eq 'Prometheus.DriveController.exe'){
   try {
    $controllerProcess=Get-Process -Id $p.ProcessId -ErrorAction Stop
    if($controllerProcess.MainWindowHandle -ne [IntPtr]::Zero){[void]$controllerProcess.CloseMainWindow()}
    if(!$controllerProcess.WaitForExit(5000)){
     Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
     Start-Sleep -Milliseconds 500
    }
   }catch{Log ('Controller shutdown error: '+$_.Exception.Message)}
   if(Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue){throw ('Controller PID '+$p.ProcessId+' did not exit; aborting handoff safely')}
  }else{Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue}
 }
 Start-Sleep -Seconds 2
 $remaining=@(Get-CimInstance Win32_Process | Where-Object {$_.ProcessId -ne $PID -and (($_.ExecutablePath -and $_.ExecutablePath.StartsWith(($Drive+'\'),[StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.Contains(($Drive+'\'))) )})
 if($remaining.Count){throw ('SSD process still active: '+(($remaining|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))}
 Set-Content -LiteralPath $pause -Value 'Paused for Windows eject' -Encoding ASCII
 $dc=@(Get-CimInstance Win32_Process | Where-Object {$_.ProcessId -ne $PID -and $_.Name -eq 'node.exe' -and $_.CommandLine -like '*desktop-commander*index.js*'})
 Log ('Identified '+$dc.Count+' Desktop Commander node processes for temporary shutdown')
 foreach($p in $dc){if($p.Name -eq 'node.exe'){Log ('Pausing Desktop Commander '+$p.ProcessId);Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue}}
 Start-Sleep -Seconds 3
 $result=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive 2>&1
 $ejectExit=$LASTEXITCODE
 Log ('Windows eject result: '+($result -join ' ')+' exit '+$ejectExit)
 if($ejectExit -eq 0 -and (($result -join ' ') -like '*Windows confirmed volume removal; safe to unplug.*') -and !(Present)){Log 'SUCCESS: Windows explicitly accepted removal and Prometheus volume disappeared';Resume;exit 0}
 throw ('Windows did not release SSD: '+($result -join ' '))
}catch{Log ('BLOCKED: '+$_.Exception.Message);if($ownsHandoff){Resume};exit 2}
