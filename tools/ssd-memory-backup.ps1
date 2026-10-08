# Run verified SSD memory backup only when the expected removable volume is present.
$ErrorActionPreference = 'Stop'
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'"
if (!$drive -or $drive.VolumeName -ne 'Prometheus-2TB') {
    Write-Output 'SKIP: Prometheus-2TB SSD not mounted as D:'
    exit 0
}
$python = 'C:\Users\rolon\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$script = 'C:\Users\rolon\Documents\Prometheus-GitHub-Recovery\tools\scheduled-memory-backup.py'
$source = Join-Path $env:LOCALAPPDATA 'Prometheus\memory.sqlite3'
$dest = 'D:\Prometheus-Recovery\Memory-Backups'
& $python $script --source $source --destination $dest --keep 14
exit $LASTEXITCODE
