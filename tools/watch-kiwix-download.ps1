param([string]$ResourceRoot='D:\Prometheus-Resources',[int]$IntervalSeconds=5)
$ErrorActionPreference='Stop'
if($IntervalSeconds -lt 1){throw 'IntervalSeconds must be positive'}
$file=Join-Path $ResourceRoot 'Knowledge\Kiwix\metadata\live-status.json'
while($true){
 if(Test-Path $file){
  $row=Get-Content $file -Raw|ConvertFrom-Json
  $age=((Get-Date)-(Get-Item $file).LastWriteTime).TotalSeconds
  Write-Output ($row|ConvertTo-Json -Compress)
  if($row.phase -eq 'verified'){exit 0}
  if($row.phase -eq 'failed'){exit 1}
  if($age -gt 30){Write-Warning 'Progress report is stale; do not infer continued work or readiness.'}
 }else{Write-Warning 'No measured progress report is available yet.'}
 Start-Sleep -Seconds $IntervalSeconds
}
