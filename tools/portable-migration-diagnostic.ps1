param([string]$Root=(Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),[switch]$Json)
$ErrorActionPreference='Stop'
$rootPath=[IO.Path]::GetFullPath($Root)
$report=[ordered]@{schema_version=2;timestamp_utc=(Get-Date).ToUniversalTime().ToString('o');host_name=[Environment]::MachineName;os_version=[Environment]::OSVersion.VersionString;drive_root=[IO.Path]::GetPathRoot($rootPath);ssd_label=$null;core_present=$false;core_integrity=$false;python_present=$false;ollama_present=$false;model_present=$false;memory_present=$false;memory_integrity='not checked';portable_ready=$false;controller_ready=$false;host_agent_detected=$false;controller_preflight=$null;controller_check_exit=$null;ready=$false;notes=@('Read-only component checks; only the diagnostic report is written. No host installation, model startup, partition changes, or memory writes.','ready describes portable prerequisites; controller and automatic startup are separate optional features.')}
try { $report.ssd_label=(New-Object IO.DriveInfo($report.drive_root)).VolumeLabel } catch { $report.notes+=('Volume label unavailable: '+$_.Exception.Message) }
$py=Join-Path $rootPath 'Prometheus-Resources\Python\python.exe'
$probe=Join-Path $rootPath 'Prometheus-Resources\Tools\portable-readiness.py'
$report.python_present=Test-Path -LiteralPath $py -PathType Leaf
try {
    if(!$report.python_present){throw 'Portable Python executable is missing'}
    if(!(Test-Path -LiteralPath $probe -PathType Leaf)){throw 'Portable readiness helper is missing'}
    $raw=& $py -B $probe --root $rootPath 2>&1
    $probeExit=$LASTEXITCODE
    $core=($raw -join "`n") | ConvertFrom-Json
    foreach($key in @('core_present','core_integrity','python_present','ollama_present','model_present','memory_present','memory_integrity','portable_ready')) { $report[$key]=$core.$key }
    $report.core_details=$core
    $report.portable_ready=($core.portable_ready -and $probeExit -eq 0)
} catch { $report.notes+=('Portable check: '+$_.Exception.Message) }
$preflight=Join-Path $rootPath 'Prometheus-Resources\Tools\portable-controller-check.ps1'
try {
    if(!(Test-Path -LiteralPath $preflight -PathType Leaf)){throw 'Optional controller preflight is missing'}
    $raw=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $preflight -Root $rootPath -Json 2>&1
    $report.controller_check_exit=$LASTEXITCODE
    $report.controller_preflight=($raw -join "`n") | ConvertFrom-Json
    $report.controller_ready=($report.controller_check_exit -eq 0 -and $report.controller_preflight.controller_files -and $report.controller_preflight.package_ready_for_host_agent -and $report.controller_preflight.desktop_runtime)
    $report.host_agent_detected=[bool]$report.controller_preflight.host_agent_installed
} catch { $report.notes+=('Optional controller check: '+$_.Exception.Message) }
$report.ready=[bool]$report.portable_ready
try {
    $logdir=Join-Path $rootPath 'Prometheus-Recovery\Migration-Diagnostics'
    New-Item -ItemType Directory -Force -Path $logdir | Out-Null
    $reportPath=Join-Path $logdir ('host-check-'+(Get-Date -Format 'yyyyMMdd-HHmmssfff')+'.json')
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    if(!$Json){Write-Output ('Diagnostic saved: '+$reportPath)}
} catch { $report.notes+=('Could not save report: '+$_.Exception.Message) }
$report | ConvertTo-Json -Depth 10
if(!$report.ready){exit 2}
exit 0
