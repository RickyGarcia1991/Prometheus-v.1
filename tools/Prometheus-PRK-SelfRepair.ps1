param(
 [ValidateSet('health','repair','simulate')][string]$Action='health',
 [string]$Drive,
 [string]$StageRoot,
 [string]$LocalPrometheusRoot,
 [switch]$AllowStateCleanup
)
$ErrorActionPreference='Stop'
function Get-KeyDrive {
 if($Drive){return $Drive.TrimEnd('\')}
 $x=Get-CimInstance Win32_LogicalDisk|Where-Object {Test-Path ($_.DeviceID+'\Prometheus-Recovery-Key\PRK-STATUS.json')}|Select-Object -First 1
 if(!$x){throw 'Prometheus Recovery Key not found.'};return $x.DeviceID
}
function Get-KeyRoot {return (Get-KeyDrive)+'\Prometheus-Recovery-Key'}
function Get-Manifest {
 $p=Join-Path (Get-KeyRoot) 'PRK-MANIFEST.json'
 if(!(Test-Path $p)){throw 'PRK manifest missing.'}
 return (Get-Content $p -Raw|ConvertFrom-Json)
}
function Test-SourceIntegrity {
 $root=Get-KeyRoot;$m=Get-Manifest;$bad=@();$done=0;$total=@($m.files).Count
 foreach($f in $m.files){$p=Join-Path $root $f.path;$done++;if(!(Test-Path $p) -or (Get-FileHash $p -Algorithm SHA256).Hash -ne $f.sha256){$bad+=$f.path}}
 [pscustomobject]@{name='integrity';percent=100;state=$(if($bad.Count){'error'}else{'ready'});detail=$(if($bad.Count){$bad.Count.ToString()+' protected file(s) failed verification'}else{$total.ToString()+' / '+$total+' protected files verified'});bad=$bad;files=$total}
}
function Roots {
 $local=if($LocalPrometheusRoot){$LocalPrometheusRoot}else{Join-Path $env:LOCALAPPDATA 'Prometheus'}
 $stage=if($StageRoot){$StageRoot}else{Join-Path $local 'Recovery\Staged-PRK'}
 [pscustomobject]@{local=$local;stage=$stage}
}
function Copy-VerifiedFile([string]$Relative,[string]$Destination) {
 $root=Get-KeyRoot;$m=Get-Manifest;$entry=@($m.files|Where-Object {$_.path -eq $Relative})|Select-Object -First 1
 if(!$entry){throw ('Protected source not in manifest: '+$Relative)}
 $src=Join-Path $root $Relative
 if(!(Test-Path $src)){throw ('Protected source missing: '+$Relative)}
 if((Get-FileHash $src -Algorithm SHA256).Hash -ne $entry.sha256){throw ('Protected source failed verification: '+$Relative)}
 New-Item -ItemType Directory -Force (Split-Path $Destination)|Out-Null
 Copy-Item $src $Destination -Force
 if((Get-FileHash $Destination -Algorithm SHA256).Hash -ne $entry.sha256){throw ('Post-repair verification failed: '+$Destination)}
}
function Get-Health {
 $r=Roots;$integrity=Test-SourceIntegrity;$root=Get-KeyRoot;$manifest=Get-Manifest
 $id=Test-Path (Join-Path $root 'PRK-STATUS.json')
 $tool=Test-Path (Join-Path $root 'tools\Prometheus-Recovery-Key.ps1')
 $payload=(Test-Path (Join-Path $root 'src')) -and (Test-Path (Join-Path $root 'tests'))
 $stageManifest=Join-Path $r.stage 'PRK-MANIFEST.json';$stageOk=$false;$stageBad=@();if(Test-Path $stageManifest){try{$sm=Get-Content $stageManifest -Raw|ConvertFrom-Json;foreach($sf in $sm.files){$sp=Join-Path $r.stage $sf.path;if(!(Test-Path $sp) -or (Get-FileHash $sp -Algorithm SHA256).Hash -ne $sf.sha256){$stageBad+=$sf.path}};$stageOk=($stageBad.Count -eq 0)}catch{$stageOk=$false;$stageBad+=('manifest-error: '+$_.Exception.Message)}}
 $hostSup=Join-Path $r.local 'Prometheus-USB-Supervisor.ps1';$supOk=Test-Path $hostSup
 $eject=Join-Path $r.local 'eject-mode.json';$pause=Join-Path (Split-Path $r.local) 'DesktopCommanderStartup\runner-paused.request'
 $stale=(Test-Path $eject) -or (Test-Path $pause)
 $repairable=($integrity.state -eq 'ready')
 $channels=@(
  [pscustomobject]@{name='identity';percent=$(if($id){100}else{0});state=$(if($id){'ready'}else{'error'});detail=$(if($id){'PRK identity verified'}else{'PRK status file missing'})},
  $integrity,
  [pscustomobject]@{name='tools';percent=$(if($tool){100}else{0});state=$(if($tool){'ready'}else{'error'});detail=$(if($tool){'Recovery toolset present'}else{'Recovery toolset missing'})},
  [pscustomobject]@{name='payload';percent=$(if($payload){100}else{0});state=$(if($payload){'ready'}else{'error'});detail=$(if($payload){'Source and tests payload present'}else{'Recovery payload incomplete'})},
  [pscustomobject]@{name='staging';percent=$(if($stageOk){100}else{25});state=$(if($stageOk){'ready'}else{'repairable'});detail=$(if($stageOk){'Staged recovery verified'}else{[string]$stageBad.Count+' staged file(s) missing or corrupt'})},
  [pscustomobject]@{name='bootstrap';percent=$(if($supOk){100}else{0});state=$(if($supOk){'ready'}else{'repairable'});detail=$(if($supOk){'Host supervisor present'}else{'Host supervisor can be restored from verified PRK'})},
  [pscustomobject]@{name='handoff';percent=$(if(!$stale){100}else{50});state=$(if(!$stale){'ready'}else{'repairable'});detail=$(if(!$stale){'No stale eject state detected'}else{'Stale eject/pause state detected'})},
  [pscustomobject]@{name='self_repair';percent=$(if($repairable){100}else{0});state=$(if($repairable){'armed'}else{'blocked'});detail=$(if($repairable){'Verified source available for bounded repairs'}else{'Blocked: PRK source integrity failed'})}
 )
 [pscustomobject]@{timestamp=(Get-Date).ToString('o');drive=(Get-KeyDrive);protected_files=@($manifest.files).Count;source_verified=($integrity.state -eq 'ready');channels=$channels}
}
function Write-RepairLog($Record) {
 $r=Roots;$logDir=Join-Path $r.local 'Recovery';New-Item -ItemType Directory -Force $logDir|Out-Null
 $line=$Record|ConvertTo-Json -Depth 12 -Compress
 Add-Content -Encoding UTF8 (Join-Path $logDir 'self-repair.log') $line
}
function Invoke-Repair {
 $before=Get-Health
 if(!$before.source_verified){$result=[pscustomobject]@{timestamp=(Get-Date).ToString('o');action='repair';success=$false;state='blocked';message='Self-repair blocked because protected PRK source integrity failed.';before=$before;actions=@();after=$before};Write-RepairLog $result;return $result}
 $r=Roots;$actions=@()
 $stageState=@($before.channels|Where-Object {$_.name -eq 'staging'})|Select-Object -First 1
 if(!$stageState -or $stageState.state -ne 'ready'){
  if(Test-Path $r.stage){Remove-Item $r.stage -Recurse -Force}
  New-Item -ItemType Directory -Force $r.stage|Out-Null
  Copy-Item ((Get-KeyRoot)+'\*') $r.stage -Recurse -Force
  $actions+='rebuilt_staging'
 }
 $supRel='tools\Prometheus-USB-Supervisor.ps1';$supDest=Join-Path $r.local 'Prometheus-USB-Supervisor.ps1'
 $m=Get-Manifest;$supEntry=@($m.files|Where-Object {$_.path -eq $supRel})|Select-Object -First 1
 if($supEntry){
  $need=!(Test-Path $supDest)
  if(!$need){$need=((Get-FileHash $supDest -Algorithm SHA256).Hash -ne $supEntry.sha256)}
  if($need){Copy-VerifiedFile $supRel $supDest;$actions+='restored_host_supervisor'}
 }
 if($AllowStateCleanup){
  $eject=Join-Path $r.local 'eject-mode.json';$pause=Join-Path (Split-Path $r.local) 'DesktopCommanderStartup\runner-paused.request'
  if(Test-Path $eject){Remove-Item $eject -Force;$actions+='cleared_stale_eject_lock'}
  if(Test-Path $pause){Remove-Item $pause -Force;$actions+='cleared_stale_commander_pause'}
 }
 $after=Get-Health
 $failed=@($after.channels|Where-Object {$_.state -in @('error','blocked','repairable')})
 $result=[pscustomobject]@{timestamp=(Get-Date).ToString('o');action='repair';success=($failed.Count -eq 0);state=$(if($failed.Count){'error'}else{'ready'});message=$(if($actions.Count){'Bounded self-repair completed and re-verified.'}else{'No repair was required.'});actions=$actions;before=$before;after=$after}
 Write-RepairLog $result
 return $result
}
if($Action -eq 'health'){Get-Health|ConvertTo-Json -Depth 8;exit}
if($Action -eq 'repair'){Invoke-Repair|ConvertTo-Json -Depth 10;exit}
if($Action -eq 'simulate'){Get-Health|ConvertTo-Json -Depth 8;exit}
