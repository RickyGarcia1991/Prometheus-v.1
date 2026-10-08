param([string]$Root=(Split-Path $PSScriptRoot -Parent),[switch]$RequestWindowsRemoval)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$drive=[IO.Path]::GetPathRoot($root).TrimEnd('\')
$check=Join-Path $root 'Prometheus-Resources\Tools\portable-eject-preflight.ps1'
if(!(Test-Path $check)){throw 'Preflight missing; no removal attempted'}
Write-Output 'Close Prometheus, controller, model server and other SSD users normally before requesting removal.'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $check -Root $root -Json
if($LASTEXITCODE -ne 0){Write-Error 'BLOCKED: SSD is busy or integrity could not be verified. No removal attempted.';exit 2}
if(!$RequestWindowsRemoval){Write-Output 'Preflight PASS. Windows removal NOT requested.';exit 0}
$helper=Join-Path $root 'Prometheus-Controller-v0.5.5\Prometheus-Safe-Eject.ps1'
if(!(Test-Path $helper)){throw 'Windows removal helper missing'}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $drive -InspectOnly
if($LASTEXITCODE -ne 0){Write-Error 'BLOCKED: USB identity verification failed.';exit 3}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $drive
exit $LASTEXITCODE
