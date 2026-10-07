param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference='Stop'
$root=$Target.TrimEnd('\')+'\'
if(!(Test-Path $root)){throw "Target drive is not mounted: $root"}
$drive=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$Target.TrimEnd('\')+"'")
if(!$drive){throw "Target must be a mounted Windows drive letter."}
$required=2GB
if([int64]$drive.FreeSpace -lt $required){throw "At least 2 GB free is required for the portable core kit."}
$dest=Join-Path $root 'Prometheus-Flash-Kit'
New-Item -ItemType Directory -Force $dest|Out-Null
$repo=Split-Path $PSScriptRoot -Parent
Copy-Item (Join-Path $repo 'tools') $dest -Recurse -Force
Copy-Item (Join-Path $repo 'src') $dest -Recurse -Force
Copy-Item (Join-Path $repo 'prometheus.py') $dest -Force
Copy-Item (Join-Path $repo 'START_PROMETHEUS.cmd') $dest -Force
Copy-Item (Join-Path $repo 'tools\Prometheus-Portable-Control.ps1') $dest -Force
@'
@echo off
set "RUNNER=%LOCALAPPDATA%\DesktopCommanderStartup\Run-DesktopCommander.ps1"
if exist "%RUNNER%" powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "%RUNNER%"
'@ | Set-Content -Encoding ASCII (Join-Path $root 'START-DESKTOP-COMMANDER.cmd')
@'
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Prometheus-Flash-Kit\Prometheus-Portable-Control.ps1" -Action ui -Drive "%~d0"
'@ | Set-Content -Encoding ASCII (Join-Path $root 'START PROMETHEUS.cmd')
@{
 created=(Get-Date).ToString('o');source='Prometheus portable core';resources_included=$false;
 note='Large Ollama/Kiwix resources are intentionally not copied. Attach or copy validated resources separately.'
}|ConvertTo-Json|Set-Content -Encoding UTF8 (Join-Path $dest 'FLASH-KIT-STATUS.json')
Write-Host "Portable core prepared at $dest"
Write-Host "Knowledge/model resources are not included; this prevents accidental large transfers."
