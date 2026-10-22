# Create-only R3 -> R5 explicit quiet-host/arrival replacement. No old task identity is reused.
[CmdletBinding()]param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function Invoke-QuietHostCreateOnlyUpgrade($Provider){
 $phase='preflight';$new=$null
 try{
  $baseline=& $Provider.Check
  & $Provider.Save 'intent' $baseline
  $phase='backup';& $Provider.Backup $baseline
  & $Provider.Save 'backup-verified' $baseline
  $phase='remove-task';& $Provider.Recheck $baseline;& $Provider.RemoveTask
  & $Provider.Save 'old-task-removed' $baseline
  $phase='remove-payloads';& $Provider.RemoveBinary $baseline
  & $Provider.Save 'old-payloads-removed' $baseline
  $phase='new-enrollment';$new=& $Provider.Enroll
  if($new.status -cne 'enrolled-disabled' -or $new.activated -ne $false -or $new.id -cnotmatch '^[a-f0-9]{32}$' -or $new.id -ceq '76285c9134d2434790cb84b4680350b4'){throw 'Fresh disabled enrollment not proven'}
  & $Provider.Save 'new-enrolled-disabled' $new
  return $new
 }catch{
  try{& $Provider.Save 'failed' @{phase=$phase;new_enrollment=$new;error=$_.Exception.Message;requires_inspection=$true;old_task_not_reused=$true;activation_requested=$false}}catch{}
  throw
 }
}
function Assert-OldEnrollmentJournal($j,$verified,$id){
 if($j.id -cne $id -or $j.package_identity -cne $verified.Identity -or $j.phase -cne 'enrolled-disabled' -or $j.task_was_absent -isnot [bool] -or !$j.task_was_absent -or $j.task_enabled -isnot [bool] -or $j.task_enabled){throw 'Old completed disabled enrollment required'}
 if($j.created_files -isnot [array] -or $j.completed_files -isnot [array] -or $j.rollback_errors -isnot [array] -or $j.rollback_errors.Count -or $j.created_files.Count -ne $j.completed_files.Count -or $j.created_files.Count -gt 5){throw 'Old enrollment completion is malformed'}
 $seen=@{}
 foreach($row in $j.created_files){
  if($row.name -isnot [string] -or $row.hash -isnot [string] -or $seen.ContainsKey($row.name)){throw 'Old enrollment ownership is malformed'};$seen[$row.name]=$true
  if(@($verified.Files|Where-Object {$_.Name -ceq $row.name -and $_.Hash -ceq $row.hash}).Count -ne 1 -or @($j.completed_files|Where-Object {$_.name -ceq $row.name -and $_.hash -ceq $row.hash}).Count -ne 1){throw 'Old enrollment differs from signed package'}
 }
}
function Assert-PhysicalEnrollmentRoot([string]$Root){
 if(-not ('PrometheusUpgradePhysicalRoot' -as [type])){
  Add-Type -TypeDefinition @"
using System;using System.Text;using System.Runtime.InteropServices;using Microsoft.Win32.SafeHandles;
public static class PrometheusUpgradePhysicalRoot {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] public static extern int GetCurrentPackageFullName(ref uint length,StringBuilder name);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern SafeFileHandle CreateFile(string p,uint a,uint s,IntPtr sec,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder b,uint n,uint flags);
}
"@
 }
 [uint32]$packageLength=0;if([PrometheusUpgradePhysicalRoot]::GetCurrentPackageFullName([ref]$packageLength,$null) -ne 15700){throw 'Packaged/MSIX execution context refused'}
 $handle=[PrometheusUpgradePhysicalRoot]::CreateFile($Root,128,3,[IntPtr]::Zero,3,33554432,[IntPtr]::Zero)
 try{
  if($handle.IsInvalid){throw 'Physical root handle unavailable'}
  $buffer=[Text.StringBuilder]::new(32768);$length=[PrometheusUpgradePhysicalRoot]::GetFinalPathNameByHandle($handle,$buffer,32768,0)
  if(!$length -or $length -ge 32768 -or $buffer.ToString().TrimEnd('\') -ine ('\\?\'+[IO.Path]::GetFullPath($Root).TrimEnd('\'))){throw 'Profile root resolves elsewhere; MSIX/redirection refused'}
  return $handle
 }catch{$handle.Dispose();throw}
}
$base=$PSScriptRoot
$old=Join-Path $base 'reconnect-enrollment-candidate-r3'
$new=Join-Path $base 'reconnect-enrollment-candidate-r5'
$oldManifest='a0461287d0984894d3549c22d4e16a5bff5db55f4a1925729479eed7f48ea3b8'
$newManifest='b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e'
$id='76285c9134d2434790cb84b4680350b4'
$qualifier=Join-Path $base 'qualify-reconnect-enrollment-v2.ps1'
$activation=Join-Path $base 'activate-enrolled-reconnect.ps1'
$guards=[Collections.Generic.List[object]]::new();$physical=$null
try{
 foreach($file in @($qualifier,$activation)+@(Get-ChildItem $old -File|ForEach-Object FullName)+@(Get-ChildItem $new -File|ForEach-Object FullName)){$guards.Add([IO.File]::Open($file,'Open','Read','Read'))}
 if((Get-FileHash $qualifier).Hash.ToLower() -cne '9e46ac586172efcf43e82ce0dab9b56c888cf967c614e27ba5214210b2daa0fe'){throw 'Qualifier changed'}
 if((Get-FileHash $activation).Hash.ToLower() -cne '5bd020db3ca7ded6c8ab4a52eb168cc904ba28b1f050076a17e34e642dda9a97'){throw 'Activation helper changed'}
 foreach($spec in @(@($old,$oldManifest),@($new,$newManifest))){& $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $spec[0] -ExpectedManifestSha256 $spec[1]|Out-Null}
 if((Get-FileHash (Join-Path $old 'ReconnectEnrollment.psm1')).Hash -cne (Get-FileHash (Join-Path $new 'ReconnectEnrollment.psm1')).Hash){throw 'Upgrade requires unchanged enrollment implementation'}
 Import-Module (Join-Path $old 'ReconnectEnrollment.psm1') -Force
 $context=& (Get-Module ReconnectEnrollment) {param($p)New-ReconnectContext $p} $old
 $physical=Assert-PhysicalEnrollmentRoot $context.Root
 $result=& (Get-Module ReconnectEnrollment) {
  param($old,$new,$oldManifest,$newManifest,$id,$base,$core,$journalCheck)
  $c=New-ReconnectContext $old;$n=New-ReconnectContext $new;$p=Get-ReconnectEnrollmentProvider;$locks=& $p.Lock $c
  $out=Join-Path $base ('quiet-create-only-upgrade-'+[guid]::NewGuid().ToString('N'))
  $changed=@('Prometheus-QuietHost-v3.exe','Prometheus-Arrival.ps1')
  $provider=@{
   Check={
    $a=& $p.Verify $c;$b=& $p.Verify $n
    if($a.Identity -cne $oldManifest -or $b.Identity -cne $newManifest){throw 'Package identity changed'}
    $j=Read-EnrollmentJournal $c $id $oldManifest;& $journalCheck $j $a $id
    $state=& $p.State $c;if(!$state.Complete -or $state.Paused -or $state.Active){throw 'Host is active, paused or unreadable'}
    $tasks=@($state.Tasks|Where-Object {$script:ReconnectNames -icontains $_.TaskName})
    if($tasks.Count -ne 1 -or !(& $p.OwnedTask $c $id)){throw 'Old exact disabled task required'}
    foreach($f in $a.Files){if((& $p.Hash $c $f.Name) -cne $f.Hash){throw 'Old installed dependency changed'};if($f.Name -cnotin $changed -and @($b.Files|Where-Object {$_.Name -ceq $f.Name -and $_.Hash -ceq $f.Hash}).Count -ne 1){throw 'Upgrade changes undeclared dependencies'}}
    [pscustomobject]@{old_manifest=$oldManifest;new_manifest=$newManifest;old_transaction=$id;changed_files=@($a.Files|Where-Object {$_.Name -cin $changed}|ForEach-Object {[pscustomobject]@{Name=$_.Name;Hash=$_.Hash;Backup=(Join-Path $out ('old-signed-'+$_.Name))}});task_xml=(Export-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -ErrorAction Stop);backup_directory=$out}
   }
   Save={param($phase,$record)Assert-EnrollmentPath $out;[IO.Directory]::CreateDirectory($out)|Out-Null;Write-EnrollmentExclusive (Join-Path $out ($phase+'.json')) ([Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Depth 10)))}
   Backup={param($record)foreach($f in $record.changed_files){$bytes=Read-EnrollmentBytes (Join-Path $c.Root $f.Name) 16777216;if((Get-EnrollmentHash $bytes) -cne $f.Hash){throw 'Old payload changed'};Write-EnrollmentExclusive $f.Backup $bytes;if((Get-EnrollmentHash (Read-EnrollmentBytes $f.Backup 16777216)) -cne $f.Hash){throw 'Backup readback failed'}}}
   Recheck={param($record)$state=& $p.State $c;if(!$state.Complete -or $state.Paused -or $state.Active -or !(& $p.OwnedTask $c $id) -or (Export-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -ErrorAction Stop) -cne $record.task_xml){throw 'Task/state changed before removal'}}
   RemoveTask={& $p.RemoveTask $c $id}
   RemoveBinary={param($record)Assert-EnrollmentState (& $p.State $c);foreach($f in $record.changed_files){if((& $p.Hash $c $f.Name) -cne $f.Hash){throw 'Old payload changed before removal'}};foreach($f in $record.changed_files){& $p.RemoveFile $c $f}}
   Enroll={Start-ReconnectEnrollment -Package $new}
  }
  try{& $core $provider}finally{& $p.Unlock $c $locks}
 } $old $new $oldManifest $newManifest $id $base ${function:Invoke-QuietHostCreateOnlyUpgrade} ${function:Assert-OldEnrollmentJournal}
 # Activation revalidates the NEW sealed completion journal and exact new disabled task.
 & $activation -Package $new -ManifestHash $newManifest -TransactionId $result.id
}finally{if($physical){$physical.Dispose()};foreach($guard in $guards){$guard.Dispose()}}



