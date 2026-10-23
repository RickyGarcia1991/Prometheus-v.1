# Production task policy. No top-level task mutation and no activation launcher.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script:TaskName='Prometheus USB Reconnect Supervisor'
$script:CollectorPin='399606ea06f58a85c65c8d6c53a831c58bcfc893e35cbbb9ddeceafbff8fefcd'

function Get-MigrationBytesHash([byte[]]$Bytes) {
 [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant()
}
function Get-MigrationNormalizedTaskXml([string]$Xml) {
 $document=[xml]::new();$document.PreserveWhitespace=$true;$document.XmlResolver=$null
 $document.LoadXml($Xml)
 $manager=[Xml.XmlNamespaceManager]::new($document.NameTable)
 $manager.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
 $nodes=@($document.SelectNodes('/t:Task/t:Settings/t:Enabled',$manager))
 if($nodes.Count -ne 1 -or $nodes[0].InnerText -cnotin @('true','false')){throw 'Exactly one direct task Enabled element required'}
 $nodes[0].InnerText='false'
 $document.OuterXml
}
function Get-ActiveMigrationObservation {
 # Reuse the unchanged read-only signed closure, real-profile and receipt verifier.
 $collector=Join-Path $PSScriptRoot 'collect-active-host-migration-preflight.ps1'
 $stream=[IO.File]::Open($collector,'Open','Read','Read')
 try {
  $memory=[IO.MemoryStream]::new()
  try {$stream.CopyTo($memory);$bytes=$memory.ToArray()} finally {$memory.Dispose()}
  if((Get-MigrationBytesHash $bytes) -cne $script:CollectorPin){throw 'Read-only collector pin changed'}
  $observation=(& $collector)|ConvertFrom-Json
  if(!$observation.collected){throw ('Fresh observation refused: '+$observation.stage)}
  $xml=Export-ScheduledTask -TaskName $script:TaskName -TaskPath '\'
  if((Get-MigrationBytesHash ([Text.Encoding]::UTF8.GetBytes($xml))) -cne $observation.task.config_sha256){throw 'Task changed after collector observation'}
  $machine=[string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
  $normalized=Get-MigrationNormalizedTaskXml $xml
  [pscustomobject]@{schema=1;observation=$observation;task_xml=$xml;normalized_task_sha256=(Get-MigrationBytesHash ([Text.Encoding]::UTF8.GetBytes($normalized)));host=(Get-MigrationBytesHash ([Text.Encoding]::UTF8.GetBytes($machine)))}
 } finally {$stream.Dispose()}
}
function Assert-MigrationTaskBinding($Observed,$Binding,[bool]$ExpectedEnabled) {
 $o=$Observed.observation
 if($o.context.classification -cne 'physical-profile' -or $o.context.sid -cne $Binding.sid -or $o.context.root -ine $Binding.root -or $o.context.session_id -ne $Binding.session_id){throw 'Owner/profile/session changed'}
 if($Observed.normalized_task_sha256 -cne $Binding.normalized_task_sha256 -or $o.task.enrollment_id -cne $Binding.enrollment_id -or !$o.task.owned_shape){throw 'Exact existing task binding changed'}
 if($o.task.enabled -ne $ExpectedEnabled){throw 'Unexpected task enabled state'}
 if($o.auth.sha256 -cne $Binding.auth_sha256 -or !$o.auth.sidecar_matches -or !$o.auth.host_matches -or $Observed.host -cne $Binding.host -or $o.receipt.sha256 -cne $Binding.enrollment_sha256 -or !$o.receipt.authenticated){throw 'Original authority or receipt changed'}
 if($o.pause_markers.Count -or $o.competing_lifecycle_tasks.Count){throw 'Concurrent lifecycle/pause state'}
}
function Invoke-MigrationTaskOperation {
 # Internal callback only. Owner must hold outer locks and verify runtime-lock
 # release before invoking restore/start. This is not an exported live entry.
 param([ValidateSet('Disable','Restore','Start')][string]$Operation,$Binding,[bool]$ExpectedEnabled,[bool]$OriginalEnabled)
 $before=Get-ActiveMigrationObservation
 Assert-MigrationTaskBinding $before $Binding $ExpectedEnabled
 switch($Operation){
  'Disable' {if($ExpectedEnabled){$null=Disable-ScheduledTask -TaskName $script:TaskName -TaskPath '\'}}
  'Restore' {if($OriginalEnabled -ne $ExpectedEnabled){if($OriginalEnabled){$null=Enable-ScheduledTask -TaskName $script:TaskName -TaskPath '\'}else{$null=Disable-ScheduledTask -TaskName $script:TaskName -TaskPath '\'}}}
  'Start' {if(!$OriginalEnabled -or !$ExpectedEnabled -or $before.observation.task.state -ne 'Ready' -or @($before.observation.workers|Where-Object kind -In @('wrapper','supervisor')).Count){throw 'Start requires exact stopped enabled task'};$null=Start-ScheduledTask -TaskName $script:TaskName -TaskPath '\'}
 }
 $after=Get-ActiveMigrationObservation
 $enabled=if($Operation -eq 'Disable'){$false}elseif($Operation -eq 'Restore'){$OriginalEnabled}else{$ExpectedEnabled}
 Assert-MigrationTaskBinding $after $Binding $enabled
 $after
}
function Get-MigrationIndependentProcesses {
 $observed=Get-ActiveMigrationObservation;$o=$observed.observation
 $all=@(Get-CimInstance Win32_Process -ErrorAction Stop)
 if($all.Count -gt 8192){throw 'Process inventory bound'}
 $ports=@(Get-NetTCPConnection -State Listen -ErrorAction Stop|Where-Object {$_.LocalPort -in @(54555,54559)}|Select-Object @{n='port';e={[int]$_.LocalPort}},@{n='pid';e={[long]$_.OwningProcess}}|Sort-Object port,pid -Unique)
 $selected=[Collections.Generic.HashSet[long]]::new()
 foreach($port in $ports){$null=$selected.Add($port.pid)}
 $commander=Join-Path $o.context.physical_local 'DesktopCommanderStartup'
 foreach($process in $all){
  if($process.Name -ieq 'Prometheus.DriveController.exe' -or ([string]$process.CommandLine).IndexOf($commander,[StringComparison]::OrdinalIgnoreCase) -ge 0){
   $owner=Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction Stop
   if($owner.ReturnValue -ne 0){throw 'Independent owner unavailable'}
   if([string]$owner.Sid -ceq $o.context.sid){$null=$selected.Add([long]$process.ProcessId)}
  }
 }
 if($selected.Count -gt 64){throw 'Independent process bound'}
 [pscustomobject]@{pids=@($selected|Sort-Object);ports=$ports}
}
Export-ModuleMember -Function Get-ActiveMigrationObservation,Get-MigrationNormalizedTaskXml,Get-MigrationIndependentProcesses
