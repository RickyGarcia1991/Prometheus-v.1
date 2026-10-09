# Shared by the internal-disk supervisor and the verified controller package.
function Write-LifecycleJson([string]$Path, $Value) {
 [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
 $temporary=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
 [IO.File]::WriteAllText($temporary,($Value|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
 if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temporary,$Path,[System.Management.Automation.Language.NullString]::Value)}else{[IO.File]::Move($temporary,$Path)}
}
function Get-EjectState {
 $path=Join-Path $env:LOCALAPPDATA 'Prometheus\eject-mode.json'
 if(Test-Path -LiteralPath $path){return (Get-Content -LiteralPath $path -Raw -ErrorAction Stop|ConvertFrom-Json)}
 return $null
}
function Get-PhysicalUsbPresence([string]$InstanceId) {
 if($InstanceId -notmatch '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\[^\r\n]+$'){throw 'Missing or invalid physical USB identity'}
 $escaped=$InstanceId.Replace('\','\\').Replace("'","\'")
 $rows=@(Get-CimInstance Win32_PnPEntity -Filter ("DeviceID='"+$escaped+"'") -ErrorAction Stop)
 if($rows.Count -gt 1){throw 'Ambiguous physical USB identity'}
 if(!$rows.Count){return $false}
 # CM_PROB_HELD_FOR_EJECT (47) means prepared for removal, still attached.
 if([int]$rows[0].ConfigManagerErrorCode -eq 47){return $true}
 if($null -eq $rows[0].Present){throw 'Physical USB presence is unavailable'}
 return [bool]$rows[0].Present
}
function Get-EjectDecision($State, $PhysicalPresent, [bool]$VolumePresent) {
 if(!$State){return 'normal'}
 if($null -eq $PhysicalPresent -or $PhysicalPresent -or $VolumePresent){return 'hold'}
 return 'disconnected'
}
function Start-LifecycleScript([string]$Script,[string[]]$Arguments=@()) {
 $all=@('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$Script)+$Arguments
 foreach($arg in $all){if($arg.Contains('"') -or $arg.Contains("`n") -or $arg.Contains("`r")){throw 'Invalid child argument'}}
 $info=[Diagnostics.ProcessStartInfo]::new((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'))
 $info.Arguments=($all|ForEach-Object {'"'+$_+'"'}) -join ' '
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.WindowStyle='Hidden'
 $info.WorkingDirectory=Join-Path $env:LOCALAPPDATA 'Prometheus'
 return [Diagnostics.Process]::Start($info)
}
function Resume-Lifecycle($State,[string]$Reason) {
 $local=Join-Path $env:LOCALAPPDATA 'Prometheus'
 $current=Get-EjectState
 if(!$current -or !$State.id -or $current.id -ne $State.id){throw 'Eject ownership changed'}
 $dc=Join-Path $env:LOCALAPPDATA 'DesktopCommanderStartup'
 foreach($name in @('remote-stop.request','runner-paused.request')){
  $path=Join-Path $dc $name
  if((Test-Path -LiteralPath $path) -and (Get-Content -LiteralPath $path -Raw).Trim() -eq ('Prometheus eject '+$State.id)){
   Remove-Item -LiteralPath $path -ErrorAction Stop
  }
 }
 Write-LifecycleJson (Join-Path $local 'last-eject.json') @{id=$State.id;drive=$State.drive;usb_instance=$State.usb_instance;result=$State.phase;resumed_because=$Reason;updated=(Get-Date).ToString('o')}
 Remove-Item -LiteralPath (Join-Path $local 'eject-mode.json') -ErrorAction Stop
 # Use the existing console-free host. Its runner mutex prevents duplicates.
 $quiet=Join-Path $local 'QuietBackground\Prometheus-QuietHost-v2.exe'
 if(Test-Path -LiteralPath $quiet){Start-Process -FilePath $quiet -ArgumentList 'desktop-commander' -WorkingDirectory (Split-Path $quiet) -WindowStyle Hidden|Out-Null}
 else{$runner=Join-Path $dc 'Run-DesktopCommander.ps1';if(Test-Path -LiteralPath $runner){$child=Start-LifecycleScript $runner;$child.Dispose()}}
}
