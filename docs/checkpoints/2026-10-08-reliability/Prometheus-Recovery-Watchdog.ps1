$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'Prometheus\Diagnostics'
$log=Join-Path $root 'recovery-watchdog.log'
$observations=Join-Path $root 'reliability-observations.jsonl'
$names=@('Prometheus USB Reconnect Supervisor','Prometheus Recovery Evidence Recorder','Prometheus Volume Watcher')
function Rotate([string]$path){
 try {
  if(!(Test-Path -LiteralPath $path)){return}
  if((Get-Item -LiteralPath $path).Length -lt 5MB){return}
  $old="$path.3";if(Test-Path -LiteralPath $old){Remove-Item -LiteralPath $old -Force}
  foreach($i in @(2,1)){ $from="$path.$i";$to="$path."+($i+1);if(Test-Path -LiteralPath $from){Move-Item -LiteralPath $from -Destination $to -Force} }
  Move-Item -LiteralPath $path -Destination "$path.1" -Force
 }catch{Write-Output "Log rotation deferred: $($_.Exception.Message)"}
}
function Log([string]$s){"$(Get-Date -Format o) $s" | Add-Content -LiteralPath $log -Encoding UTF8}
Rotate $log
Rotate $observations
$states=@()
foreach($name in $names){
 try {
  $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop
  $info=Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
  $before=[string]$task.State
  $result=[long]$info.LastTaskResult
  $action='none';$after=$before
  if($before -eq 'Disabled'){$action='skipped-disabled'}
  elseif($before -ne 'Running'){
   Start-ScheduledTask -TaskName $name -ErrorAction Stop
   Start-Sleep -Seconds 2
   $after=[string](Get-ScheduledTask -TaskName $name).State
   $action='restart-requested'
   Log "RESTART name=$name previous=$before previousResult=$result after=$after"
  }
  $states+=@{name=$name;before=$before;previousResult=$result;action=$action;after=$after}
 }catch{
  $states+=@{name=$name;action='error';message=$_.Exception.Message}
  Log "ERROR name=$name message=$($_.Exception.Message)"
 }
}
$intentional=Test-Path (Join-Path $env:LOCALAPPDATA 'Prometheus\controller-intentional-close.flag')
$sample=@{timestamp=(Get-Date).ToString('o');bootTime=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o');intentionalClose=$intentional;tasks=$states}
$sample|ConvertTo-Json -Depth 7 -Compress|Add-Content -LiteralPath $observations -Encoding UTF8
