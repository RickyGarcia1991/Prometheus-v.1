$ErrorActionPreference='Stop'
$b=$PSScriptRoot;$pkg=Join-Path $b 'reconnect-enrollment-candidate-r5'
& (Join-Path $b 'qualify-reconnect-enrollment-v2.ps1') -Operation Verify -PackageKind ReconnectEnrollment -Candidate $pkg -ExpectedManifestSha256 b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e | Out-Null
Import-Module (Join-Path $pkg 'ReconnectEnrollment.psm1') -Force
$r=& (Get-Module ReconnectEnrollment) {
 param($pkg)
 $c=New-ReconnectContext $pkg;$p=Get-ReconnectEnrollmentProvider;$v=& $p.Verify $c
 $rows=@(foreach($f in $v.Files){$actual=& $p.Hash $c $f.Name;if($actual -cne $f.Hash){throw 'Installed payload mismatch'};@{name=$f.Name;sha256=$actual}})
 $j=Read-EnrollmentJournal $c '7b06d1cde83a4b8ebb5a8ac1fa086887' $v.Identity
 if($j.phase -cne 'enrolled-disabled'){throw 'Fresh sealed enrollment not complete'}
 @{passed=$true;manifest=$v.Identity;files=$rows;authorization_sha=(Get-FileHash (Join-Path $c.Root 'authorized-host.json')).Hash;memory_present=(Test-Path (Join-Path $c.Root 'memory.sqlite3'));fresh_journal_phase=$j.phase;activation_record_separate=$true;captured=(Get-Date).ToUniversalTime().ToString('o')}
} $pkg
$r|ConvertTo-Json -Depth 6|Set-Content (Join-Path $b 'real-r5-installed-integrity.json')
