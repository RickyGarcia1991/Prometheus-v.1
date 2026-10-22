#requires -Version 7.3
[CmdletBinding()]
param(
 [Parameter(Mandatory)][ValidateSet('Validate','Verify','Sign')][string]$Operation,
 [Parameter(Mandatory)][ValidateSet('ReconnectEnrollment')][string]$PackageKind,
 [Parameter(Mandatory)][string]$Candidate,
 [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedManifestSha256
)
$ErrorActionPreference='Stop'
$library='C:/Users/rolon/Documents/Codex/Workstation/Prometheus/Signing/Prometheus-Signing.ps1'
$librarySha='e2090ff70bc2a1a5677d0b40854d332ee234e07c4441bbcfe2b646f7475ce86f'
$trusted=@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98')
function AssertPlain([string]$Path) {
 $cursor=Get-Item -LiteralPath $Path -Force
 while($null -ne $cursor){if($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse paths refused'};$cursor=$cursor.Parent}
}
function HashStream([IO.Stream]$Stream){$Stream.Position=0;try{return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Stream)).ToLower()}finally{$Stream.Position=0}}
function VerifyPublic([string]$Root,[string]$Manifest){
 $cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $Root 'MANIFEST-PUBLIC.cer'))
 try {
  if($trusted -notcontains $cert.Thumbprint -or $cert.HasPrivateKey){throw 'Certificate not an accepted public signer'}
  $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert)
  try {if(!$rsa.VerifyData([IO.File]::ReadAllBytes($Manifest),[IO.File]::ReadAllBytes((Join-Path $Root 'SHA256-MANIFEST.sig')),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)){throw 'Signature invalid'}}finally{$rsa.Dispose()}
  return $cert.Thumbprint
 }finally{$cert.Dispose()}
}
$locks=[Collections.Generic.List[IO.Stream]]::new()
try {
 $root=[IO.Path]::GetFullPath($Candidate).TrimEnd('\','/')
 $allowed=[IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\','/')+'\'
 if(!$root.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'Candidate must be a new local companion-release subdirectory'}
 AssertPlain $root
 if(!(Test-Path -LiteralPath $root -PathType Container)){throw 'Candidate directory missing'}
 $expected=@('Prometheus-QuietHost-v3.exe','Prometheus-USB-Reconnect-Supervisor.ps1','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-Host-Agent.ps1','RECONNECT-ENROLLMENT.json','ReconnectEnrollment.psm1')
 $signatureNames=@('SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')
 $allowedNames=$expected+@('SHA256-MANIFEST.json')
 if($Operation -eq 'Verify'){$allowedNames+=$signatureNames}
 foreach($item in Get-ChildItem -LiteralPath $root -Force){AssertPlain $item.FullName;if($item.PSIsContainer -or $item.Name -cnotin $allowedNames){throw ('Unlisted or noncanonical file: '+$item.Name)}}
 foreach($name in $allowedNames){if(!(Test-Path -LiteralPath (Join-Path $root $name) -PathType Leaf)){throw ('Missing file: '+$name)}}
 $manifest=Join-Path $root 'SHA256-MANIFEST.json'
 $manifestLock=[IO.File]::Open($manifest,'Open','Read','Read');$locks.Add($manifestLock)
 if($manifestLock.Length -gt 1048576 -or (HashStream $manifestLock) -ne $ExpectedManifestSha256){throw 'Manifest digest or length mismatch'}
 $reader=[IO.StreamReader]::new($manifestLock,[Text.UTF8Encoding]::new($false,$true),$false,1024,$true)
 try{$manifestText=$reader.ReadToEnd()}finally{$reader.Dispose();$manifestLock.Position=0}
 $rows=ConvertFrom-Json -InputObject $manifestText -NoEnumerate -Depth 8
 if($rows -isnot [Array] -or $rows.Count -ne $expected.Count){throw 'Manifest count/type mismatch'}
 $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
 $total=0L
 foreach($row in $rows){
  if($row.path -isnot [string] -or $row.path -cnotin $expected -or !$seen.Add($row.path) -or $row.sha256 -isnot [string] -or $row.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or ($row.size_bytes -isnot [long] -and $row.size_bytes -isnot [int]) -or $row.size_bytes -le 0 -or $row.size_bytes -gt 16777216){throw 'Invalid manifest record'}
  if(@($row.PSObject.Properties.Name | Where-Object {$_ -notin @('path','sha256','size_bytes')}).Count){throw 'Unexpected manifest record field'}
  $file=Join-Path $root $row.path;AssertPlain $file
  $stream=[IO.File]::Open($file,'Open','Read','Read');$locks.Add($stream)
  $total+=$stream.Length
  if($total -gt 33554432 -or $stream.Length -ne $row.size_bytes -or (HashStream $stream) -ne $row.sha256){throw 'Payload size/hash mismatch'}
 }
 if($seen.Count -ne $expected.Count){throw 'Required payload omitted'}
 $policy=Get-Content -LiteralPath (Join-Path $root 'RECONNECT-ENROLLMENT.json') -Raw | ConvertFrom-Json -Depth 8
 if($policy -isnot [pscustomobject] -or @($policy.PSObject.Properties).Count -ne 4){throw 'Invalid enrollment policy object'}
 foreach($key in @('mode','quiet_host','external_runner_sha256')){if($policy.$key -isnot [string]){throw 'Policy strings required'}}
 if($policy.schema -isnot [long] -and $policy.schema -isnot [int]){throw 'Policy schema integer required'}
 if($policy.schema -ne 1 -or $policy.mode -cne 'reconnect' -or $policy.quiet_host -cne 'Prometheus-QuietHost-v3.exe' -or $policy.external_runner_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Policy identity invalid'}
 $thumb=$null
 if($Operation -eq 'Sign'){
  AssertPlain $library
  $libraryLock=[IO.File]::Open($library,'Open','Read','Read');$locks.Add($libraryLock)
  if((HashStream $libraryLock) -ne $librarySha){throw 'Signing library changed'}
  # Preserve reviewed arguments across the library's dot-sourced parameter scope.
  function SignExisting([string]$FrozenManifest,[string]$FrozenHash,[string]$FrozenRoot,[string]$PinnedLibrary){
   . $PinnedLibrary
   $result=Write-PrometheusManifestSignature -ManifestPath $FrozenManifest -ExpectedSha256 $FrozenHash -SignaturePath (Join-Path $FrozenRoot 'SHA256-MANIFEST.sig') -CertificatePath (Join-Path $FrozenRoot 'MANIFEST-PUBLIC.cer')
   if($result.thumbprint -ne '1E0F99439DD072E014D81C5574B325C97EA4AC98'){throw 'Unexpected existing signing identity; do not activate'}
  }
  SignExisting $manifest $ExpectedManifestSha256 $root $library
  $thumb=VerifyPublic $root $manifest
 }elseif($Operation -eq 'Verify'){$thumb=VerifyPublic $root $manifest}
 [ordered]@{operation=$Operation;package_kind=$PackageKind;payload_verified=$true;signature_verified=($Operation -ne 'Validate');manifest_sha256=$ExpectedManifestSha256;files=$seen.Count;payload_bytes=$total;thumbprint=$thumb;activated=$false;private_key_exported=$false}|ConvertTo-Json
}finally{foreach($stream in $locks){$stream.Dispose()}}
