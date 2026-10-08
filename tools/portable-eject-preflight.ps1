param([string]$Root=(Split-Path $PSScriptRoot -Parent),[switch]$Json)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$drive=[IO.Path]::GetPathRoot($root).TrimEnd('\')
$vol=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'"
if(!$vol -or $vol.VolumeName -ne 'Prometheus-2TB'){throw 'Expected Prometheus-2TB volume not found'}
$memory=Join-Path $root 'Prometheus-Data\memory.sqlite3'
$lock=Join-Path $root 'Prometheus-Data\.memory-session.lock'
$busy=$false
if(Test-Path $lock){
  $h=$null
  try {
    $h=[IO.File]::Open($lock,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
  } catch [IO.IOException] { $busy=$true }
  finally { if($h){$h.Dispose()} }
}
$prefix=$drive+'\'
$processes=@(Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $PID -and $_.CommandLine -notmatch 'portable-eject-preflight\.ps1' -and
  (($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) -or
   ($_.CommandLine -and $_.CommandLine.Contains($prefix)))
} | Select-Object ProcessId,Name)
$integrity='not checked'
if(!$busy -and (Test-Path $memory)){
  $py=Join-Path $root 'Prometheus-Resources\Python\python.exe'
  if(Test-Path $py){
    $probe=& $py -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA quick_check').fetchone()[0]); c.close()" $memory 2>&1
    if($LASTEXITCODE -eq 0){$integrity=($probe -join ' ').Trim()}else{$integrity='error'}
  }
}
$report=[ordered]@{drive=$drive;memory_exists=(Test-Path $memory);memory_session_active=$busy;drive_processes=$processes;memory_quick_check=$integrity;ready_for_windows_safe_removal=((Test-Path $memory) -and -not $busy -and $processes.Count -eq 0 -and $integrity -eq 'ok');note='Preflight only. No process termination or physical eject attempted. Use Windows Safely Remove Hardware.'}
if($Json){$report|ConvertTo-Json -Depth 5}else{$report|Format-List}
if(!$report.ready_for_windows_safe_removal){exit 2}
