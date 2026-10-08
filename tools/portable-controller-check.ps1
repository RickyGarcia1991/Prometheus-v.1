param([string]$Root=(Split-Path $PSScriptRoot -Parent),[switch]$Json)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Root)
$exe=Join-Path $root 'Prometheus-Controller-v0.5.5\Prometheus.DriveController.exe'
$installer=Join-Path $root 'INSTALL-PROMETHEUS-HOST-AGENT.ps1'
$agent=Join-Path $env:LOCALAPPDATA 'Prometheus\Prometheus-Host-Agent.ps1'
$runtime=Get-Command dotnet -ErrorAction SilentlyContinue
$frameworks=if($runtime){& $runtime.Source --list-runtimes 2>$null}else{@()}
$desktop=[bool](@($frameworks|Where-Object {$_ -match '^Microsoft.WindowsDesktop.App 10\.'}).Count)
$report=[ordered]@{
  controller_files=(Test-Path $exe -PathType Leaf)
  desktop_runtime=$desktop
  host_agent_installed=(Test-Path $agent -PathType Leaf)
  optional_host_agent_installer=(Test-Path $installer -PathType Leaf)
  controller_launch_requires_host_setup=(-not $desktop)
  message='Read-only preflight. Does not install services, grant permissions, or override intentional close.'
}
if($Json){$report|ConvertTo-Json}else{$report|Format-List}
if(!$report.controller_files){exit 2}
