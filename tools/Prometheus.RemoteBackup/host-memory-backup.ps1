# Installed beside Invoke-MemoryBackup.ps1 and scheduled-memory-backup.py.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-MemoryBackup.ps1') -Target Host
exit $LASTEXITCODE
