# Inspect-only collector: no task/process mutation, repair, enrollment, or receipt creation.
param()
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$locks=[Collections.Generic.List[IO.Stream]]::new();$stage='context';$context=$null;$physicalHandle=$null
function Read-Locked([string]$Path,[int]$Limit=1048576){
 $item=Get-Item -LiteralPath $Path -Force
 while($item){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse context/input refused'};if($item -is [IO.DirectoryInfo]){$item=$item.Parent}else{$item=$item.Directory}}
 $stream=[IO.File]::Open($Path,'Open','Read','Read');$locks.Add($stream)
 if('PrometheusUpgradePhysicalRoot' -as [type]){
  $final=[Text.StringBuilder]::new(32768);$length=[PrometheusUpgradePhysicalRoot]::GetFinalPathNameByHandle($stream.SafeFileHandle,$final,32768,0)
  if(!$length -or $length -ge 32768 -or $final.ToString() -ine ('\\?\'+[IO.Path]::GetFullPath($Path))){if($context){$context.classification='msix-or-file-redirection-refused'};throw 'File handle resolves elsewhere; MSIX/file redirection refused'}
 }

 if($stream.Length -gt $Limit){throw 'Input exceeds inspection bound'}
 $memory=[IO.MemoryStream]::new();try{$stream.CopyTo($memory);return ,$memory.ToArray()}finally{$memory.Dispose()}
}
function Hash-Bytes([byte[]]$Bytes){[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant()}
function Text-Bytes([byte[]]$Bytes){[Text.UTF8Encoding]::new($false,$true).GetString($Bytes)}
function Resolve-Sid([string]$Account){if($Account -match '^S-1-'){return ([Security.Principal.SecurityIdentifier]::new($Account)).Value};return ([Security.Principal.NTAccount]::new($Account)).Translate([Security.Principal.SecurityIdentifier]).Value}
function Test-WorkerCreationStamp([long]$NativeTicks,[long]$CimTicks){
 # CIM_DATETIME has microsecond precision. Preserve integer math for 100ns ticks.
 $delta=$NativeTicks-$CimTicks
 return $NativeTicks -gt 0 -and $CimTicks -gt 0 -and $delta -ge 0 -and $delta -le 9
}
function Test-ActiveMigrationTaskShape($Task,[string]$Root,[string]$Sid){
 try{
  $actions=@($Task.Actions);$triggers=@($Task.Triggers)
  if($actions.Count -ne 1 -or $triggers.Count -ne 1){return $false}
  $trigger=$triggers[0]
  if($trigger.Enabled -isnot [bool] -or !$trigger.Enabled){return $false}
  return [bool]($actions[0].Execute -ieq (Join-Path $Root 'Prometheus-QuietHost-v3.exe') -and $actions[0].Arguments -ceq 'reconnect' -and $actions[0].WorkingDirectory -ieq $Root -and (Resolve-Sid $Task.Principal.UserId) -ceq $Sid -and $Task.Principal.RunLevel -eq 0 -and $Task.Principal.LogonType -eq 3 -and $trigger.CimClass.CimClassName -ceq 'MSFT_TaskLogonTrigger' -and (Resolve-Sid $trigger.UserId) -ceq $Sid -and $Task.Settings.MultipleInstances -eq 2 -and !$Task.Settings.DisallowStartIfOnBatteries -and !$Task.Settings.StopIfGoingOnBatteries -and $Task.Settings.StartWhenAvailable -and $Task.Settings.ExecutionTimeLimit -ceq 'PT0S')
 }catch{return $false}
}
try{
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $profiles=@(Get-CimInstance Win32_UserProfile -Filter ("SID='"+$sid+"'"))
 if($profiles.Count -ne 1 -or !$profiles[0].LocalPath){throw 'Unique current SID profile unavailable'}
 $profile=[IO.Path]::GetFullPath([string]$profiles[0].LocalPath).TrimEnd('\')
 $physical=Join-Path $profile 'AppData\Local';$reported=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $root=Join-Path $physical 'Prometheus'
 $classification=if([IO.Path]::GetFullPath($reported).TrimEnd('\') -ieq $physical){'physical-profile'}elseif($reported -like '*\Packages\*'){'msix-redirected-profile'}else{'profile-path-mismatch'}
 $context=@{sid=$sid;session_id=[Diagnostics.Process]::GetCurrentProcess().SessionId;profile_root=$profile;physical_local=$physical;reported_local=$reported;root=$root;classification=$classification}
 if($classification -ne 'physical-profile'){throw 'Run inspection from the real current-user profile; redirected context is not installed authority'}
 foreach($path in @($profile,$physical,$root)){$item=Get-Item -LiteralPath $path -Force;while($item){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse profile refused'};$item=$item.Parent}}
 $physicalGuard=Join-Path $PSScriptRoot 'read-controller-production-preflight.ps1'
 if((Hash-Bytes (Read-Locked $physicalGuard)) -cne '1a4d7171fd05bb4bb4e33fb48d5d66a5a99c0877d2ed908dc4d099206ff55d57'){throw 'Pinned physical context guard changed'}
 $physicalAst=[Management.Automation.Language.Parser]::ParseFile($physicalGuard,[ref]$null,[ref]$null)
 $physicalFunction=@($physicalAst.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-PhysicalEnrollmentRoot'},$false))
 if($physicalFunction.Count -ne 1){throw 'Physical context guard missing'};. ([scriptblock]::Create($physicalFunction[0].Extent.Text))
 try{$physicalHandle=Assert-PhysicalEnrollmentRoot $root}catch{$context.classification='msix-or-physical-redirection-refused';throw}
 $stage='packages'
 $old=Join-Path $PSScriptRoot 'reconnect-enrollment-candidate-r5';$new=Join-Path $PSScriptRoot 'reconnect-enrollment-candidate-r7'
 $r5='b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e';$r7='6e26224389829b4f9cfb3288b8f9495d0e285e9fc96a322f3db25e82ecddc01e'
 $qualifier=Join-Path $PSScriptRoot 'qualify-reconnect-enrollment-v2.ps1'
 if((Hash-Bytes (Read-Locked $qualifier)) -cne '9e46ac586172efcf43e82ce0dab9b56c888cf967c614e27ba5214210b2daa0fe'){throw 'Pinned qualifier changed'}
 foreach($pkg in @($old,$new)){foreach($file in Get-ChildItem -LiteralPath $pkg -File){$null=Read-Locked $file.FullName 16777216}}
 $ov=(& $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $old -ExpectedManifestSha256 $r5)|ConvertFrom-Json
 $nv=(& $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $new -ExpectedManifestSha256 $r7)|ConvertFrom-Json
 $oldRows=@(Get-Content (Join-Path $old 'SHA256-MANIFEST.json') -Raw|ConvertFrom-Json);$newRows=@(Get-Content (Join-Path $new 'SHA256-MANIFEST.json') -Raw|ConvertFrom-Json)
 $changed=@($oldRows|Where-Object {$row=$_;$match=@($newRows|Where-Object path -CEQ $row.path);$match.Count -ne 1 -or $match[0].sha256 -cne $row.sha256 -or $match[0].size_bytes -ne $row.size_bytes})
 if(!$ov.signature_verified -or !$nv.signature_verified -or $ov.files -ne 7 -or $nv.files -ne 7 -or $changed.Count -ne 1 -or $changed[0].path -cne 'Prometheus-Host-Agent.ps1'){throw 'Exact signed HostAgent-only delta required'}
 $modulePath=Join-Path $old 'ReconnectEnrollment.psm1'
 if((Hash-Bytes (Read-Locked $modulePath)) -cne '3df45e228e82e6a72509c2c2f4fb4301836c1a3b2a70bbc497feda2406ba1509'){throw 'Pinned enrollment module changed'}
 $module=Import-Module $modulePath -Force -PassThru
 $stage='task'
 $tasks=@(Get-ScheduledTask);$taskSet=@($tasks|Where-Object {$_.TaskName -ceq 'Prometheus USB Reconnect Supervisor' -and $_.TaskPath -ceq '\'})
 if($taskSet.Count -ne 1){throw 'Unique existing root supervisor task required'}
 $t=$taskSet[0];$description=[string]$t.Description
 if($description -cnotmatch '^Prometheus reconnect enrollment ([a-f0-9]{32})$'){throw 'Task does not identify original enrollment'}
 $id=$Matches[1]
 $shape=Test-ActiveMigrationTaskShape $t $root $sid
 # XML digest captures the current definition. Original R5 receipt has no XML digest.
 $xml=Export-ScheduledTask -TaskName $t.TaskName -TaskPath '\'
 $task=@{name=[string]$t.TaskName;path=[string]$t.TaskPath;enrollment_id=$id;config_sha256=(Hash-Bytes ([Text.Encoding]::UTF8.GetBytes($xml)));enabled=[bool]$t.Settings.Enabled;state=[string]$t.State;owned_shape=[bool]$shape}
 $stage='original-receipt'
 $receiptPath=Join-Path $root ('Enrollment\'+$id+'\journal.json');$receiptBytes=Read-Locked $receiptPath 131072
 $journal=& $module {param($root,$id,$pin) Read-EnrollmentJournal @{Root=$root} $id $pin} $root $id $r5
 & $module {param($j) Assert-EnrollmentObject $j @('id','phase','task_was_absent','created_files','completed_files','package_identity','task_enabled','rollback_errors')} $journal
 $activation=Join-Path $PSScriptRoot 'activate-enrolled-reconnect.ps1'
 if((Hash-Bytes (Read-Locked $activation)) -cne '5bd020db3ca7ded6c8ab4a52eb168cc904ba28b1f050076a17e34e642dda9a97'){throw 'Pinned activation guard changed'}
 $ast=[Management.Automation.Language.Parser]::ParseFile($activation,[ref]$null,[ref]$null)
 $guard=@($ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-ActivationJournal'},$false))
 if($guard.Count -ne 1){throw 'Original receipt guard missing'};. ([scriptblock]::Create($guard[0].Extent.Text))
 $payloadNames=@('Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-Host-Agent.ps1')
 $verified=[pscustomobject]@{Identity=$r5;Files=@($oldRows|Where-Object {$_.path -cin $payloadNames}|ForEach-Object {[pscustomobject]@{Name=$_.path;Hash=$_.sha256}})}
 Assert-ActivationJournal $journal $verified $id
 $receipt=@{id=$id;sha256=(Hash-Bytes $receiptBytes);phase=[string]$journal.phase;package=[string]$journal.package_identity;authenticated=$true}
 $stage='authorization'
 $authBytes=Read-Locked (Join-Path $root 'authorized-host.json') 65536;$authHash=Hash-Bytes $authBytes
 $sidecar=([Text.Encoding]::ASCII.GetString((Read-Locked (Join-Path $root 'authorized-host.json.sha256') 512))).Trim()
 $authObject=(Text-Bytes $authBytes)|ConvertFrom-Json
 $machine=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
 $hostHash=Hash-Bytes ([Text.Encoding]::UTF8.GetBytes($machine))
 $auth=@{sha256=$authHash;sidecar_matches=($authHash -ceq $sidecar.ToLowerInvariant());host_matches=([string]$authObject.fingerprint -ceq $hostHash)}
 $stage='runtime'
 $runtime=@(foreach($name in $payloadNames){$o=@($oldRows|Where-Object path -CEQ $name)[0];$n=@($newRows|Where-Object path -CEQ $name)[0];@{name=$name;sha256=(Hash-Bytes (Read-Locked (Join-Path $root $name) 16777216));r5_sha256=$o.sha256;r7_sha256=$n.sha256}})
 $stage='workers'
 $workers=@();$all=@(Get-CimInstance Win32_Process);if($all.Count -gt 8192){throw 'Process inventory exceeds bound'}
 $shell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
 foreach($w in $all){
  if($w.ProcessId -eq $PID){continue}
  $cmd=[string]$w.CommandLine;$image=[string]$w.ExecutablePath
  if(!$cmd -and $w.SessionId -eq $context.session_id -and $w.Name -match '^(powershell|pwsh|pythonw?|Prometheus-QuietHost-v3)\.exe$'){throw 'Relevant process command line unreadable'}
  $kind=$null
  if($image -ieq (Join-Path $root 'Prometheus-QuietHost-v3.exe') -and $cmd -match '\breconnect\s*$'){$kind='wrapper'}
  foreach($pair in @(@('supervisor','Prometheus-USB-Reconnect-Supervisor.ps1'),@('arrival','Prometheus-Arrival.ps1'),@('host-agent','Prometheus-Host-Agent.ps1'))){if($cmd.IndexOf((Join-Path $root $pair[1]),[StringComparison]::OrdinalIgnoreCase) -ge 0){$kind=$pair[0]}}
  if(!$kind){continue}
  $owner=Invoke-CimMethod -InputObject $w -MethodName GetOwnerSid;if($owner.ReturnValue -ne 0){throw 'Worker owner unavailable'}
  $p=[Diagnostics.Process]::GetProcessById([int]$w.ProcessId)
  try{$ticks=$p.StartTime.ToUniversalTime().Ticks;if($p.MainModule.FileName -ine $image -or !(Test-WorkerCreationStamp $ticks $w.CreationDate.ToUniversalTime().Ticks)){throw 'Worker identity changed during capture'}}finally{$p.Dispose()}
  $commandValid=$false
  if($kind -eq 'wrapper'){$expected='"'+(Join-Path $root 'Prometheus-QuietHost-v3.exe')+'" reconnect';$commandValid=$cmd -ieq $expected -or $cmd -ieq ($image+' reconnect')}
  if($kind -eq 'supervisor'){$expected='"'+$shell+'" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $root 'Prometheus-USB-Reconnect-Supervisor.ps1')+'"';$commandValid=$image -ieq $shell -and $cmd -ieq $expected}
  $workers+=@(@{kind=$kind;pid=[long]$w.ProcessId;parent_pid=[long]$w.ParentProcessId;creation_ticks=$ticks;sid=[string]$owner.Sid;session_id=[int]$w.SessionId;image=$image;image_sha256=(Hash-Bytes (Read-Locked $image 16777216));command_valid=[bool]$commandValid})
 }
 $stage='pause-and-migration-evidence'
 $pause=@();foreach($path in @((Join-Path $root 'eject-mode.json'),(Join-Path $root 'active-chat.shutdown'),(Join-Path $physical 'DesktopCommanderStartup\runner-paused.request'))){if(Test-Path -LiteralPath $path){$pause+=@($path)}}
 $migration=@();$migrationRoot=Join-Path $root 'ActiveHostMigration'
 if(Test-Path -LiteralPath $migrationRoot){$entries=@(Get-ChildItem -LiteralPath $migrationRoot -Force);if($entries.Count -gt 128){throw 'Migration evidence inventory exceeds bound'};$migration=@($entries|ForEach-Object {$_.Name})}
 $conflicting=@($tasks|Where-Object {$_.TaskName -in @('Prometheus Volume Watcher','Prometheus Recovery Watchdog','Prometheus Recovery Watchdog At Logon') -and $_.Settings.Enabled}|ForEach-Object {$_.TaskPath+$_.TaskName})
 if((Export-ScheduledTask -TaskName $t.TaskName -TaskPath '\') -cne $xml){throw 'Task definition changed during observation'}
 @{schema=1;collected=$true;observed_utc=[DateTime]::UtcNow.ToString('o');context=$context;packages=@{r5=$r5;r7=$r7;entries=7;unchanged=6};receipt=$receipt;auth=$auth;task=$task;runtime=$runtime;workers=$workers;pause_markers=$pause;migration_evidence=$migration;competing_lifecycle_tasks=$conflicting}|ConvertTo-Json -Depth 8 -Compress
}catch{
 @{schema=1;collected=$false;stage=$stage;error=$_.Exception.Message;context=$context}|ConvertTo-Json -Depth 5 -Compress
}finally{foreach($stream in $locks){$stream.Dispose()};if($physicalHandle){$physicalHandle.Dispose()}}


