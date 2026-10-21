if(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Prometheus\eject-mode.json')){exit 0}
# Verified backup of portable memory, only when the expected SSD is connected.
$ErrorActionPreference = 'Stop'
$volumes = @(Get-CimInstance Win32_LogicalDisk | Where-Object { $_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -in @(2,3) })
if ($volumes.Count -ne 1) {
    Write-Output 'SKIP: expected single Prometheus-2TB removable SSD not found'
    exit 0
}
$root = $volumes[0].DeviceID + '\'
$python = 'C:\Users\rolon\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$script = 'C:\Users\rolon\Documents\Prometheus-GitHub-Recovery\tools\scheduled-memory-backup.py'
$source = Join-Path $root 'Prometheus-Data\memory.sqlite3'
$dest = Join-Path $root 'Prometheus-Recovery\Memory-Backups'
if(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Prometheus\eject-mode.json')){exit 0}
& $python $script --source $source --destination $dest --keep 14
exit $LASTEXITCODE
