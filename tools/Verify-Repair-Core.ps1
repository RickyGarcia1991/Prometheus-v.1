param([Parameter(Mandatory=$true)][string]$Root,[string]$Recovery,[switch]$Repair)
$ErrorActionPreference='Stop'
$trusted='898F702114B0F889763589C4057DC19CF1657CD5'
function SafePath([string]$base,[string]$relative){
 if(!$relative -or $relative -match '(^|[/\\])\.\.?([/\\]|$)|[:\x00-\x1f]' -or [IO.Path]::IsPathRooted($relative)){throw 'Unsafe manifest path'}
 foreach($part in ($relative -split '[/\\]')){if(!$part -or $part -match '[. ]$|^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)'){throw 'Invalid manifest component'}}
 $base=[IO.Path]::GetFullPath($base).TrimEnd('\');$path=[IO.Path]::GetFullPath((Join-Path $base $relative))
 if(!$path.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Manifest path escapes root'}
 $cursor=$path
 while($cursor){
  if(Test-Path -LiteralPath $cursor){if((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked paths are refused'}}
  $parent=[IO.Path]::GetDirectoryName($cursor);if($parent -eq $cursor){break};$cursor=$parent
 }
 return $path
}
function Manifest([string]$directory){
 $path=SafePath $directory 'SHA256-MANIFEST.json'
 if((Get-Item -LiteralPath $path).Length -gt 1000000){throw 'Manifest too large'}
 $certPath=SafePath $directory 'MANIFEST-PUBLIC.cer'
 $cert=New-Object Security.Cryptography.X509Certificates.X509Certificate2($certPath)
 try{
  if($cert.Thumbprint -ne $trusted){throw 'Untrusted core signing identity'}
  $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert)
  try{$ok=$rsa.VerifyData([IO.File]::ReadAllBytes($path),[IO.File]::ReadAllBytes((SafePath $directory 'SHA256-MANIFEST.sig')),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)}finally{$rsa.Dispose()}
  if(!$ok){throw 'Invalid core manifest signature'}
 }finally{$cert.Dispose()}
 $rows=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
 if(!$rows.Count -or $rows.Count -gt 4096){throw 'Invalid core manifest size'}
 $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
 foreach($entry in $rows){
  $relative=[string]$entry.path;$null=SafePath $directory $relative
  if($relative -match '(^|[/\\])(data|\.git|\.ollama|memory)([/\\]|$)|\.sqlite3?$|\.db$' -or $relative -in @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')){throw 'Manifest includes protected data or signature metadata'}
  if(!$seen.Add($relative.Replace('\','/')) -or $entry.sha256 -notmatch '^[a-fA-F0-9]{64}$'){throw 'Invalid or duplicate manifest entry'}
 }
 if(!$seen.Contains('prometheus.py') -or !$seen.Contains('src/prometheus_assistant/cli.py') -or !$seen.Contains('tools/Verify-Repair-Core.ps1')){throw 'Core manifest omits a required entry'}
 return $rows
}
function Matches([string]$path,$entry){return (Test-Path -LiteralPath $path -PathType Leaf) -and (Get-Item -LiteralPath $path).Length -eq $entry.size_bytes -and (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $entry.sha256}
try{
 $Root=[IO.Path]::GetFullPath($Root);$entries=@(Manifest $Root);$bad=@();$repaired=@()
 # Check the release tree, including the entry script's import directory,
 # for injected unsigned files, without following links or quarantined files.
 $expected=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
 foreach($e in $entries){$null=$expected.Add(([string]$e.path).Replace('\','/'))}
 foreach($name in @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')){$null=$expected.Add($name)}
 $queue=New-Object 'System.Collections.Generic.Queue[string]';$queue.Enqueue($Root);$count=0
 while($queue.Count){foreach($item in (Get-ChildItem -LiteralPath $queue.Dequeue() -Force)){
  $count++;if($count -gt 8192){throw 'Core tree exceeds its scan limit'}
  if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked code paths are refused'}
  if($item.PSIsContainer){if($item.FullName -ne (Join-Path $Root 'repair-history')){$queue.Enqueue($item.FullName)}}else{
   $rel=$item.FullName.Substring($Root.TrimEnd('\').Length+1).Replace('\','/')
   if(!$expected.Contains($rel)){throw ('Unsigned file in code search path: '+$rel)}
  }
 }}
 foreach($entry in $entries){if(!(Matches (SafePath $Root $entry.path) $entry)){$bad+=$entry}}
 if($bad.Count -and $Repair){
  if(!$Recovery){throw 'Verified recovery directory is required'}
  $Recovery=[IO.Path]::GetFullPath($Recovery);$null=Manifest $Recovery
  if((Get-FileHash -LiteralPath (SafePath $Root 'SHA256-MANIFEST.json')).Hash -ne (Get-FileHash -LiteralPath (SafePath $Recovery 'SHA256-MANIFEST.json')).Hash){throw 'Recovery edition does not match active core'}
  # Validate every needed backup before writing anything.
  foreach($entry in $bad){if(!(Matches (SafePath $Recovery $entry.path) $entry)){throw ('Recovery file failed verification: '+$entry.path)}}
  foreach($entry in $bad){
   $target=SafePath $Root $entry.path;$source=SafePath $Recovery $entry.path
   $parent=Split-Path $target -Parent;[IO.Directory]::CreateDirectory($parent)|Out-Null
   $temporary=SafePath $Root ($entry.path+'.repair-'+[guid]::NewGuid().ToString('N'))
   $quarantine=SafePath $Root ('repair-history/'+(Get-Date -Format 'yyyyMMddHHmmssfff')+'-'+[guid]::NewGuid().ToString('N')+'/'+$entry.path)
   [IO.File]::Copy($source,$temporary,$false)
   if(!(Matches $temporary $entry)){throw 'Staged repair hash mismatch'}
   if(Test-Path -LiteralPath $target){
    [IO.Directory]::CreateDirectory((Split-Path $quarantine -Parent))|Out-Null
    [IO.File]::Replace($temporary,$target,$quarantine,$true)
   }else{[IO.File]::Move($temporary,$target)}
   if(!(Matches $target $entry)){throw 'Final repair verification failed'}
   $repaired+=$entry.path
  }
 }
 $remaining=@($entries|Where-Object {!(Matches (SafePath $Root $_.path) $_)})
 [pscustomobject]@{signature_verified=$true;files=$entries.Count;healthy=($remaining.Count -eq 0);damaged_before=@($bad|ForEach-Object {$_.path});repaired=$repaired;memory_modified=$false;network_used=$false}|ConvertTo-Json -Depth 5 -Compress
 if($remaining.Count){exit 2};exit 0
}catch{Write-Error ('Core integrity check stopped: '+$_.Exception.Message) -ErrorAction Continue;exit 3}
