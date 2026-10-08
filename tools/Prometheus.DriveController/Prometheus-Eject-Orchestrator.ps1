param([string]$Drive='D:',[switch]$InspectOnly)
$ErrorActionPreference='Stop'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$diag=Join-Path $local 'Diagnostics'
$pause=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\runner-paused.request'
$lock=Join-Path $local 'eject-mode.json'
$runner=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup\Run-DesktopCommander.ps1'
$helper=Join-Path $local 'Prometheus-Safe-Eject.ps1'
$log=Join-Path $diag 'eject-orchestrator.log'
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
 if(Test-Path $lock){throw 'Another eject operation is already active'}
 @{active=$true;drive=$Drive;started=(Get-Date).ToString('o');phase='handoff'} | ConvertTo-Json | Set-Content -LiteralPath $lock -Encoding UTF8
 Start-Sleep -Seconds 2
 $targets=@(Get-CimInstance Win32_Process | Where-Object {
   $_.ProcessId -ne $PID -and ($_.Name -eq 'Prometheus.DriveController.exe' -or
   ($_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'Prometheus-Drive-Watcher\.ps1') -or
   ($_.ExecutablePath -and $_.ExecutablePath.StartsWith(($Drive+'\'),[StringComparison]::OrdinalIgnoreCase)) -or
   ($_.CommandLine -and $_.CommandLine.Contains(($Drive+'\'))))
 })
 foreach($p in $targets){Log ('Closing blocker '+$p.Name+' PID '+$p.ProcessId);Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue}
 Start-Sleep -Seconds 2
 $remaining=@(Get-CimInstance Win32_Process | Where-Object {$_.ProcessId -ne $PID -and (($_.ExecutablePath -and $_.ExecutablePath.StartsWith(($Drive+'\'),[StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.Contains(($Drive+'\'))) )})
 if($remaining.Count){throw ('SSD process still active: '+(($remaining|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))}
 Set-Content -LiteralPath $pause -Value 'Paused for Windows eject' -Encoding ASCII
 $dc=@(Get-CimInstance Win32_Process | Where-Object {$_.ProcessId -ne $PID -and $_.CommandLine -match 'DesktopCommanderStartup|desktop-commander'})
 foreach($p in $dc){if($p.Name -in @('node.exe','powershell.exe')){Log ('Pausing Desktop Commander '+$p.ProcessId);Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue}}
 Start-Sleep -Seconds 3
 $result=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive 2>&1
 Log ('Windows eject result: '+($result -join ' ')+' exit '+$LASTEXITCODE)
 if(!(Present)){Log 'SUCCESS: Windows removed Prometheus volume';Resume;exit 0}
 throw ('Windows did not release SSD: '+($result -join ' '))
}catch{Log ('BLOCKED: '+$_.Exception.Message);Resume;exit 2}