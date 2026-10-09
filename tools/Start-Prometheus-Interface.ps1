param([switch]$NoBrowser)
$ErrorActionPreference='Stop'
$resource=Split-Path $PSScriptRoot -Parent
$driveRoot=Split-Path $resource -Parent
$python=Join-Path $resource 'Python\python.exe'
$guard=Join-Path $PSScriptRoot 'portable-memory-guard.py'
$stateRoot=Join-Path $driveRoot 'Prometheus-Data\Interface'
$url='http://127.0.0.1:54555/'
[IO.Directory]::CreateDirectory($stateRoot)|Out-Null
$record=Join-Path $stateRoot 'server.json'
if(Test-Path -LiteralPath $record){
 try{
  $previous=Get-Content -LiteralPath $record -Raw|ConvertFrom-Json
  $previousProcess=Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$previous.pid)
  if($previousProcess -and $previousProcess.CommandLine -like '*portable-memory-guard.py*' -and $previousProcess.CommandLine -like '*ui*'){
   $ready=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
   if($ready.StatusCode -eq 200){if(!$NoBrowser){Start-Process $url};Write-Output $url;exit 0}
  }
 }catch{}
}
$env:PROMETHEUS_RESOURCE_ROOT=$resource
$env:PROMETHEUS_PYTHON=$python
$env:OLLAMA_EXE=Join-Path $resource 'Ollama\runtime\ollama.exe'
$env:OLLAMA_MODELS=Join-Path $resource 'Ollama\.ollama\models'
$env:OLLAMA_HOST='127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD='true'
$env:OLLAMA_MAX_LOADED_MODELS='1'
$env:OLLAMA_NUM_PARALLEL='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$stamp=Get-Date -Format 'yyyyMMdd-HHmmssfff'
$stdout=Join-Path $stateRoot ($stamp+'.stdout.log')
$stderr=Join-Path $stateRoot ($stamp+'.stderr.log')
$arguments=@('-B','-X','utf8',('"'+$guard+'"'),'--root',('"'+$driveRoot.TrimEnd('\')+'/"'),'--','ui','--no-browser')
$child=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $driveRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
[pscustomobject]@{pid=$child.Id;url=$url;stdout=$stdout;stderr=$stderr}|ConvertTo-Json|Set-Content -LiteralPath $record -Encoding UTF8
$deadline=(Get-Date).AddSeconds(100)
while((Get-Date) -lt $deadline){
 $child.Refresh()
 if($child.HasExited){throw ('Prometheus could not open. Read '+$stderr)}
 if((Test-Path -LiteralPath $stdout) -and (Get-Content -LiteralPath $stdout -Raw) -match 'Prometheus interface:'){
  try{
   $ready=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
   if($ready.StatusCode -eq 200){if(!$NoBrowser){Start-Process $url};Write-Output $url;exit 0}
  }catch{}
 }
 Start-Sleep -Milliseconds 400
}
throw ('Interface startup is taking longer than expected. Read '+$stderr)
