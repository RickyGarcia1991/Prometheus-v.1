param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$logdir=Join-Path $root 'Prometheus-Recovery\Migration-Diagnostics'
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$reportPath=Join-Path $logdir ('host-check-'+$stamp+'.json')
$report=[ordered]@{schema_version=1;timestamp_utc=(Get-Date).ToUniversalTime().ToString('o');host_name=[Environment]::MachineName;os_version=[Environment]::OSVersion.VersionString;drive_root=[IO.Path]::GetPathRoot($root);ssd_label=$null;core_present=$false;model_present=$false;memory_present=$false;memory_integrity='not checked';controller_preflight=$null;controller_check_exit=$null;ready=$false;notes=@('Read-only diagnostics; does not start models, modify memory, install services, or eject drives.')}
try {
 $disk=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$report.drive_root.TrimEnd('\')+"'")
 $report.ssd_label=if($disk){$disk.VolumeName}else{$null}
 $report.core_present=Test-Path (Join-Path $root 'START-PROMETHEUS-PORTABLE.cmd')
 $report.model_present=Test-Path (Join-Path $root 'Prometheus-Resources')
 $memory=Join-Path $root 'Prometheus-Data\memory.sqlite3'
 $report.memory_present=Test-Path $memory
 $py=Join-Path $root 'Prometheus-Resources\Python\python.exe'
 if($report.memory_present -and (Test-Path $py)){
  $probe=Join-Path $root 'Prometheus-Resources\Tools\migration-memory-check.py'
  if(Test-Path $probe){$result=& $py $probe $memory 2>&1}else{$result=@('memory probe missing')}
  $report.memory_integrity=if($LASTEXITCODE -eq 0){($result -join ' ').Trim()}else{'check failed'}
 }
 $preflight=Join-Path $root 'Prometheus-Resources\Tools\portable-controller-check.ps1'
 if(Test-Path $preflight){
  $raw=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $preflight -Root $root -Json 2>&1
  $report.controller_check_exit=$LASTEXITCODE
  try {$report.controller_preflight=($raw -join "`n" | ConvertFrom-Json)}catch {$report.notes+=('Controller check unreadable: '+($raw -join ' '))}
 }
 $report.ready=($report.ssd_label -eq 'Prometheus-2TB' -and $report.core_present -and $report.model_present -and $report.memory_integrity -eq 'ok' -and $report.controller_check_exit -eq 0 -and $report.controller_preflight.desktop_runtime -and $report.controller_preflight.host_agent_installed)
} catch {$report.notes+=('Check error: '+$_.Exception.Message)}
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Output ('Diagnostic saved: '+$reportPath)
$report | ConvertTo-Json -Depth 8
if(-not $report.ready){exit 2}
