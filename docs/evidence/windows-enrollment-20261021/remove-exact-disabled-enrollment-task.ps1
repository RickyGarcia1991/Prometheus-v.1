param([switch]$Remove)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Resolve-RecoverySid([string]$Identity){
 if([string]::IsNullOrWhiteSpace($Identity)){throw 'Missing task identity'}
 if($Identity -match '^S-1-') {return ([Security.Principal.SecurityIdentifier]::new($Identity)).Value}
 return ([Security.Principal.NTAccount]::new($Identity)).Translate([Security.Principal.SecurityIdentifier]).Value
}
function Assert-RecoveryTask($Task,$Context,[string]$Id){
 $a=@($Task.Actions);$t=@($Task.Triggers)
 if($Task.TaskName -cne 'Prometheus USB Reconnect Supervisor' -or $Task.TaskPath -cne '\' -or $Task.Description -cne ('Prometheus reconnect enrollment '+$Id) -or [string]$Task.State -cne 'Disabled' -or $Task.Settings.Enabled){throw 'Task is not exact disabled transaction'}
 if($a.Count -ne 1 -or $a[0].Execute -ine (Join-Path $Context.Root 'Prometheus-QuietHost-v3.exe') -or $a[0].Arguments -cne 'reconnect' -or $a[0].WorkingDirectory -ine $Context.Root){throw 'Task action mismatch'}
 if((Resolve-RecoverySid $Task.Principal.UserId) -cne $Context.Sid -or $Task.Principal.RunLevel -ne 0 -or $Task.Principal.LogonType -ne 3){throw 'Task principal mismatch'}
 if($t.Count -ne 1 -or $t[0].CimClass.CimClassName -cne 'MSFT_TaskLogonTrigger' -or (Resolve-RecoverySid $t[0].UserId) -cne $Context.Sid){throw 'Task trigger mismatch'}
 if($Task.Settings.MultipleInstances -ne 2 -or $Task.Settings.DisallowStartIfOnBatteries -or $Task.Settings.StopIfGoingOnBatteries -or !$Task.Settings.StartWhenAvailable -or $Task.Settings.ExecutionTimeLimit -cne 'PT0S'){throw 'Task settings mismatch'}
}
$package=Join-Path $PSScriptRoot 'reconnect-enrollment-candidate-r2'
$manifest='64c19a686fc34b148a6bcec8a24098d5768510ffdcb701c67d7dbd436e5ebefb'
$id='1b06096c52914b72afdb4663c468ff0b'
$qualifier=Join-Path $PSScriptRoot 'qualify-reconnect-enrollment-v2.ps1'
$handles=[Collections.Generic.List[object]]::new()
try{
 foreach($path in @($qualifier,(Join-Path $package 'ReconnectEnrollment.psm1'))){$handles.Add([IO.File]::Open($path,'Open','Read','Read'))}
 if((Get-FileHash $qualifier).Hash.ToLowerInvariant() -cne '9e46ac586172efcf43e82ce0dab9b56c888cf967c614e27ba5214210b2daa0fe'){throw 'Qualifier changed'}
 & $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $package -ExpectedManifestSha256 $manifest | Out-Null
 Import-Module (Join-Path $package 'ReconnectEnrollment.psm1') -Force
 & (Get-Module ReconnectEnrollment) {
  param($package,$manifest,$id,$remove,$output,$handles)
  $c=New-ReconnectContext $package;$p=Get-ReconnectEnrollmentProvider;$locks=$null
  try{
   $locks=& $p.Lock $c
   $verified=& $p.Verify $c;if($verified.Identity -cne $manifest){throw 'Package identity changed'}
   $j=Read-EnrollmentJournal $c $id $manifest
   if($j.phase -cne 'rollback-incomplete' -or $j.task_was_absent -ne $true -or $j.task_enabled -ne $false){throw 'Unexpected sealed transaction state'}
   foreach($rows in @(@{items=@($j.created_files)},@{items=@($j.completed_files)})){
    if($rows.items.Count -ne $verified.Files.Count){throw 'Incomplete signed transaction file set'}
    foreach($file in $verified.Files){$matches=@($rows.items|Where-Object {$_.name -ceq $file.Name -and $_.hash -ceq $file.Hash});if($matches.Count -ne 1){throw 'Transaction payload identity mismatch'}}
   }
   foreach($file in $verified.Files){$path=Join-Path $c.Root $file.Name;Assert-EnrollmentPath $path;$handles.Add([IO.File]::Open($path,'Open','Read','Read'));if((& $p.Hash $c $file.Name) -cne $file.Hash){throw 'Installed signed payload differs'}}
   $state=& $p.State $c
   if(!$state.Complete -or $state.Paused -or $state.Active){throw 'Unsafe recovery inventory'}
   $tasks=@($state.Tasks|Where-Object {$script:ReconnectNames -icontains $_.TaskName})
   if($tasks.Count -ne 1){throw 'Unexpected lifecycle task set'}
   & $output.AssertTask $tasks[0] $c $id
   $xml=Export-ScheduledTask -TaskName $tasks[0].TaskName -TaskPath $tasks[0].TaskPath -ErrorAction Stop
   $bytes=[Text.UTF8Encoding]::new($false).GetBytes($xml)
   if($bytes.Length -gt 1048576){throw 'Task export exceeds evidence limit'}
   $evidence=Join-Path $output.Directory ('manual-task-'+$id+'.xml')
   if($null -eq (Get-EnrollmentAttributes $evidence)){Write-EnrollmentExclusive $evidence $bytes}
   if((Get-EnrollmentHash (Read-EnrollmentBytes $evidence 1048576)) -cne (Get-EnrollmentHash $bytes)){throw 'Task evidence differs'}
   $again=@(Get-ScheduledTask -TaskName 'Prometheus USB Reconnect Supervisor' -TaskPath '\' -ErrorAction Stop)
   if($again.Count -ne 1){throw 'Task changed before removal'}
   & $output.AssertTask $again[0] $c $id
   if((Export-ScheduledTask -TaskName $again[0].TaskName -TaskPath $again[0].TaskPath -ErrorAction Stop) -cne $xml){throw 'Task definition changed before removal'}
   if($remove){Unregister-ScheduledTask -InputObject $again[0] -Confirm:$false -ErrorAction Stop
    if(@(Get-ScheduledTask -ErrorAction Stop|Where-Object {$_.TaskName -ieq 'Prometheus USB Reconnect Supervisor' -and $_.TaskPath -ceq '\'}).Count){throw 'Task remains after removal'}
   }
   [ordered]@{transaction=$id;manifest_sha256=$manifest;task_removed=[bool]$remove;principal_sid=$c.Sid;trigger_sid=$c.Sid;payloads_verified=$verified.Files.Count;evidence=$evidence;files_removed=$false;repair_invoked=$false;activated=$false}|ConvertTo-Json
  }finally{if($null -ne $locks){& $p.Unlock $c $locks}}
 } $package $manifest $id ([bool]$Remove) @{Directory=$PSScriptRoot;AssertTask=${function:Assert-RecoveryTask}} $handles
}finally{foreach($handle in $handles){$handle.Dispose()}}
