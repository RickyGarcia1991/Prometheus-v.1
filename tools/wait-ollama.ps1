param([int]$TimeoutSeconds=120)
$ErrorActionPreference='Stop'
function Ready {
 try {
  $result=Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
  return $null -ne $result.models
 } catch {return $false}
}
if(Ready){Write-Output 'Local Ollama is ready.'; exit 0}
if(!(Test-Path $env:OLLAMA_EXE)){throw 'Portable Ollama executable missing'}
$expected=[IO.Path]::GetFullPath($env:OLLAMA_EXE)
$existing=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'ollama.exe' -and $_.ExecutablePath -eq $expected})
if(!$existing.Count){
 $logs=Join-Path $env:LOCALAPPDATA 'Prometheus\logs'
 New-Item -ItemType Directory -Force $logs|Out-Null
 $stamp=Get-Date -Format yyyyMMdd-HHmmss
 Start-Process $expected -ArgumentList serve -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs ('ollama-'+$stamp+'.stdout.log')) -RedirectStandardError (Join-Path $logs ('ollama-'+$stamp+'.stderr.log'))|Out-Null
 Write-Output 'Starting portable Ollama; waiting for measured readiness.'
} else {Write-Output 'Waiting for the existing portable Ollama process.'}
$deadline=(Get-Date).AddSeconds($TimeoutSeconds)
while((Get-Date) -lt $deadline){
 if(Ready){Write-Output 'Local Ollama readiness verified.';exit 0}
 Start-Sleep -Milliseconds 500
}
Write-Error 'Local Ollama did not become ready before the deadline. See Prometheus logs.'
exit 1
