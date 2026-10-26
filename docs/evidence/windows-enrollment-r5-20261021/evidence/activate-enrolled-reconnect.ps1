param([Parameter(Mandatory)][string]$Package,[Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestHash,[Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$TransactionId)
$ErrorActionPreference='Stop'
function Assert-ActivationJournal($j,$verified,[string]$id){
 if($j.id -cne $id -or $j.package_identity -cne $verified.Identity -or $j.phase -cne 'enrolled-disabled' -or $j.task_was_absent -isnot [bool] -or !$j.task_was_absent -or $j.task_enabled -isnot [bool] -or $j.task_enabled){throw 'Completed disabled enrollment required'}
 if($j.created_files -isnot [array] -or $j.completed_files -isnot [array] -or $j.rollback_errors -isnot [array] -or $j.rollback_errors.Count -ne 0 -or $j.created_files.Count -ne $j.completed_files.Count -or $j.created_files.Count -gt $verified.Files.Count){throw 'Incomplete enrollment file history'}
 $seen=@{}
 foreach($row in $j.created_files){
  if($row.name -isnot [string] -or $row.hash -isnot [string] -or $seen.ContainsKey($row.name)){throw 'Malformed enrollment file history'}
  $seen[$row.name]=$true
  if(@($verified.Files|Where-Object {$_.Name -ceq $row.name -and $_.Hash -ceq $row.hash}).Count -ne 1 -or @($j.completed_files|Where-Object {$_.name -ceq $row.name -and $_.hash -ceq $row.hash}).Count -ne 1){throw 'Enrollment file history differs from signed package'}
 }
}
$moduleLock=$null;$qualifierLock=$null
$payloadLocks=[Collections.Generic.List[object]]::new()
try {
 $qualifier=Join-Path $PSScriptRoot 'qualify-reconnect-enrollment-v2.ps1'
 $qualifierLock=[IO.File]::Open($qualifier,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 if((Get-FileHash $qualifier -Algorithm SHA256).Hash.ToLower() -cne '9e46ac586172efcf43e82ce0dab9b56c888cf967c614e27ba5214210b2daa0fe'){throw 'Qualification helper changed'}
 $moduleLock=[IO.File]::Open((Join-Path $Package 'ReconnectEnrollment.psm1'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 & $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $Package -ExpectedManifestSha256 $ManifestHash | Out-Null
 Import-Module (Join-Path $Package 'ReconnectEnrollment.psm1') -Force
 $result=& (Get-Module ReconnectEnrollment) {
  param($package,$id,$expected,$payloadLocks,$journalGuard)
  $c=New-ReconnectContext $package;$provider=Get-ReconnectEnrollmentProvider;$locks=& $provider.Lock $c
  $enableAttempted=$false;$startAttempted=$false
  try {
   $verified=& $provider.Verify $c
   if($verified.Identity -cne $expected){throw 'Package identity changed'}
   $j=Read-EnrollmentJournal $c $id $expected
   Assert-EnrollmentObject $j @('id','phase','task_was_absent','created_files','completed_files','package_identity','task_enabled','rollback_errors')
   & $journalGuard $j $verified $id
   foreach($f in $verified.Files){$path=Join-Path $c.Root $f.Name;Assert-EnrollmentPath $path;$payloadLocks.Add([IO.File]::Open($path,'Open','Read','Read'));if((& $provider.Hash $c $f.Name) -cne $f.Hash){throw 'Installed payload differs'}}
   $state=& $provider.State $c
   if(!$state.Complete -or $state.Paused -or $state.Active){throw 'Activation requires readable unpaused idle state'}
   $tasks=@($state.Tasks|Where-Object {$script:ReconnectNames -icontains $_.TaskName})
   if($tasks.Count -ne 1 -or $tasks[0].TaskName -cne 'Prometheus USB Reconnect Supervisor' -or $tasks[0].TaskPath -cne '\'){throw 'Unexpected lifecycle task set'}
   if(!(& $provider.OwnedTask $c $id)){throw 'Disabled task ownership does not match'}
   $baseline=Export-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -ErrorAction Stop
   $task=Get-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -ErrorAction Stop
   if(!(& $provider.OwnedTask $c $id) -or (Export-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -ErrorAction Stop) -cne $baseline){throw 'Task changed before activation'}
   $enableAttempted=$true;Enable-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null
   $startAttempted=$true;Start-ScheduledTask -InputObject $task -ErrorAction Stop
   [pscustomobject]@{success=$true;transaction=$id;manifest=$expected;enabled=$true;start_requested=$true;activation_acceptance_complete=$false}
  }catch{
   $failure=$_.Exception.Message;$enabledObserved=$null
   if($enableAttempted){try{$enabledObserved=[bool](Get-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -ErrorAction Stop).Settings.Enabled}catch{}}
   [pscustomobject]@{success=$false;transaction=$id;manifest=$expected;error=$failure;enable_attempted=$enableAttempted;enabled_observed=$enabledObserved;start_attempted=$startAttempted;process_may_have_started=$startAttempted;activation_acceptance_complete=$false;requires_recovery_check=$enableAttempted}
  }finally{& $provider.Unlock $c $locks}
 } $Package $TransactionId $ManifestHash $payloadLocks ${function:Assert-ActivationJournal}
 $result|ConvertTo-Json -Depth 5
 if(!$result.success){exit 1}
}finally{foreach($handle in $payloadLocks){$handle.Dispose()};if($moduleLock){$moduleLock.Dispose()};if($qualifierLock){$qualifierLock.Dispose()}}
