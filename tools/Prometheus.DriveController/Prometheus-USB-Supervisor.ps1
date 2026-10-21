$ErrorActionPreference='Stop'
# Compatibility route: one owner observes the exact physical USB identity.
$script=Join-Path $env:LOCALAPPDATA 'Prometheus\Prometheus-USB-Reconnect-Supervisor.ps1'
if(!(Test-Path -LiteralPath $script)){throw 'Install the current signed lifecycle supervisor first.'}
& $script
