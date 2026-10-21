$ErrorActionPreference='Stop'
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
# Compatibility entry point. The single supervisor owns physical USB state.
& (Join-Path $local 'Prometheus-USB-Reconnect-Supervisor.ps1')
