param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference='Stop';$drive=$Target.TrimEnd('\');$root=$drive+'\';$ld=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$drive+"'");if(!$ld){throw 'Recovery-key target is not mounted.'};if([int64]$ld.FreeSpace -lt 2GB){throw 'At least 2 GB free is required.'}
$repo=Split-Path $PSScriptRoot -Parent;$dest=Join-Path $root 'Prometheus-Recovery-Key';if(Test-Path $dest){Remove-Item $dest -Recurse -Force};New-Item -ItemType Directory -Force $dest|Out-Null
foreach($n in @('tools','src','tests')){Copy-Item (Join-Path $repo $n) $dest -Recurse -Force};$ctlProj=Join-Path $repo 'tools\Prometheus.PRK.Controller\Prometheus.PRK.Controller.csproj';if(Test-Path $ctlProj){$ctlOut=Join-Path $dest 'PRK-Controller';dotnet publish $ctlProj -c Release -r win-x64 --self-contained false -o $ctlOut;if($LASTEXITCODE -ne 0){throw 'Portable PRK controller publish failed.'}};foreach($n in @('prometheus.py','START_PROMETHEUS.cmd','README.md','pyproject.toml')){if(Test-Path (Join-Path $repo $n)){Copy-Item (Join-Path $repo $n) $dest -Force}}
@{created=(Get-Date).ToString('o');role='Prometheus Recovery Key';version=1;resources_included=$false;secrets_included=$false;private_memory_included=$false;workflow='stage to C:, eject key, connect SSD, recover'}|ConvertTo-Json|Set-Content -Encoding UTF8 (Join-Path $dest 'PRK-STATUS.json')
@'
PROMETHEUS RECOVERY KEY (PRK)
Purpose: independently verify, diagnose, stage, bootstrap, and recover Prometheus.
Single USB port workflow: run recovery -> stage to C: -> safely eject PRK -> connect Prometheus SSD -> continue from staged recovery.
Excluded by design: passwords, API keys, private memory databases, Ollama models, and large Kiwix archives.
'@|Set-Content -Encoding UTF8 (Join-Path $dest 'RECOVERY-README.txt')
$manifestFiles=@(Get-ChildItem $dest -File -Recurse|Where-Object {$_.Name -ne 'PRK-MANIFEST.json'}|ForEach-Object {[ordered]@{path=$_.FullName.Substring($dest.Length+1);bytes=$_.Length;sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash}})
@{created=(Get-Date).ToString('o');algorithm='SHA256';files=$manifestFiles}|ConvertTo-Json -Depth 4|Set-Content -Encoding UTF8 (Join-Path $dest 'PRK-MANIFEST.json')
@'
@echo off
if exist "%~dp0Prometheus-Recovery-Key\PRK-Controller\Prometheus.PRK.Controller.exe" (start "" "%~dp0Prometheus-Recovery-Key\PRK-Controller\Prometheus.PRK.Controller.exe") else (powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Prometheus-Recovery-Key\tools\Prometheus-Recovery-Key.ps1" -Action ui -Drive "%~d0")
'@|Set-Content -Encoding ASCII (Join-Path $root 'PROMETHEUS RECOVERY.cmd')
@'
@echo off
set "RUNNER=%LOCALAPPDATA%\DesktopCommanderStartup\Run-DesktopCommander.ps1"
if exist "%RUNNER%" powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "%RUNNER%"
'@|Set-Content -Encoding ASCII (Join-Path $root 'START-DESKTOP-COMMANDER.cmd')
Write-Host ('Prometheus Recovery Key built: '+$dest);Write-Host ('Protected files: '+$manifestFiles.Count)
