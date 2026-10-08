param([string]$Root=(Split-Path $PSScriptRoot -Parent),[switch]$Json)
$ErrorActionPreference='Stop'
$checks=@()
function Check($name,$ok,$detail){$script:checks+= [pscustomobject]@{name=$name;pass=[bool]$ok;detail=[string]$detail}}
$root=[IO.Path]::GetFullPath($Root)
$os=Get-CimInstance Win32_OperatingSystem
$cs=Get-CimInstance Win32_ComputerSystem
Check 'Windows x64' ([Environment]::Is64BitOperatingSystem -and $os.Caption -match 'Windows') $os.Caption
Check 'RAM at least 7.5 GiB (8 GB class)' ($cs.TotalPhysicalMemory -ge 7.5GB) ('{0:N1} GiB' -f ($cs.TotalPhysicalMemory/1GB))
$py=Join-Path $root 'Prometheus-Resources\Python\python.exe'
$ollama=Join-Path $root 'Prometheus-Resources\Ollama\runtime\ollama.exe'
$models=Join-Path $root 'Prometheus-Resources\Ollama\.ollama\models'
$memory=Join-Path $root 'Prometheus-Data\memory.sqlite3'
Check 'Portable Python' (Test-Path $py -PathType Leaf) $py
Check 'Portable Ollama' (Test-Path $ollama -PathType Leaf) $ollama
Check 'Local models' (Test-Path $models -PathType Container) $models
Check 'SSD memory' (Test-Path $memory -PathType Leaf) $memory
Check 'SSD write access' ((Test-Path $memory -PathType Leaf) -and -not ((Get-Item $memory -ErrorAction SilentlyContinue).IsReadOnly)) 'Database exists and is not read-only'
$controller=Join-Path $root 'Prometheus-Controller-v0.5.5\Prometheus.DriveController.exe'
Check 'Portable controller files' (Test-Path $controller -PathType Leaf) $controller
$runtime=Get-Command dotnet -ErrorAction SilentlyContinue
$frameworks=if($runtime){& $runtime.Source --list-runtimes 2>$null}else{@()}
Check '.NET 10 Windows Desktop runtime' ([bool](@($frameworks|Where-Object {$_ -match '^Microsoft.WindowsDesktop.App 10\.'}).Count)) 'Needed only for controller UI'
$report=[ordered]@{root=$root;ready=(@($checks|Where-Object {-not $_.pass}).Count -eq 0);checks=$checks}
if($Json){$report|ConvertTo-Json -Depth 5}else{$checks|Format-Table -AutoSize}
if(!$report.ready){exit 1}
