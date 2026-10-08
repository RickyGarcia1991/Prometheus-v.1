param([switch]$Once)
$ErrorActionPreference='Continue'
$seen=@{}
while($true){
 foreach($disk in @(Get-CimInstance Win32_LogicalDisk -ErrorAction SilentlyContinue | Where-Object {$_.VolumeName -eq 'Prometheus-2TB'})){
  $root=$disk.DeviceID+'\'
  $check=Join-Path $root 'Prometheus-Resources\Tools\portable-migration-diagnostic.ps1'
  if((Test-Path $check) -and -not $seen.ContainsKey($disk.DeviceID)){
   $seen[$disk.DeviceID]=$true
   & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $check -Root $root
  }
 }
 foreach($key in @($seen.Keys)){
  if(-not (Test-Path ($key+'\'))){$seen.Remove($key)}
 }
 if($Once){break}
 Start-Sleep -Seconds 10
}
