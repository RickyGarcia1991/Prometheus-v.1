# Development-only native signed-chain fixture bridge. Verify is read-only.
param([Parameter(Mandatory)][string]$Scratch,[ValidateSet('CreateReceipt','Verify')][string]$Mode='Verify',[string]$OldPackage,[string]$NewPackage)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$R5='b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e';$R7='6e26224389829b4f9cfb3288b8f9495d0e285e9fc96a322f3db25e82ecddc01e'
if(!$OldPackage){$OldPackage=Join-Path $PSScriptRoot 'reconnect-enrollment-candidate-r5'}
if(!$NewPackage){$NewPackage=Join-Path $PSScriptRoot 'reconnect-enrollment-candidate-r7'}
$root=[IO.Path]::GetFullPath($Scratch).TrimEnd('\');$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if(!$root.StartsWith($temp+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $root -Leaf) -notlike 'prometheus-active-chain-*'){throw 'Generated native-chain fixture required'}
$locks=[Collections.Generic.List[IO.Stream]]::new()
function Lock-Read([string]$Path){
 $item=Get-Item -LiteralPath $Path -Force
 while($item){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse input refused'};if($item -is [IO.DirectoryInfo]){$item=$item.Parent}else{$item=$item.Directory}}
 $locks.Add([IO.File]::Open($Path,'Open','Read','Read'))
}
function Digest([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
try{
 $qualifier=Join-Path $PSScriptRoot 'qualify-reconnect-enrollment-v2.ps1';Lock-Read $qualifier
 if((Digest $qualifier) -cne '9e46ac586172efcf43e82ce0dab9b56c888cf967c614e27ba5214210b2daa0fe'){throw 'Existing qualifier changed'}
 foreach($bundle in @($OldPackage,$NewPackage)){foreach($file in Get-ChildItem -LiteralPath $bundle -File){Lock-Read $file.FullName}}
 $oldVerified=(& $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $OldPackage -ExpectedManifestSha256 $R5)|ConvertFrom-Json
 $newVerified=(& $qualifier -Operation Verify -PackageKind ReconnectEnrollment -Candidate $NewPackage -ExpectedManifestSha256 $R7)|ConvertFrom-Json
 if(!$oldVerified.signature_verified -or !$newVerified.signature_verified -or $oldVerified.files -ne 7 -or $newVerified.files -ne 7){throw 'Signed exact closure required'}
 $oldRows=@(Get-Content -LiteralPath (Join-Path $OldPackage 'SHA256-MANIFEST.json') -Raw|ConvertFrom-Json)
 $newRows=@(Get-Content -LiteralPath (Join-Path $NewPackage 'SHA256-MANIFEST.json') -Raw|ConvertFrom-Json)
 $changed=@()
 foreach($old in $oldRows){$new=@($newRows|Where-Object path -CEQ $old.path);if($new.Count -ne 1){throw 'Closure path mismatch'};if($new[0].sha256 -cne $old.sha256 -or $new[0].size_bytes -ne $old.size_bytes){$changed+=@($old.path)}}
 if($changed.Count -ne 1 -or $changed[0] -cne 'Prometheus-Host-Agent.ps1'){throw 'Only HostAgent may differ'}
 $modulePath=Join-Path $OldPackage 'ReconnectEnrollment.psm1'
 if((Digest $modulePath) -cne '3df45e228e82e6a72509c2c2f4fb4301836c1a3b2a70bbc497feda2406ba1509'){throw 'Existing enrollment module changed'}
 $module=Import-Module $modulePath -Force -PassThru
 $activation=Join-Path $PSScriptRoot 'activate-enrolled-reconnect.ps1';Lock-Read $activation
 if((Digest $activation) -cne '5bd020db3ca7ded6c8ab4a52eb168cc904ba28b1f050076a17e34e642dda9a97'){throw 'Existing activation source changed'}
 # Extract only the actual pure journal guard. Never run activation top-level code.
 $ast=[Management.Automation.Language.Parser]::ParseFile($activation,[ref]$null,[ref]$null)
 $guard=@($ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-ActivationJournal'},$false))
 if($guard.Count -ne 1){throw 'Journal guard missing'};. ([scriptblock]::Create($guard[0].Extent.Text))
 $runtime=Join-Path $root 'runtime';$observationsPath=Join-Path $root 'observations.json';Lock-Read $observationsPath
 $observed=Get-Content -LiteralPath $observationsPath -Raw|ConvertFrom-Json
 if($observed.fixture -cne 'synthetic-authority-not-installed' -or $observed.enrollment_id -cnotmatch '^[a-f0-9]{32}$'){throw 'Synthetic fixture identity required'}
 $id=$observed.enrollment_id
 $payloadNames=@('Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-Host-Agent.ps1')
 $oldFiles=@($oldRows|Where-Object {$_.path -cin $payloadNames}|ForEach-Object {[pscustomobject]@{Name=$_.path;Hash=$_.sha256}})
 $verified=[pscustomobject]@{Identity=$R5;Files=$oldFiles}
 $receipt=Join-Path $runtime ('Enrollment\'+$id+'\journal.json')
 if($Mode -eq 'CreateReceipt'){
  if(Test-Path -LiteralPath $receipt){throw 'Original fixture receipt is create-only'}
  $history=@($oldFiles|ForEach-Object {@{name=$_.Name;hash=$_.Hash}})
  $j=[ordered]@{id=$id;phase='enrolled-disabled';task_was_absent=$true;created_files=$history;completed_files=$history;package_identity=$R5;task_enabled=$false;rollback_errors=@()}
  Assert-ActivationJournal ([pscustomobject]$j) $verified $id
  $sealed=& $module {param($j) Protect-EnrollmentJournal $j} $j
  $null=[IO.Directory]::CreateDirectory((Split-Path $receipt -Parent))
  $stream=[IO.File]::Open($receipt,'CreateNew','Write','None');try{$stream.Write($sealed,0,$sealed.Length);$stream.Flush($true)}finally{$stream.Dispose()}
  @{receipt_sha256=(Digest $receipt);fixture_original_receipt=$true;installed_authorization_authenticated=$false}|ConvertTo-Json -Compress
  return
 }
 $planPath=Join-Path $root 'plan.json';Lock-Read $planPath;Lock-Read $receipt
 $plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
 if($plan.old_manifest -cne $R5 -or $plan.new_manifest -cne $R7 -or $plan.enrollment_sha256 -cne (Digest $receipt)){throw 'Immutable receipt/package binding mismatch'}
 $journal=& $module {param($runtime,$id,$pin) Read-EnrollmentJournal @{Root=$runtime} $id $pin} $runtime $id $R5
 & $module {param($j) Assert-EnrollmentObject $j @('id','phase','task_was_absent','created_files','completed_files','package_identity','task_enabled','rollback_errors')} $journal
 Assert-ActivationJournal $journal $verified $id
 $legacyGuardRefused=$false
 try{Assert-ActivationJournal $journal ([pscustomobject]@{Identity=$R7;Files=@($newRows|Where-Object {$_.path -cin $payloadNames}|ForEach-Object {[pscustomobject]@{Name=$_.path;Hash=$_.sha256}})}) $id}catch{$legacyGuardRefused=$true}
 if(!$legacyGuardRefused){throw 'Existing activation guard unexpectedly accepted R5 receipt as R7'}
 # The historical R5 schema lacks host/auth/XML fields: authenticate those exact
 # observations via the separate migration plan, never invent receipt properties.
 $auth=Join-Path $runtime 'authorized-host.json';$task=Join-Path $root 'task.xml';Lock-Read $auth;Lock-Read $task
 $authObject=Get-Content -LiteralPath $auth -Raw|ConvertFrom-Json
 if($plan.auth_sha256 -cne (Digest $auth) -or $plan.task_config_sha256 -cne (Digest $task) -or $plan.host -cne $observed.host -or $plan.host -cne $authObject.fingerprint -or $plan.profile -cne $observed.profile -or $plan.task_identity -cne $observed.task_identity){throw 'Observed host/profile/task/authorization binding mismatch'}
 [xml]$taskXml=Get-Content -LiteralPath $task -Raw
 if($taskXml.Task.RegistrationInfo.Description -cne ('Prometheus reconnect enrollment '+$id)){throw 'Original receipt/task transaction mismatch'}
 $oldHost=@($oldRows|Where-Object path -CEQ 'Prometheus-Host-Agent.ps1')[0]
 $newHost=@($newRows|Where-Object path -CEQ 'Prometheus-Host-Agent.ps1')[0]
 if($plan.old_file_sha256 -cne $oldHost.sha256 -or $plan.new_file_sha256 -cne $newHost.sha256){throw 'Signed HostAgent digest binding mismatch'}
 $provider=& $module {Get-ReconnectEnrollmentProvider}
 foreach($name in $payloadNames){
  $file=Join-Path $runtime $name;Lock-Read $file;$row=@($newRows|Where-Object path -CEQ $name)[0]
  $actual=& $module {param($provider,$runtime,$name) & $provider.Hash @{Root=$runtime} $name} $provider $runtime $name
  if($actual -cne $row.sha256 -or (Get-Item -LiteralPath $file).Length -ne $row.size_bytes){throw ('Installed fixture payload differs: '+$name)}
 }
 # Actual unchanged R5 provider hash + activation comparison reject changed R7 bytes.
 $legacyMismatch=@($oldFiles|Where-Object {(& $module {param($provider,$runtime,$name) & $provider.Hash @{Root=$runtime} $name} $provider $runtime $_.Name) -cne $_.Hash})
 if($legacyMismatch.Count -ne 1 -or $legacyMismatch[0].Name -cne 'Prometheus-Host-Agent.ps1'){throw 'Expected old-consumer R5 refusal was not demonstrated'}
 $wrongPackageRefused=$false
 try{& $module {param($runtime,$id,$pin) Read-EnrollmentJournal @{Root=$runtime} $id $pin} $runtime $id $R7|Out-Null}catch{$wrongPackageRefused=$true}
 if(!$wrongPackageRefused){throw 'Old receipt unexpectedly reinterpreted as R7'}
 @{native_verified=$true;old_manifest=$R5;new_manifest=$R7;bundle_entries=7;unchanged_entries=6;runtime_payloads=5;receipt_sha256=(Digest $receipt);plan_sha256=(Digest $planPath);legacy_r5_runtime_refused=$true;existing_activation_guard_rejects_r7=$true;receipt_as_r7_refused=$true;fixture_original_receipt=$true;installed_authorization_authenticated=$false;activated=$false}|ConvertTo-Json -Compress
}finally{foreach($stream in $locks){$stream.Dispose()}}

