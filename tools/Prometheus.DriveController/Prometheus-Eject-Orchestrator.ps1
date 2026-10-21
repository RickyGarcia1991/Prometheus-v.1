param([ValidatePattern('^[A-Za-z]:$')][string]$Drive='D:',[switch]$InspectOnly,[switch]$PrepareOnly)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Prometheus-Controller-Common.ps1')
. (Join-Path $PSScriptRoot 'Prometheus-Lifecycle.ps1')
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
 $identityText=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive -InspectOnly -Json
 if($LASTEXITCODE -ne 0){throw 'Exact USB identity check failed'}
 $identity=($identityText -join "`n")|ConvertFrom-Json
 if($InspectOnly){$identity|ConvertTo-Json -Compress;exit 0}
 New-Item -ItemType Directory -Force -Path $local | Out-Null
 $supervisor=@(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction Stop|Where-Object {$_.CommandLine -like ('*'+$local+'\Prometheus-USB-Reconnect-Supervisor.ps1*')})
 if(!$supervisor.Count){throw 'The internal USB lifecycle supervisor is not running; automatic reconnection is unavailable'}
 $previous=Get-EjectState
 if($previous -and ($previous.drive -ne $Drive -or $previous.usb_instance -ne $identity.usb_instance -or $previous.phase -notin @('blocked','released','prepared'))){throw 'Another operation owns the eject lock'}
 $operation=if($previous){[string]$previous.id}else{[guid]::NewGuid().ToString('N')}
 $state=@{id=$operation;active=$true;drive=$Drive;usb_instance=$identity.usb_instance;started=(Get-Date).ToString('o');phase='stopping'}
 Write-LifecycleJson $lock $state
 $ownsLock=$true
 $marker='Prometheus eject '+$operation
 if((Test-Path -LiteralPath $pause) -and (Get-Content -LiteralPath $pause -Raw).Trim() -ne $marker){throw 'A different pause owns Desktop Commander'}
 Set-Content -LiteralPath $pause -Value $marker -Encoding ASCII
 Remove-Item -LiteralPath (Join-Path $local 'desired-running.flag') -ErrorAction SilentlyContinue
 Close-DriveWindows $root
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
    if((Get-DesktopCommanderChildRole $child $health ([string]$config.nodePath)) -eq 'unknown'){throw 'Desktop Commander has unknown active work'}
    if(@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+[int]$child.ProcessId) -ErrorAction Stop).Count){throw 'Desktop Commander has a running terminal/job; finish it first'}
   }
   $marker='Prometheus eject '+$operation
   foreach($path in @($pause,$stop)){
    if((Test-Path -LiteralPath $path) -and (Get-Content -LiteralPath $path -Raw).Trim() -ne $marker){throw 'A different Desktop Commander pause is already active'}
    Set-Content -LiteralPath $path -Value $marker -Encoding ASCII
   }
   $ownsPause=$true;$ownsStop=$true
   $deadline=(Get-Date).AddSeconds(25)
   do {
    if(!(Test-ProcessIdentity $remote)){break}
    Start-Sleep -Milliseconds 250
   }while((Get-Date) -lt $deadline)
   if(Test-ProcessIdentity $remote){throw 'Desktop Commander did not close cooperatively'}
   # Console teardown can trail node exit briefly. Do not claim release until
   # every previously verified child has also exited.
   $deadline=(Get-Date).AddSeconds(10)
   do{$left=@($children|Where-Object {Test-ProcessIdentity $_});if(!$left.Count){break};Start-Sleep -Milliseconds 250}while((Get-Date) -lt $deadline)
   if($left.Count){throw 'Desktop Commander child processes have not closed'}
   Say 'Desktop Commander closed cooperatively.'
  }
 }
 # Keep the runner paused even if Desktop Commander was already offline.
 $marker='Prometheus eject '+$operation
 if(!(Test-Path -LiteralPath $pause)){Set-Content -LiteralPath $pause -Value $marker -Encoding ASCII}
 elseif((Get-Content -LiteralPath $pause -Raw).Trim() -ne $marker){throw 'A different pause owns Desktop Commander'}
 Start-Sleep -Milliseconds 750
 $remaining=@(Get-DriveProcesses $Drive)
 if($remaining.Count){throw ('Drive references remain: '+(($remaining|ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))}
 if($PrepareOnly){
  $state.phase='prepared';$state.message='Prometheus services stopped and verified. Use Windows Safely Remove Hardware now; Windows removal is not yet confirmed.'
  Write-LifecycleJson $lock $state;Say $state.message;exit 0
 }
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Drive $Drive
 if($LASTEXITCODE -ne 0){throw 'Windows vetoed removal; keep the drive connected and review the blocker above'}
 if([IO.DriveInfo]::new($Drive+'\').IsReady){throw 'Volume is still mounted; keep it connected'}
 $state.phase='released';$state.message='Windows confirmed removal. Safe to unplug; services stay paused until physical USB disconnection.';Write-LifecycleJson $lock $state
 Say $state.message
 exit 0
}catch{
 if($ownsLock){$state.phase='blocked';$state.message=$_.Exception.Message;Write-LifecycleJson $lock $state}
 Say ('Eject blocked: '+$_.Exception.Message+'; services remain paused. Retry eject or explicitly Start Prometheus to cancel.')
 exit 2
}finally{
 # Only physical disconnection or an explicit user Start releases the pause.
 if($held){$mutex.ReleaseMutex()}
 $mutex.Dispose()
}
