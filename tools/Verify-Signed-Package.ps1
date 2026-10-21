param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
try {
 $base=[IO.Path]::GetFullPath($Root).TrimEnd('\')
 if(!(Test-Path -LiteralPath $base -PathType Container)){throw 'Package directory missing'}
 $kind=Split-Path $base -Leaf
 $required=switch -Regex ($kind){
  '^Prometheus-Controller-v[0-9.]+$' {@('Prometheus.DriveController.exe','Prometheus.DriveController.dll','Prometheus.DriveController.deps.json','Prometheus.DriveController.runtimeconfig.json','Prometheus-Controller-Common.ps1','Prometheus-Drive-Engine.ps1','Prometheus-Eject-Orchestrator.ps1','Prometheus-Safe-Eject.ps1','Prometheus-Lifecycle.ps1','verify-portable-memory.py');break}
  '^Prometheus-Host-Agent-v[0-9.]+$' {@('Prometheus-Host-Agent.ps1','Prometheus-QuietHost-v2.exe','Prometheus-Lifecycle.ps1','Prometheus-Arrival.ps1','Prometheus-USB-Reconnect-Supervisor.ps1','Install-Lifecycle-Tasks.ps1');break}
  '^Prometheus-Maps-Studio-v[0-9.]+$' {@('studio.py','index.html','studio.js','money.js','catalog.json','runtime-pins.json','Start-Studio.ps1');break}
  default {throw 'Unknown package type'}
 }
 function CheckPath([string]$Path){
  $item=Get-Item -LiteralPath $Path -Force
  while($item){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked package path refused'};if($item.FullName.TrimEnd('\') -eq $base){break};$item=$item.Parent;if(!$item){$item=(Get-Item -LiteralPath $Path).Directory}}
 }
 CheckPath $base
 $manifest=Join-Path $base 'SHA256-MANIFEST.json'
 if((Get-Item -LiteralPath $manifest).Length -gt 8MB){throw 'Manifest too large'}
 foreach($name in @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')){CheckPath (Join-Path $base $name)}
 $cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $base 'MANIFEST-PUBLIC.cer'))
 if($cert.Thumbprint -ne '898F702114B0F889763589C4057DC19CF1657CD5'){throw 'Untrusted signing identity'}
 $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert)
 try{$valid=$rsa.VerifyData([IO.File]::ReadAllBytes($manifest),[IO.File]::ReadAllBytes((Join-Path $base 'SHA256-MANIFEST.sig')),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)}finally{$rsa.Dispose();$cert.Dispose()}
 if(!$valid){throw 'Invalid package signature'}
 # Windows PowerShell 5.1 emits JSON arrays as one pipeline object. Assign
 # before enumerating so an entry never becomes a concatenation of paths.
 $decoded=Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json
 $entries=@($decoded)
 if(!$entries.Count -or $entries.Count -gt 10000){throw 'Invalid manifest entry count'}
 $names=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
 foreach($entry in $entries){
  $relative=[string]$entry.path
  if(!$relative -or [IO.Path]::IsPathRooted($relative) -or $relative.Contains(':') -or ($relative -split '[/\\]') -contains '..' -or ($relative -split '[/\\]') -contains '.' -or !$names.Add($relative.Replace('/','\'))){throw 'Invalid or duplicate manifest path'}
  $path=[IO.Path]::GetFullPath((Join-Path $base $relative))
  if(!$path.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Manifest path leaves package'}
  CheckPath $path
  $file=Get-Item -LiteralPath $path -Force
  if($file.PSIsContainer -or $null -eq $entry.size_bytes -or $file.Length -ne [long]$entry.size_bytes -or [string]$entry.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $entry.sha256){throw ('Payload integrity failed: '+$relative)}
 }
 foreach($name in $required){if(!$names.Contains($name)){throw ('Required payload missing: '+$name)}}
 function CheckTree([string]$Directory){
  foreach($item in Get-ChildItem -LiteralPath $Directory -Force){
   if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked package entry refused'}
   if($item.PSIsContainer){CheckTree $item.FullName;continue}
   $relative=$item.FullName.Substring($base.Length+1)
   if($relative -notin @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer') -and !$names.Contains($relative)){throw ('Unsigned package entry: '+$relative)}
  }
 }
 CheckTree $base
 @{healthy=$true;signature_verified=$true;files_checked=$entries.Count;package=$kind}|ConvertTo-Json -Compress
 exit 0
}catch{Write-Output (@{healthy=$false;signature_verified=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress);exit 2}
