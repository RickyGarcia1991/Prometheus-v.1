param([string]$Root=(Split-Path $PSScriptRoot -Parent),[switch]$RequestWindowsRemoval)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$preflight=Join-Path $root 'Prometheus-Resources\Tools\portable-eject-preflight.ps1'
if(!(Test-Path $preflight)){throw 'Safety preflight missing. No eject requested.'}
$logdir=Join-Path $root 'Prometheus-Recovery\Migration-Diagnostics'
New-Item -ItemType Directory -Path $logdir -Force | Out-Null
$log=Join-Path $logdir ('shutdown-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.json')
# Cooperative only: request close from interactive user, never force-kill an SSD process.
Write-Output 'Close Prometheus chat, controller, Ollama/model server and SSD files normally.'
Write-Output 'No processes will be killed. This script refuses to eject a busy SSD.'
$raw=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $preflight -Root $root -Json 2>&1
$code=$LASTEXITCODE
$record=[ordered]@{timestamp_utc=(Get-Date).ToUniversalTime().ToString('o');host_name=$env:COMPUTERNAME;preflight_exit=$code;windows_removal_requested=[bool]$RequestWindowsRemoval;preflight_output=($raw -join "`n");outcome='blocked';note='No forced process termination'}
if($code -eq 0){$record.outcome='preflight_pass'}
$record|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $log -Encoding UTF8
Write-Output ('Shutdown report saved: '+$log)
Write-Output $record.preflight_output
if($code -ne 0){Write-Output 'BLOCKED: SSD still in use or memory verification failed.';exit 2}
if(!$RequestWindowsRemoval){Write-Output 'PASS: preflight only. Windows eject was not requested.';exit 0}
$helper=Join-Path $root 'Prometheus-Controller-v0.5.5\Prometheus-Safe-Eject.ps1'
if(!(Test-Path $helper)){throw 'Windows eject helper missing'}
$drive=[IO.Path]::GetPathRoot($root).TrimEnd('\')
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $drive -InspectOnly
if($LASTEXITCODE -ne 0){throw 'Device identity verification failed; no eject attempted'}
Write-Output 'Preflight passed. Requesting Windows safe removal without killing processes.'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $drive
exit $LASTEXITCODE
