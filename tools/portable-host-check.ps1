param([string]$Root=(Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),[switch]$Json)
$ErrorActionPreference='Stop'
$diagnostic=Join-Path ([IO.Path]::GetFullPath($Root)) 'Prometheus-Resources\Tools\portable-migration-diagnostic.ps1'
if(!(Test-Path -LiteralPath $diagnostic -PathType Leaf)){throw 'Portable migration diagnostic is missing'}
$raw=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $diagnostic -Root $Root -Json 2>&1
$diagnosticExit=$LASTEXITCODE
$details=($raw -join "`n") | ConvertFrom-Json
$checks=@(
    [pscustomobject]@{name='Portable core and checksums';pass=[bool]($details.core_present -and $details.core_integrity);required=$true},
    [pscustomobject]@{name='Portable Python';pass=[bool]$details.python_present;required=$true},
    [pscustomobject]@{name='Portable Ollama';pass=[bool]$details.ollama_present;required=$true},
    [pscustomobject]@{name='Installed model files';pass=[bool]$details.model_present;required=$true},
    [pscustomobject]@{name='SSD memory integrity';pass=($details.memory_integrity -eq 'ok');required=$true},
    [pscustomobject]@{name='Optional controller and desktop runtime';pass=[bool]$details.controller_ready;required=$false},
    [pscustomobject]@{name='Optional host agent detected';pass=[bool]$details.host_agent_detected;required=$false}
)
$report=[ordered]@{root=[IO.Path]::GetFullPath($Root);ready=[bool]$details.portable_ready;checks=$checks;details=$details}
if($Json){$report|ConvertTo-Json -Depth 12}else{$checks|Format-Table -AutoSize;Write-Output ('Portable prerequisites ready: '+$report.ready)}
exit $diagnosticExit
