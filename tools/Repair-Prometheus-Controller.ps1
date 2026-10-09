param(
 [string]$Root=(Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
 [string]$CacheRoot=(Join-Path $env:LOCALAPPDATA 'Prometheus\Controllers'),
 [string]$ControllerRelease='',
 [switch]$VerifyOnly,
 [switch]$Launch
)
$ErrorActionPreference='Stop'
$rootPath=[IO.Path]::GetFullPath($Root)
$meta=Get-Content -LiteralPath (Join-Path $rootPath 'PROMETHEUS-SSD-STATUS.json') -Raw|ConvertFrom-Json
$name=if($ControllerRelease){$ControllerRelease}else{Split-Path ([string]$meta.controller_release) -Leaf}
if($name -notmatch '^Prometheus-Controller-v[0-9]+\.[0-9]+\.[0-9]+$'){throw 'Invalid controller release name'}
$src=Join-Path $rootPath $name
$manifest=Join-Path $src 'SHA256-MANIFEST.json'
$cert=New-Object Security.Cryptography.X509Certificates.X509Certificate2((Join-Path $src 'MANIFEST-PUBLIC.cer'))
try {
 if($cert.Thumbprint -ne '898F702114B0F889763589C4057DC19CF1657CD5'){throw 'Controller certificate is not trusted'}
 $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert)
 try {$valid=$rsa.VerifyData([IO.File]::ReadAllBytes($manifest),[IO.File]::ReadAllBytes((Join-Path $src 'SHA256-MANIFEST.sig')),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)}finally{$rsa.Dispose()}
 if(!$valid){throw 'Controller manifest signature is invalid'}
} finally {$cert.Dispose()}
$entries=Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json
if(!$entries.Count){throw 'Empty controller manifest'}
$names=@()
foreach($entry in $entries){
 $leaf=[string]$entry.path
 if(!$leaf -or $leaf -ne [IO.Path]::GetFileName($leaf) -or $leaf -in @('.','..') -or $leaf -match '[:/\\]' -or $names -contains $leaf){throw 'Invalid or duplicate controller manifest path'}
 $names+=$leaf
 if((Get-FileHash -LiteralPath (Join-Path $src $leaf) -Algorithm SHA256).Hash -ne $entry.sha256){throw ('Controller integrity failure: '+$leaf)}
}
foreach($required in @('Prometheus.DriveController.exe','Prometheus.DriveController.dll','Prometheus.DriveController.deps.json','Prometheus.DriveController.runtimeconfig.json','Prometheus-Drive-Engine.ps1','Prometheus-Eject-Orchestrator.ps1','Prometheus-Safe-Eject.ps1')){if($names -notcontains $required){throw ('Required payload omitted: '+$required)}}
$cache=[IO.Path]::GetFullPath($CacheRoot).TrimEnd('\')
$dst=[IO.Path]::GetFullPath((Join-Path $cache $name))
if(!$dst.StartsWith($cache+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Cache destination escapes controller root'}
$copyNames=$names+@('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')
function TestCopy([string]$directory){
 foreach($leaf in $copyNames){$path=Join-Path $directory $leaf;if(!(Test-Path -LiteralPath $path -PathType Leaf)){return $false};if((Get-FileHash -LiteralPath $path).Hash -ne (Get-FileHash -LiteralPath (Join-Path $src $leaf)).Hash){return $false}}
 return $true
}
$before=TestCopy $dst
if($VerifyOnly){[pscustomobject]@{signed_source_valid=$true;cache_matches=$before;controller=$name}|ConvertTo-Json;if(!$before){exit 2};exit 0}
if(!$before){
 $usingCache=@(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue | Where-Object {!$_.Path -or [IO.Path]::GetDirectoryName($_.Path) -eq $dst})
 if($usingCache.Count){throw 'Close the controller normally before repairing its cache; no process was terminated'}
 New-Item -ItemType Directory -Force -Path $cache|Out-Null
 $stage=$dst+'.staging-'+[guid]::NewGuid().ToString('N')
 $backup=$dst+'.before-repair-'+(Get-Date -Format 'yyyyMMddHHmmssfff')
 if(!$stage.StartsWith($cache+'\') -or !$backup.StartsWith($cache+'\')){throw 'Unsafe staging path'}
 New-Item -ItemType Directory -Path $stage|Out-Null
 foreach($leaf in $copyNames){Copy-Item -LiteralPath (Join-Path $src $leaf) -Destination (Join-Path $stage $leaf)}
 if(!(TestCopy $stage)){throw 'Staged controller copy failed verification'}
 $moved=$false
 try {
  if(Test-Path -LiteralPath $dst){Move-Item -LiteralPath $dst -Destination $backup;$moved=$true}
  Move-Item -LiteralPath $stage -Destination $dst
 } catch {if($moved -and !(Test-Path -LiteralPath $dst)){Move-Item -LiteralPath $backup -Destination $dst};throw}
}
if(!(TestCopy $dst)){throw 'Final controller verification failed'}
if($Launch){
 # An explicit manual launch is the user's request to reopen after closing.
 $flag=Join-Path $env:LOCALAPPDATA 'Prometheus\controller-intentional-close.flag'
 Remove-Item -LiteralPath $flag -ErrorAction SilentlyContinue
 if(!@(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue).Count){Start-Process (Join-Path $dst 'Prometheus.DriveController.exe') -WorkingDirectory $dst}
}
[pscustomobject]@{signed_source_valid=$true;cache_matches=$true;cache_repaired=(!$before);launch_requested=[bool]$Launch;controller=$name}|ConvertTo-Json
