# Create-only reconnect enrollment. No optional schedules or automatic activation.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script:ReconnectNames=@('Desktop Commander Remote','Prometheus Memory Backup','Prometheus SSD Memory Backup','Prometheus Recovery Evidence Recorder','Prometheus Recovery Watchdog','Prometheus Recovery Watchdog At Logon','Prometheus USB Reconnect Supervisor','Prometheus Volume Watcher')
$script:ReconnectPayload=@('Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-Host-Agent.ps1')
function Get-EnrollmentHash([byte[]]$Bytes){$sha=[Security.Cryptography.SHA256]::Create();try{([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
function Get-EnrollmentAttributes([string]$Path){try{return [IO.File]::GetAttributes($Path)}catch [IO.FileNotFoundException]{return $null}catch [IO.DirectoryNotFoundException]{return $null}}
function Read-EnrollmentBytes([string]$Path,[int]$Limit){
 Assert-EnrollmentPath $Path
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{if($stream.Length -gt $Limit){throw 'Enrollment input exceeds byte limit'};$memory=[IO.MemoryStream]::new();try{$buffer=New-Object byte[] 4096;$total=0;while(($n=$stream.Read($buffer,0,[Math]::Min(4096,$Limit-$total+1))) -gt 0){$total+=$n;if($total -gt $Limit){throw 'Enrollment input grew beyond byte limit'};$memory.Write($buffer,0,$n)};return ,$memory.ToArray()}finally{$memory.Dispose()}}finally{$stream.Dispose()}
}
function Assert-EnrollmentObject($Value,[string[]]$Fields){
 if($Value -isnot [pscustomobject]){throw 'Expected enrollment object'}
 $names=@($Value.PSObject.Properties.Name)
 if($names.Count -ne $Fields.Count -or @($names|Where-Object{$_ -cnotin $Fields}).Count){throw 'Unexpected enrollment object fields'}
}
function Assert-EnrollmentManifestEntry($Value){
 Assert-EnrollmentObject $Value @('path','sha256','size_bytes')
   if($Value.path -isnot [string] -or $Value.sha256 -isnot [string] -or $Value.sha256 -cnotmatch '^[a-f0-9]{64}$' -or ($Value.size_bytes -isnot [int] -and $Value.size_bytes -isnot [long]) -or $Value.size_bytes -lt 1 -or $Value.size_bytes -gt 16777216){throw 'Malformed manifest entry'}
}
function Assert-EnrollmentPolicy($Value){
  Assert-EnrollmentObject $Value @('schema','mode','quiet_host','external_runner_sha256')
  if(($Value.schema -isnot [int] -and $Value.schema -isnot [long]) -or $Value.schema -ne 1 -or $Value.mode -isnot [string] -or $Value.mode -cne 'reconnect' -or $Value.quiet_host -isnot [string] -or $Value.quiet_host -cne 'Prometheus-QuietHost-v3.exe' -or $Value.external_runner_sha256 -isnot [string] -or $Value.external_runner_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid signed enrollment policy'}
}
function Assert-EnrollmentPath([string]$Path){
 $cursor=[IO.Path]::GetFullPath($Path)
 while($cursor){$attributes=Get-EnrollmentAttributes $cursor;if($null -ne $attributes -and ($attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse paths are not permitted for enrollment'};$parent=[IO.Path]::GetDirectoryName($cursor);if($parent -eq $cursor){break};$cursor=$parent}
}
function Assert-EnrollmentState($State){
 if(!$State.Complete -or $State.Paused -or $State.Active){throw 'Enrollment requires complete inventory, readable unpaused state and no active owned process'}
 foreach($task in @($State.Tasks)){if($script:ReconnectNames -icontains [string]$task.TaskName){throw 'Lifecycle task name already exists; create-only enrollment refuses migration'}}
}
function Invoke-ReconnectEnrollmentTransaction($Provider,$Context){
 $id=[guid]::NewGuid().ToString('N');$locks=$null;$journal=$null;$attempted=@();$completed=@();$inflight=$null;$taskAttempted=$false
 # Pure checks before lock acquisition and all writes.
 $package=& $Provider.Verify $Context
 Assert-EnrollmentState (& $Provider.State $Context)
 try{
  $locks=& $Provider.Lock $Context
  Assert-EnrollmentState (& $Provider.State $Context)
  $fresh=& $Provider.Verify $Context
  if($fresh.Identity -ne $package.Identity){throw 'Package changed during preflight'}
  $package=$fresh
  # Inspect every target before even the journal directory is written.
  $missing=@()
  foreach($file in @($package.Files)){$existing=& $Provider.Hash $Context $file.Name;if($null -eq $existing){$missing+=$file}else{if($existing -ne $file.Hash){throw 'Existing dependency differs; forward recovery required'}}}
  $journal=[ordered]@{id=$id;phase='prepared';task_was_absent=$true;created_files=@();package_identity=$package.Identity;task_enabled=$false;rollback_errors=@()}
  & $Provider.Journal $Context $journal
  & $Provider.Stage $Context $id $package
  Assert-EnrollmentState (& $Provider.State $Context)
  foreach($file in $missing){
   # Intent is durable before mutation; provider uses exclusive creation.
   $attempted+=$file;$journal.created_files=@($attempted|ForEach-Object{@{name=$_.Name;hash=$_.Hash}});$journal.phase='copying'
   & $Provider.Journal $Context $journal
   $inflight=$file; & $Provider.Copy $Context $id $file; $completed+=$file; $inflight=$null
   if((& $Provider.Hash $Context $file.Name) -ne $file.Hash){throw 'Installed dependency readback failed'}
  }
  Assert-EnrollmentState (& $Provider.State $Context)
  $journal.phase='registering';& $Provider.Journal $Context $journal
  $taskAttempted=$true
  & $Provider.Register $Context $id
  if(!(& $Provider.OwnedTask $Context $id)){throw 'Registered task does not match disabled owned definition'}
  foreach($file in @($package.Files)){if((& $Provider.Hash $Context $file.Name) -ne $file.Hash){throw 'Final dependency verification failed'}}
  $journal.phase='enrolled-disabled';& $Provider.Journal $Context $journal
  return [pscustomobject]@{id=$id;status='enrolled-disabled';activated=$false;rollback_complete=$null}
 }catch{
  $original=$_.Exception.Message
  if($journal){
   $errors=@(); if($null -ne $inflight){try{if($null -ne (& $Provider.Hash $Context $inflight.Name)){$errors+="Interrupted copy ownership is uncertain; preserve files"}}catch{$errors+="Interrupted copy state is unreadable"}}
   if($taskAttempted){try{& $Provider.RemoveTask $Context $id}catch{$errors+='Task rollback refused or failed'}}
   # Preserve dependencies if a possibly registered task could still reference them.
   if(!$errors.Count){for($i=$completed.Count-1;$i -ge 0;$i--){try{& $Provider.RemoveFile $Context $completed[$i]}catch{$errors+=('File rollback refused or failed: '+$completed[$i].Name)}}}
   $journal.phase=if($errors.Count){'rollback-incomplete'}else{'rolled-back'};$journal.rollback_errors=$errors
   try{& $Provider.Journal $Context $journal}catch{$errors+='Rollback journal could not be saved'}
   throw ('Enrollment failed: '+$original+'; rollback '+$(if($errors.Count){'incomplete; retain journal and artifacts'}else{'complete'}))
  }
  throw
 }finally{if($null -ne $locks){& $Provider.Unlock $Context $locks}}
}
function Get-ReconnectEnrollmentProvider {
 @{
 Verify={param($c)
  Assert-EnrollmentPath $c.Package;Assert-EnrollmentPath $c.Root
  $manifest=Read-EnrollmentBytes (Join-Path $c.Package 'SHA256-MANIFEST.json') 131072
  $certificate=[Security.Cryptography.X509Certificates.X509Certificate2]::new([byte[]](Read-EnrollmentBytes (Join-Path $c.Package 'MANIFEST-PUBLIC.cer') 16384))
  try{
   if(@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98') -notcontains $certificate.Thumbprint){throw 'Untrusted package signer'}
   $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
   try{if(!$rsa.VerifyData($manifest,(Read-EnrollmentBytes (Join-Path $c.Package 'SHA256-MANIFEST.sig') 8192),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)){throw 'Invalid package signature'}}finally{$rsa.Dispose()}
  }finally{$certificate.Dispose()}
  $text=[Text.UTF8Encoding]::new($false,$true).GetString($manifest).Trim()
  if(!$text.StartsWith('[') -or !$text.EndsWith(']')){throw 'Manifest must be an array'}
  $entries=@($text|ConvertFrom-Json);$seen=@{};$payload=@{};$policy=$null;$total=0
  $allowed=@($script:ReconnectPayload)+@('RECONNECT-ENROLLMENT.json')
  if($entries.Count -ne $allowed.Count){throw 'Manifest must contain exact enrollment closure'}
  $disk=@(Get-ChildItem -LiteralPath $c.Package -Force -ErrorAction Stop)
  $diskNames=@($allowed)+@('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')
  if($disk.Count -ne $diskNames.Count -or @($disk|Where-Object {$_.PSIsContainer -or $_.Name -cnotin $diskNames}).Count){throw 'Unexpected package content'}
  foreach($entry in $entries){
   Assert-EnrollmentManifestEntry $entry
   $name=$entry.path
   if($name -cnotin $allowed){throw 'Unexpected signed dependency'}
   if($name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $seen.ContainsKey($name)){throw 'Invalid or duplicate manifest path'}
   $seen[$name]=$true;$path=Join-Path $c.Package $name;Assert-EnrollmentPath $path
   $bytes=Read-EnrollmentBytes $path 16777216;$total+=$bytes.Length;if($total -gt 33554432){throw "Package exceeds total byte limit"};$hash=Get-EnrollmentHash $bytes
   if($hash -ne [string]$entry.sha256 -or $bytes.Length -ne [long]$entry.size_bytes){throw 'Signed package payload mismatch'}
   if($script:ReconnectPayload -contains $name){$payload[$name]=[pscustomobject]@{Name=$name;Hash=$hash;Bytes=$bytes}}
   if($name -eq 'RECONNECT-ENROLLMENT.json'){if($bytes.Length -gt 65536){throw 'Policy exceeds byte limit'};$policy=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)|ConvertFrom-Json}
  }
  if($payload.Count -ne $script:ReconnectPayload.Count -or !$policy){throw 'Signed reconnect dependency closure is incomplete'}
  Assert-EnrollmentPolicy $policy
  $runner=Join-Path $c.Local 'DesktopCommanderStartup\Run-DesktopCommander.ps1';Assert-EnrollmentPath $runner
  if((Get-EnrollmentHash (Read-EnrollmentBytes $runner 1048576)) -ne $policy.external_runner_sha256){throw 'External runner differs from reviewed identity'}
  # Existing host authorization is preserved, never minted by this enrollment.
  $auth=Join-Path $c.Root 'authorized-host.json';Assert-EnrollmentPath $auth
  $authBytes=Read-EnrollmentBytes $auth 65536
  if((Get-EnrollmentHash $authBytes) -ine [Text.Encoding]::ASCII.GetString((Read-EnrollmentBytes ($auth+'.sha256') 512)).Trim()){throw 'Host authorization integrity failed'}
  $machine=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid -ErrorAction Stop).MachineGuid
  if(([Text.Encoding]::UTF8.GetString($authBytes)|ConvertFrom-Json).fingerprint -ine (Get-EnrollmentHash ([Text.Encoding]::UTF8.GetBytes($machine)))){throw 'Host authorization belongs to a different computer'}
  [pscustomobject]@{Identity=(Get-EnrollmentHash $manifest);Files=@($script:ReconnectPayload|ForEach-Object{$payload[$_]})}
 }
 State={param($c)
  Assert-EnrollmentPath $c.Root
  $paused=$false;foreach($path in @((Join-Path $c.Root 'eject-mode.json'),(Join-Path $c.Local 'DesktopCommanderStartup\runner-paused.request'),(Join-Path $c.Root 'active-chat.shutdown'))){if($null -ne (Get-EnrollmentAttributes $path)){$paused=$true}}
  $tasks=@(Get-ScheduledTask -ErrorAction Stop)
  $active=$false
  foreach($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)){
   if($process.ProcessId -eq $PID){continue}
   $command=[string]$process.CommandLine;$exe=[string]$process.ExecutablePath
   if($command.IndexOf($c.Root,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or $exe.StartsWith($c.Root+'\',[StringComparison]::OrdinalIgnoreCase)){$active=$true}
   if(!$command -and $process.SessionId -eq [Diagnostics.Process]::GetCurrentProcess().SessionId -and $process.Name -match '^(powershell|pwsh|pythonw?)\.exe$'){throw 'Owned process activity cannot be determined'}
  }
  [pscustomobject]@{Complete=$true;Paused=$paused;Active=$active;Tasks=$tasks}
 }
 Lock={param($c)
  $locks=[Collections.Generic.List[object]]::new()
  try{foreach($name in @('Local\PrometheusReconnectEnrollment')+@(65..90|ForEach-Object{'Local\PrometheusEject-'+[char]$_})){
   $m=[Threading.Mutex]::new($false,$name);$held=$false
   try{try{$held=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true};if(!$held){throw 'Enrollment or eject operation already holds a mutex'};$locks.Add($m)}finally{if(!$held){$m.Dispose()}}
  };return ,$locks}catch{foreach($m in $locks){$m.ReleaseMutex();$m.Dispose()};throw}
 }
 Unlock={param($c,$locks)for($i=$locks.Count-1;$i -ge 0;$i--){$locks[$i].ReleaseMutex();$locks[$i].Dispose()}}
 Hash={param($c,$name)$p=Join-Path $c.Root $name;Assert-EnrollmentPath $p;if($null -eq (Get-EnrollmentAttributes $p)){return $null};Get-EnrollmentHash ([IO.File]::ReadAllBytes($p))}
 Journal={param($c,$j)
  $dir=Join-Path $c.Root ('Enrollment\'+$j.id);Assert-EnrollmentPath $dir;[IO.Directory]::CreateDirectory($dir)|Out-Null
  $path=Join-Path $dir 'journal.json';$tmp=$path+'.tmp';[IO.File]::WriteAllText($tmp,($j|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
  if([IO.File]::Exists($path)){[IO.File]::Replace($tmp,$path,[System.Management.Automation.Language.NullString]::Value)}else{[IO.File]::Move($tmp,$path)}
 }
 Stage={param($c,$id,$package)
  $dir=Join-Path $c.Root ('Enrollment\'+$id+'\stage');Assert-EnrollmentPath $dir;[IO.Directory]::CreateDirectory($dir)|Out-Null
  foreach($f in $package.Files){$path=Join-Path $dir $f.Name;$stream=[IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$stream.Write($f.Bytes,0,$f.Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()};if((Get-EnrollmentHash ([IO.File]::ReadAllBytes($path))) -ne $f.Hash){throw 'Staging readback failed'}}
 }
 Copy={param($c,$id,$file)
  $source=Join-Path $c.Root ('Enrollment\'+$id+'\stage\'+$file.Name);$target=Join-Path $c.Root $file.Name;Assert-EnrollmentPath $source;Assert-EnrollmentPath $target
  if((Get-EnrollmentHash ([IO.File]::ReadAllBytes($source))) -ne $file.Hash){throw 'Staged dependency changed'}
  [IO.File]::Copy($source,$target,$false)
 }
 Register={param($c,$id)
  $action=New-ScheduledTaskAction -Execute (Join-Path $c.Root 'Prometheus-QuietHost-v3.exe') -Argument 'reconnect' -WorkingDirectory $c.Root
  $principal=New-ScheduledTaskPrincipal -UserId $c.Sid -LogonType Interactive -RunLevel Limited
  $trigger=New-ScheduledTaskTrigger -AtLogOn -User $c.Sid
  $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  $settings.Enabled=$false
  Register-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description ('Prometheus reconnect enrollment '+$id) -ErrorAction Stop|Out-Null
 }
 OwnedTask={param($c,$id)Test-ReconnectOwnedTask $c $id}
 RemoveTask={param($c,$id)
  $tasks=@(Get-ScheduledTask -ErrorAction Stop|Where-Object {$_.TaskName -eq 'Prometheus USB Reconnect Supervisor' -and $_.TaskPath -eq '\'})
  if(!$tasks.Count){return};if(!(Test-ReconnectOwnedTask $c $id)){throw 'Task owner changed; rollback refuses removal'}
  Unregister-ScheduledTask -InputObject $tasks[0] -Confirm:$false -ErrorAction Stop
 }
 RemoveFile={param($c,$file)$path=Join-Path $c.Root $file.Name;Assert-EnrollmentPath $path;if($null -eq (Get-EnrollmentAttributes $path)){return};if((Get-EnrollmentHash ([IO.File]::ReadAllBytes($path))) -ne $file.Hash){throw 'File changed; rollback preserves it'};[IO.File]::Delete($path)}
 }
}
function Test-ReconnectOwnedTask($c,[string]$id){
 $tasks=@(Get-ScheduledTask -ErrorAction Stop|Where-Object {$_.TaskName -eq 'Prometheus USB Reconnect Supervisor' -and $_.TaskPath -eq '\'})
 if($tasks.Count -ne 1){return $false};$t=$tasks[0];$a=@($t.Actions);$triggers=@($t.Triggers)
 return $t.Description -ceq ('Prometheus reconnect enrollment '+$id) -and $a.Count -eq 1 -and $a[0].Execute -ieq (Join-Path $c.Root 'Prometheus-QuietHost-v3.exe') -and $a[0].Arguments -ceq 'reconnect' -and $a[0].WorkingDirectory -ieq $c.Root -and !$t.Settings.Enabled -and $t.Principal.UserId -ieq $c.Sid -and $t.Principal.RunLevel -eq 0 -and $t.Principal.LogonType -eq 3 -and $triggers.Count -eq 1 -and $triggers[0].CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $triggers[0].UserId -ieq $c.Sid -and $t.Settings.MultipleInstances -eq 2 -and !$t.Settings.DisallowStartIfOnBatteries -and !$t.Settings.StopIfGoingOnBatteries -and $t.Settings.StartWhenAvailable -and $t.Settings.ExecutionTimeLimit -eq 'PT0S'
}
function Start-ReconnectEnrollment {
 [CmdletBinding()]param([Parameter(Mandatory=$true)][string]$Package)
 $local=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $context=@{Local=$local;Root=(Join-Path $local 'Prometheus');Package=[IO.Path]::GetFullPath($Package);Sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}
 Invoke-ReconnectEnrollmentTransaction (Get-ReconnectEnrollmentProvider) $context
}
Export-ModuleMember -Function Start-ReconnectEnrollment
