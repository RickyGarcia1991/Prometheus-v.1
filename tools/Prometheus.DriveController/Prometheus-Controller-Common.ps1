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
function Test-PortableMemory([string]$Root) {
 $db = Join-Path $Root 'Prometheus-Data\memory.sqlite3'
 $python = Join-Path $Root 'Prometheus-Resources\Python\python.exe'
 if(!(Test-Path -LiteralPath $db) -or !(Test-Path -LiteralPath $python)){throw 'Portable memory or Python is missing'}
 & $python -B (Join-Path $PSScriptRoot 'verify-portable-memory.py') $db
 if($LASTEXITCODE -ne 0){throw 'Portable SQLite integrity check failed'}
}
