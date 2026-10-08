# Back up the portable SSD memory to this Windows host when connected.
$ErrorActionPreference = 'Stop'
$volumes = @(Get-CimInstance Win32_LogicalDisk | Where-Object { $_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -in @(2,3) })
if ($volumes.Count -ne 1) {
    Write-Output 'SKIP: expected single Prometheus-2TB volume not found'
    exit 0
}
$root = $volumes[0].DeviceID + '\'
$python = 'C:\Users\rolon\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$script = 'C:\Users\rolon\Documents\Prometheus-GitHub-Recovery\tools\scheduled-memory-backup.py'
$source = Join-Path $root 'Prometheus-Data\memory.sqlite3'
$dest = Join-Path $env:LOCALAPPDATA 'Prometheus\backups\memory-automatic'
& $python $script --source $source --destination $dest --keep 14
exit $LASTEXITCODE
