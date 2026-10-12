param([ValidateSet('Install','Repair','Uninstall','Verify')][string]$Mode='Install',[switch]$Elevated)
$ErrorActionPreference='Stop'
$HostRoot=Join-Path $env:LOCALAPPDATA 'Prometheus';$Agent=Join-Path $HostRoot 'Prometheus-Host-Agent.ps1';$Watcher=Join-Path $HostRoot 'Prometheus-Volume-Watcher.ps1';$Recovery=Join-Path $HostRoot 'Prometheus-Security-Recovery.ps1';$AuthorizedHost=Join-Path $HostRoot 'authorized-host.json'
$WatcherTask='Prometheus Volume Watcher';$Obsolete=@('Prometheus Host Agent','Prometheus Host Agent Logon','Prometheus Device Support')
function Admin{$p=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent();$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}
function Elevate{if(Admin){return};if($Elevated){throw 'Elevation failed'};$a="-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Mode $Mode -Elevated";$p=Start-Process powershell.exe -Verb RunAs -ArgumentList $a -Wait -PassThru;exit $p.ExitCode}
function SourceRoot{$candidate=Join-Path (Split-Path $PSCommandPath -Parent) 'Prometheus-Host-Agent-v0.6.8';if(Test-Path $candidate){return $candidate};return (Split-Path $PSCommandPath -Parent)}
function HostFingerprint{$id=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid;$sha=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($id))).Replace('-',''))}finally{$sha.Dispose()}}
function New-HostAgentEjectMutex([string]$DriveLetter) {
 return [Threading.Mutex]::new($false,('Local\PrometheusEject-'+$DriveLetter))
}
function Test-HostAgentTaskOwnership($Task,[string]$Root) {
 $actions=@($Task.Actions)
 if($actions.Count -ne 1){return $false}
 $execute=[string]$actions[0].Execute;$arguments=([string]$actions[0].Arguments).Trim()
 $quietModes=@{'Prometheus USB Reconnect Supervisor'='reconnect';'Prometheus Volume Watcher'='volume-watcher'}
 try{$absoluteExe=[IO.Path]::GetFullPath($execute)}catch{return $false}
 if($quietModes.ContainsKey([string]$Task.TaskName) -and $absoluteExe -ieq [IO.Path]::GetFullPath((Join-Path $Root 'Prometheus-QuietHost-v2.exe')) -and $arguments -ieq $quietModes[[string]$Task.TaskName]){return $true}
 $scripts=@{
  'Prometheus Volume Watcher'='Prometheus-Volume-Watcher.ps1'
  'Prometheus Host Agent'='Prometheus-Host-Agent.ps1'
  'Prometheus Host Agent Logon'='Prometheus-Host-Agent.ps1'
  'Prometheus Device Support'='Prometheus-Device-Support.ps1'
  'Prometheus Device Support Logon'='Prometheus-Device-Support.ps1'
 }
 $windowsPowerShell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
 if(!$scripts.ContainsKey([string]$Task.TaskName) -or ($execute -ine 'powershell.exe' -and $absoluteExe -ine $windowsPowerShell)){return $false}
 $scriptPath=[IO.Path]::GetFullPath((Join-Path $Root $scripts[[string]$Task.TaskName]))
 $suffixes=@('');if($Task.TaskName -in @('Prometheus Host Agent','Prometheus Host Agent Logon')){$suffixes+= ' -Mode Run'}
 # Accept only known installer argument forms and the exact owned script path.
 # Additional commands, another host root, or unfamiliar task actions are refused.
 foreach($prefix in @('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "','-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "','-NoProfile -ExecutionPolicy Bypass -File "')){
  foreach($suffix in $suffixes){if($arguments -ieq ($prefix+$scriptPath+'"'+$suffix)){return $true}}
 }
 return $false
}
function Invoke-HostAgentUninstall([string]$Root) {
 $eject=Join-Path $Root 'eject-mode.json'
 if(Test-Path -LiteralPath $eject -ErrorAction Stop){throw 'Finish or cancel the active SSD eject before uninstalling the Host Agent.'}
 $locks=[Collections.Generic.List[object]]::new()
 try {
  # The user-level supervisor supports every drive letter. Hold the same locks
  # used by the controller so an eject cannot begin during dependency removal.
  foreach($letter in 65..90){
   $mutex=New-HostAgentEjectMutex ([string][char]$letter)
   try {
    try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
    if(!$held){throw 'An SSD eject operation is running; finish it before uninstalling the Host Agent.'}
    $locks.Add($mutex);$mutex=$null
   } finally {if($mutex){$mutex.Dispose()}}
  }
  if(Test-Path -LiteralPath $eject -ErrorAction Stop){throw 'Finish or cancel the active SSD eject before uninstalling the Host Agent.'}
  # Enumerate before filtering so an empty root folder is not mistaken for a
  # query failure. A denied query must not count as successful uninstallation.
  $registered=@(Get-ScheduledTask -ErrorAction Stop|Where-Object TaskPath -eq '\')
  $names=@('Prometheus USB Reconnect Supervisor','Prometheus Volume Watcher','Prometheus Host Agent','Prometheus Host Agent Logon','Prometheus Device Support','Prometheus Device Support Logon')
  # Establish ownership for every candidate before changing even the first task.
  foreach($name in $names){
   $matches=@($registered|Where-Object TaskName -eq $name)
   if($matches.Count -gt 1 -or ($matches.Count -eq 1 -and !(Test-HostAgentTaskOwnership $matches[0] $Root))){
    throw ('Task '+$name+' does not match this Host Agent installation; no tasks were changed and agent files were preserved.')
   }
  }
  foreach($name in $names){
   foreach($task in @($registered|Where-Object TaskName -eq $name)){
    # The recovery watchdog skips disabled tasks. Disable before stopping to
    # prevent it from restarting the supervisor between stop and unregister.
    Disable-ScheduledTask -InputObject $task -ErrorAction Stop|Out-Null
    Stop-ScheduledTask -InputObject $task -ErrorAction Stop
    Unregister-ScheduledTask -InputObject $task -Confirm:$false -ErrorAction Stop
   }
  }
  $remaining=@(Get-ScheduledTask -ErrorAction Stop|Where-Object {$_.TaskPath -eq '\' -and $_.TaskName -in $names})
  if($remaining.Count){throw 'Host Agent tasks remain registered; required agent files were preserved.'}
  # Keep lifecycle utilities used by the cached controller, Desktop Commander,
  # independent backups, diagnostics, and all personal data.
  foreach($name in @('Prometheus-Host-Agent.ps1','Prometheus-Volume-Watcher.ps1','Prometheus-Security-Recovery.ps1','authorized-host.json','authorized-host.json.sha256')){
   $path=Join-Path $Root $name
   if(Test-Path -LiteralPath $path -ErrorAction Stop){Remove-Item -LiteralPath $path -Force -ErrorAction Stop}
  }
 } finally {
  for($index=$locks.Count-1;$index -ge 0;$index--){try{$locks[$index].ReleaseMutex()}finally{$locks[$index].Dispose()}}
 }
}
if($Mode -eq 'Verify'){& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Agent -Mode Verify;exit $LASTEXITCODE}
Elevate
if($Mode -eq 'Uninstall'){Invoke-HostAgentUninstall $HostRoot;Write-Output 'Prometheus Host Agent uninstalled.';exit 0}
$src=SourceRoot;$srcAgent=Join-Path $src 'Prometheus-Host-Agent.ps1';$srcWatcher=Join-Path $src 'Prometheus-Volume-Watcher.ps1';$srcRecovery=Join-Path $src 'Prometheus-Security-Recovery.ps1';if(!(Test-Path $srcAgent) -or !(Test-Path $srcWatcher) -or !(Test-Path $srcRecovery)){throw 'Host Agent package is incomplete'}
$manifest=Join-Path $src 'SHA256-MANIFEST.json';$sigPath=Join-Path $src 'SHA256-MANIFEST.sig';$certPath=Join-Path $src 'MANIFEST-PUBLIC.cer';if(!(Test-Path $manifest) -or !(Test-Path $sigPath) -or !(Test-Path $certPath)){throw 'Signed Host Agent package required'}
$cert=New-Object Security.Cryptography.X509Certificates.X509Certificate2($certPath);if(@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98') -notcontains $cert.Thumbprint){throw 'Host Agent signing certificate is not trusted'};$rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert);try{$ok=$rsa.VerifyData([IO.File]::ReadAllBytes($manifest),[IO.File]::ReadAllBytes($sigPath),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)}finally{$rsa.Dispose();$cert.Dispose()};if(!$ok){throw 'Host Agent manifest signature invalid'}
foreach($e in (Get-Content $manifest -Raw|ConvertFrom-Json)){$f=Join-Path $src ([string]$e.path);if(!(Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne ([string]$e.sha256)){throw ('Host Agent integrity failure: '+$e.path)}}
$tokens=$null;$parseErrors=$null;[Management.Automation.Language.Parser]::ParseFile($srcAgent,[ref]$tokens,[ref]$parseErrors)|Out-Null;if($parseErrors.Count){throw 'Host Agent syntax validation failed'};$tokens=$null;$watchErrors=$null;[Management.Automation.Language.Parser]::ParseFile($srcWatcher,[ref]$tokens,[ref]$watchErrors)|Out-Null;if($watchErrors.Count){throw 'Volume Watcher syntax validation failed'};$tokens=$null;$recoveryErrors=$null;[Management.Automation.Language.Parser]::ParseFile($srcRecovery,[ref]$tokens,[ref]$recoveryErrors)|Out-Null;if($recoveryErrors.Count){throw 'Security Recovery syntax validation failed'}
New-Item -ItemType Directory -Force $HostRoot|Out-Null;$state=Join-Path $HostRoot 'security-state.json';if((Test-Path $state) -and !(Test-Path ($state+'.sha256'))){try{[void]([version]([string](Get-Content $state -Raw|ConvertFrom-Json).highest_controller_version));(Get-FileHash $state -Algorithm SHA256).Hash|Set-Content ($state+'.sha256') -Encoding ASCII}catch{throw 'Existing security state cannot be safely migrated'}};$audit=Join-Path $HostRoot 'security-audit.jsonl';if((Test-Path $audit) -and !(Test-Path ($AuthorizedHost+'.sha256'))){Move-Item $audit (Join-Path $HostRoot ('security-audit-legacy-v061-'+(Get-Date -Format 'yyyyMMddHHmmss')+'.jsonl')) -Force};Copy-Item $srcAgent $Agent -Force;Copy-Item $srcWatcher $Watcher -Force;Copy-Item $srcRecovery $Recovery -Force;[ordered]@{fingerprint=(HostFingerprint);authorized=(Get-Date -Format o);method='UAC-approved-install'}|ConvertTo-Json|Set-Content $AuthorizedHost -Encoding UTF8;(Get-FileHash $AuthorizedHost -Algorithm SHA256).Hash|Set-Content ($AuthorizedHost+'.sha256') -Encoding ASCII
foreach($n in @($WatcherTask)+$Obsolete){$task=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;if($task){Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;Unregister-ScheduledTask -TaskName $n -Confirm:$false}}
$support=@('Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-QuietHost-v2.exe','Install-Lifecycle-Tasks.ps1','host-memory-backup.ps1','ssd-memory-backup.ps1')
foreach($name in $support){$file=Join-Path $src $name;if(!(Test-Path -LiteralPath $file)){throw ('Lifecycle package is incomplete: '+$name)};Copy-Item -LiteralPath $file -Destination (Join-Path $HostRoot $name) -Force}
[IO.Directory]::CreateDirectory((Join-Path $HostRoot 'Diagnostics'))|Out-Null
foreach($name in @('Prometheus-Recovery-Watchdog.ps1','Prometheus-Recovery-Recorder.ps1')){Copy-Item -LiteralPath (Join-Path $src $name) -Destination (Join-Path $HostRoot ('Diagnostics\'+$name)) -Force}
& (Join-Path $HostRoot 'Install-Lifecycle-Tasks.ps1')
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Agent -Mode Verify;if($LASTEXITCODE -ne 0){throw 'Installed Host Agent verification failed'}
Write-Output ('Prometheus Host Agent '+$Mode.ToLowerInvariant()+' completed.')
