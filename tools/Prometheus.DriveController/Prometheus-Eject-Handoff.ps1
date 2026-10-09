param([ValidatePattern('^[A-Za-z]:$')][string]$Drive='D:')
$ErrorActionPreference='Stop'
# Compatibility route: graceful orchestrator replaces broad process termination.
$script=Join-Path $env:LOCALAPPDATA 'Prometheus\Prometheus-Eject-Orchestrator.ps1'
if(!(Test-Path -LiteralPath $script)){throw 'Install the current signed lifecycle support before ejecting.'}
& $script -Drive $Drive
exit $LASTEXITCODE
