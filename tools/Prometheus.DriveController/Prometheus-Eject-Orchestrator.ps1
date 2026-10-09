param([ValidatePattern('^[A-Za-z]:$')][string]$Drive='D:',[switch]$InspectOnly)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Prometheus-Controller-Common.ps1')
$local=Join-Path $env:LOCALAPPDATA 'Prometheus'
$dc=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup'
$pause=Join-Path $dc 'runner-paused.request'
$stop=Join-Path $dc 'remote-stop.request'
$lock=Join-Path $local 'eject-mode.json'
$ownsPause=$false;$ownsStop=$false;$ownsLock=$false;$held=$false
$mutex=[Threading.Mutex]::new($false,('Local\PrometheusEject-'+$Drive[0]))
function Say([string]$s){Write-Output $s}
function Close-DriveWindows([string]$Root) {
 # Close only Explorer windows whose displayed folder is on this volume.
 $shell=New-Object -ComObject Shell.Application
 try {
  foreach($window in @($shell.Windows())){
   try {
    $path=[string]$window.Document.Folder.Self.Path
    if($path -and ($path.TrimEnd('\') -ieq $Root.TrimEnd('\') -or $path.StartsWith($Root,[StringComparison]::OrdinalIgnoreCase))){
     Say ('Closing Explorer folder '+$path);$window.Quit()
    }
   }catch{Say ('Explorer window could not be closed: '+$_.Exception.Message)}
  }
 }finally{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)}
 # WM_CLOSE is cooperative. Unresponsive/unsaved-work prompts remain blockers.
 foreach($row in @(Get-DriveProcesses $Drive)){
  if($row.Name -notmatch '^(cmd|powershell|pwsh|WindowsTerminal|notepad)\.exe$'){continue}
  if(!(Test-ProcessIdentity $row)){continue}
  $children=@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+[int]$row.ProcessId) -ErrorAction Stop)
  if($children.Count){Say ('Leaving busy program open: '+$row.Name+' PID '+$row.ProcessId);continue}
  $p=[Diagnostics.Process]::GetProcessById([int]$row.ProcessId)
  try{if($p.CloseMainWindow()){Say ('Requested normal window close: '+$row.Name+' PID '+$row.ProcessId)}}
  finally{$p.Dispose()}
 }
}
try {
 try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
 if(!$held){throw 'Another eject request is already running'}
 $root=Get-PrometheusRoot $Drive
 if([IO.Path]::GetFullPath($PSScriptRoot).StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){
  throw 'Run the controller from its verified cache on the computer before ejecting'
 }
 $helper=Join-Path $PSScriptRoot 'Prometheus-Safe-Eject.ps1'
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive -InspectOnly
 if($LASTEXITCODE -ne 0){throw 'Exact USB identity check failed'}
 if($InspectOnly){Say 'Inspection passed. No applications closed and no removal requested.';exit 0}
 New-Item -ItemType Directory -Force -Path $local | Out-Null
 if(Test-Path -LiteralPath $lock){throw 'Another operation owns the eject lock'}
 @{active=$true;drive=$Drive;started=(Get-Date).ToString('o');phase='stopping'} | ConvertTo-Json | Set-Content -LiteralPath $lock -Encoding UTF8
 $ownsLock=$true
 $result=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Prometheus-Drive-Engine.ps1') -Drive $Drive -Action stop
 $stopExit=$LASTEXITCODE
 $status=($result -join [Environment]::NewLine) | ConvertFrom-Json
 if($stopExit -ne 0 -or $status.phase -ne 'green'){throw ('Prometheus did not stop: '+$status.activity)}
 Say $status.message
 Close-DriveWindows $root
 $healthFile=Join-Path $dc 'connection-health.json'
 if(Test-Path -LiteralPath $healthFile){
  $health=Get-Content -LiteralPath $healthFile -Raw | ConvertFrom-Json
  $remote=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$health.pid) -ErrorAction Stop
  if($remote){
   $config=Get-Content -LiteralPath (Join-Path $dc 'runner-settings.json') -Raw | ConvertFrom-Json
   if($remote.ExecutablePath -ine $config.nodePath -or !$remote.CommandLine.Contains([string]$config.entryPath)){
    throw 'Desktop Commander process identity could not be verified'
   }
   if(((Get-Date).ToUniversalTime()-[DateTime]::Parse($health.updatedAt).ToUniversalTime()).TotalSeconds -gt 20 -or $health.activeCalls -ne 0){
    throw 'Desktop Commander is busy or its activity report is stale'
   }
   $children=@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+[int]$remote.ProcessId) -ErrorAction Stop)
   foreach($child in $children){
    if($child.ProcessId -ne $health.localExecutorPid -or $child.Name -ne 'node.exe'){throw 'Desktop Commander has unknown active work'}
    if(@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+[int]$child.ProcessId) -ErrorAction Stop).Count){throw 'Desktop Commander has a running terminal/job; finish it first'}
   }
   if(!(Test-Path -LiteralPath $pause)){Set-Content -LiteralPath $pause -Value 'Prometheus eject handoff';$ownsPause=$true}
   if(Test-Path -LiteralPath $stop){throw 'A Desktop Commander stop operation is already pending'}
   Set-Content -LiteralPath $stop -Value 'Prometheus eject handoff';$ownsStop=$true
   $deadline=(Get-Date).AddSeconds(25)
   do {
    if(!(Test-ProcessIdentity $remote)){break}
    Start-Sleep -Milliseconds 250
   }while((Get-Date) -lt $deadline)
   if(Test-ProcessIdentity $remote){throw 'Desktop Commander did not close cooperatively'}
   foreach($child in $children){if(Test-ProcessIdentity $child){throw 'Desktop Commander executor has not closed'}}
   Say 'Desktop Commander closed cooperatively.'
  }
 }
 Start-Sleep -Milliseconds 750
 $remaining=@(Get-DriveProcesses $Drive)
 if($remaining.Count){throw ('Drive references remain: '+(($remaining|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))}
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive
 if($LASTEXITCODE -ne 0){throw 'Windows vetoed removal; keep the drive connected and review the blocker above'}
 if([IO.DriveInfo]::new($Drive+'\').IsReady){throw 'Volume is still mounted; keep it connected'}
 Say 'Windows confirmed removal. Safe to unplug.'
 exit 0
}catch{
 Say ('Eject blocked: '+$_.Exception.Message)
 exit 2
}finally{
 if($ownsStop){Remove-Item -LiteralPath $stop -ErrorAction SilentlyContinue}
 if($ownsPause){
  Remove-Item -LiteralPath $pause -ErrorAction SilentlyContinue
  $runner=Join-Path $dc 'Run-DesktopCommander.ps1'
  if(Test-Path -LiteralPath $runner){
   # Runner mutex prevents duplicates. Its existing instance also sees pause removal.
   Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$runner+'"')) -WorkingDirectory $dc -WindowStyle Hidden | Out-Null
  }
 }
 if($ownsLock){Remove-Item -LiteralPath $lock -ErrorAction SilentlyContinue}
 if($held){$mutex.ReleaseMutex()}
 $mutex.Dispose()
}
