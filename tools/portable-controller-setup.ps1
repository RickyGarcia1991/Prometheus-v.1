param([string]$Root=(Split-Path $PSScriptRoot -Parent),[ValidateSet('Check','InstallHostAgent','LaunchController')][string]$Mode='Check')
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Root)
$preflight=Join-Path $root 'Prometheus-Resources\Tools\portable-controller-check.ps1'
$launcher=Join-Path $root 'START-PROMETHEUS-CONTROLLER.cmd'
$installer=Join-Path $root 'INSTALL-PROMETHEUS-HOST-AGENT.ps1'
if(!(Test-Path $preflight)){throw 'Controller preflight missing'}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $preflight -Root $root -Json
if($LASTEXITCODE -ne 0){throw 'Controller preflight failed'}
if($Mode -eq 'Check'){exit 0}
if($Mode -eq 'InstallHostAgent'){
  if(!(Test-Path $installer)){throw 'Signed host agent installer missing'}
  Write-Output 'Host agent installation requires Windows UAC approval and verifies signed package.'
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Mode Install
  exit $LASTEXITCODE
}
if(!(Test-Path $launcher)){throw 'Controller launcher missing'}
& cmd.exe /d /c ('"'+$launcher+'"')
exit $LASTEXITCODE
