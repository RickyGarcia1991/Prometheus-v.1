$ErrorActionPreference = 'Stop'
function Get-PrometheusRoot([string]$Drive) {
 if($Drive -notmatch '^[A-Za-z]:$'){throw 'Invalid drive letter'}
 $di = [IO.DriveInfo]::new($Drive + '\')
 if(!$di.IsReady -or $di.VolumeLabel -ne 'Prometheus-2TB'){throw 'Prometheus volume identity is unavailable or incorrect'}
 return $di.RootDirectory.FullName
}
function Get-DriveProcesses([string]$Drive) {
 # Query failures are errors, never evidence that the drive is idle.
 $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
 $ignored = @($PID)
 $ancestor = $PID
 for($i=0;$i -lt 12;$i++){
  $row = $all | Where-Object ProcessId -eq $ancestor | Select-Object -First 1
  if(!$row -or !$row.ParentProcessId){break}
  $ancestor = [int]$row.ParentProcessId; $ignored += $ancestor
 }
 $prefix = $Drive + '\'
 @($all | Where-Object {
  $ignored -notcontains [int]$_.ProcessId -and (
   ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) -or
   ($_.CommandLine -and $_.CommandLine.IndexOf($prefix,[StringComparison]::OrdinalIgnoreCase) -ge 0))
 })
}
function Test-PrometheusChat($p) {
 return $p.Name -match '^python(w)?\.exe$' -and $p.CommandLine -match 'prometheus\.py|prometheus_assistant|portable-memory-guard\.py'
}
function Test-PrometheusModel($p,[string]$Drive) {
 return $p.Name -match '^(ollama|llama-server)\.exe$' -and $p.ExecutablePath -and
  $p.ExecutablePath.StartsWith(($Drive+'\Prometheus-Resources\Ollama\'),[StringComparison]::OrdinalIgnoreCase)
}
function Test-ProcessIdentity($snapshot) {
 $current = Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$snapshot.ProcessId) -ErrorAction Stop
 return $current -and $current.CreationDate -eq $snapshot.CreationDate -and $current.ExecutablePath -eq $snapshot.ExecutablePath
}
function Get-DesktopCommanderChildRole($Child,$Health,[string]$NodePath) {
 # A Windows console host is a normal direct child of the owned remote node.
 # Check its exact system path; a similarly named program is still unknown.
 if($Child.Name -ieq 'conhost.exe' -and $Child.ExecutablePath -ieq (Join-Path $env:SystemRoot 'System32\conhost.exe')){return 'console-host'}
 if($Health.localExecutorPid -and [int]$Child.ProcessId -eq [int]$Health.localExecutorPid -and $Child.Name -ieq 'node.exe' -and $Child.ExecutablePath -ieq $NodePath){return 'executor'}
 return 'unknown'
}
function Test-PortableMemory([string]$Root) {
 $db = Join-Path $Root 'Prometheus-Data\memory.sqlite3'
 $python = Join-Path $Root 'Prometheus-Resources\Python\python.exe'
 if(!(Test-Path -LiteralPath $db) -or !(Test-Path -LiteralPath $python)){throw 'Portable memory or Python is missing'}
 & $python -B (Join-Path $PSScriptRoot 'verify-portable-memory.py') $db
 if($LASTEXITCODE -ne 0){throw 'Portable SQLite integrity check failed'}
}

function Test-PrometheusStudio($p,[string]$Drive) {
 return $p.Name -match '^python(w)?\.exe$' -and $p.ExecutablePath -and
  $p.ExecutablePath.StartsWith(($Drive+'\Prometheus-Resources\Python\'),[StringComparison]::OrdinalIgnoreCase) -and $p.CommandLine -match 'studio\.py(?:["\s]|$)'
}
function Stop-PrometheusStudios([string]$Drive) {
 foreach($row in @(Get-DriveProcesses $Drive | Where-Object {Test-PrometheusStudio $_ $Drive})){
  if(!(Test-ProcessIdentity $row)){continue}
  # Legacy Studio instances do not yet observe the shared shutdown file.
  $ports=@(Get-NetTCPConnection -OwningProcess ([int]$row.ProcessId) -State Listen -ErrorAction SilentlyContinue | Where-Object LocalAddress -eq '127.0.0.1')
  foreach($port in $ports){
   $origin='http://127.0.0.1:'+([int]$port.LocalPort)
   try {
    $bootstrap=Invoke-RestMethod ($origin+'/api/bootstrap') -TimeoutSec 3
    if($bootstrap.version -notmatch '^1\.0\.[0-9]+$' -or !$bootstrap.token){continue}
    Invoke-RestMethod ($origin+'/api/close') -Method Post -ContentType 'application/json' -Body '{}' -Headers @{Origin=$origin;'X-Prometheus-Token'=$bootstrap.token} -TimeoutSec 5|Out-Null
   }catch{}
  }
 }
}
