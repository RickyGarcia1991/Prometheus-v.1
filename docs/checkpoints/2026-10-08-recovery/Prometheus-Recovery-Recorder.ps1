$ErrorActionPreference='Continue'
$root=Join-Path $env:LOCALAPPDATA 'Prometheus'
$diag=Join-Path $root 'Diagnostics'
$out=Join-Path $diag 'recovery-observations.jsonl'
$flag=Join-Path $root 'controller-intentional-close.flag'
$lock=Join-Path $root 'eject-mode.json'
$retry=Join-Path $diag 'controller-restart-state.json'
$supervisorLog=Join-Path $diag 'usb-supervisor.log'
New-Item -ItemType Directory -Path $diag -Force | Out-Null
function Record([string]$type,$data) {
 try {
  $record=[ordered]@{timestamp=(Get-Date).ToString('o');type=$type;data=$data}
  ($record|ConvertTo-Json -Compress -Depth 8)|Add-Content -LiteralPath $out -Encoding UTF8
 } catch {}
}
$previous=''
Record 'recorder-start' @{pid=$PID;location=$PSCommandPath}
while($true){
 try {
  $disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'" -ErrorAction SilentlyContinue
  $processes=@(Get-CimInstance Win32_Process -Filter "Name='Prometheus.DriveController.exe'" -ErrorAction SilentlyContinue)
  $task=Get-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -ErrorAction SilentlyContinue
  $state=[ordered]@{
   drivePresent=[bool]$disk
   driveLabel=if($disk){[string]$disk.VolumeName}else{''}
   controllerPids=@($processes|ForEach-Object {$_.ProcessId})
   intentionalClose=(Test-Path -LiteralPath $flag)
   ejectLock=(Test-Path -LiteralPath $lock)
   supervisorTask=if($task){[string]$task.State}else{'Unavailable'}
  }
  $json=$state|ConvertTo-Json -Compress -Depth 5
  if($json -ne $previous){Record 'state-change' $state;$previous=$json}
  if((Test-Path $retry) -and ((Get-Item $retry).LastWriteTimeUtc -gt (Get-Date).ToUniversalTime().AddSeconds(-6))){
   $retryData=Get-Content $retry -Raw -ErrorAction SilentlyContinue
   if($retryData){Record 'retry-state-observed' @{content=$retryData}}
  }
 } catch {Record 'recorder-error' @{message=$_.Exception.Message}}
 Start-Sleep -Seconds 2
}
