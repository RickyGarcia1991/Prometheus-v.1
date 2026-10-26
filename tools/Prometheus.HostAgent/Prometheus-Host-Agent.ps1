param([ValidateSet('Run','Verify')][string]$Mode='Run')
$ErrorActionPreference='Stop'
$HostRoot=Join-Path $env:LOCALAPPDATA 'Prometheus'
$Log=Join-Path $HostRoot 'host-agent.log'
$AuditLog=Join-Path $HostRoot 'security-audit.jsonl'
$SecurityState=Join-Path $HostRoot 'security-state.json'
$AuthorizedHost=Join-Path $HostRoot 'authorized-host.json'
$EjectLock=Join-Path $HostRoot 'eject-mode.json'
$ControllerRoot=Join-Path $HostRoot 'Controllers'
$TrustedThumbprint=@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98')
$AgentMutex=New-Object Threading.Mutex($false,'Local\PrometheusHostAgent')
function Write-Log([string]$Level,[string]$Message){New-Item -ItemType Directory -Force $HostRoot|Out-Null;Add-Content -Path $Log -Value ((Get-Date -Format o)+' '+$Level+' '+$Message)}
function Get-HostFingerprint {$id=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid;$sha=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($id))).Replace('-',''))}finally{$sha.Dispose()}}
function Write-Audit([string]$Event,[string]$Result,[string]$Detail='') {New-Item -ItemType Directory -Force $HostRoot|Out-Null;$prev='GENESIS';if(Test-Path $AuditLog){$last=Get-Content $AuditLog -Tail 1 -ErrorAction SilentlyContinue;if($last){try{$prev=([string](ConvertFrom-Json $last).hash)}catch{$prev='BROKEN'}}};$record=[ordered]@{timestamp=(Get-Date -Format o);event=$Event;result=$Result;host=(Get-HostFingerprint);detail=$Detail;previous=$prev};$body=$record|ConvertTo-Json -Compress;$sha=[Security.Cryptography.SHA256]::Create();try{$hash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).Replace('-',''))}finally{$sha.Dispose()};$record.hash=$hash;Add-Content $AuditLog ($record|ConvertTo-Json -Compress)}
function Test-ProtectedFile([string]$Path){$side=$Path+'.sha256';if(!(Test-Path $Path) -or !(Test-Path $side)){return $false};return ((Get-FileHash $Path -Algorithm SHA256).Hash -eq (Get-Content $side -Raw).Trim())}
function Protect-File([string]$Path){(Get-FileHash $Path -Algorithm SHA256).Hash|Set-Content ($Path+'.sha256') -Encoding ASCII}
function Assert-AuthorizedHost {if(!(Test-ProtectedFile $AuthorizedHost)){throw 'Host authorization state is missing or corrupt; signed installer repair required'};$a=Get-Content $AuthorizedHost -Raw|ConvertFrom-Json;if(([string]$a.fingerprint) -ne (Get-HostFingerprint)){throw 'Host authorization fingerprint mismatch'}}
function Find-PrometheusVolume {@(Get-CimInstance Win32_LogicalDisk|Where-Object {$_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -eq 3})}
function Test-EjectSuppressed {return (Test-Path -LiteralPath $EjectLock)}
function Convert-Version([string]$Name){if($Name -notmatch '^Prometheus-Controller-v([0-9]+\.[0-9]+\.[0-9]+)$'){throw 'Untrusted controller release name'};return [version]$Matches[1]}
function Assert-ControllerProvenance([string]$Source,$Entries,[string]$Name){
 $covered=@($Entries|Where-Object {$_.path -is [string] -and $_.path -ieq 'RELEASE-PROVENANCE.json'})
 if($covered.Count -ne 1 -or $covered[0].path -cne 'RELEASE-PROVENANCE.json' -or $covered[0].sha256 -isnot [string] -or $covered[0].sha256 -notmatch '^[a-fA-F0-9]{64}$'){throw 'Controller provenance must have one signed manifest entry'}
 $bytes=[IO.File]::ReadAllBytes((Join-Path $Source 'RELEASE-PROVENANCE.json'))
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$actual=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','')}finally{$sha.Dispose()}
 if($actual -ine $covered[0].sha256){throw 'Controller provenance integrity failure'}
 $json=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes).TrimStart([char]0xFEFF).TrimStart()
 if(!$json.StartsWith('{')){throw 'Controller provenance must be an object'}
 $provenance=$json|ConvertFrom-Json
 if($provenance -isnot [pscustomobject]){throw 'Controller provenance must be an object'}
 $legacy=$provenance.PSObject.Properties['release'];$modern=$provenance.PSObject.Properties['release_version']
 if(!$legacy -and !$modern){throw 'Controller provenance release identity missing'}
 if($legacy -and ($legacy.Value -isnot [string] -or $legacy.Value -cne $Name)){throw 'Signed controller release does not match package name'}
 if($modern){
  if($modern.Value -isnot [string] -or $modern.Value -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or ('Prometheus-Controller-v'+$modern.Value) -cne $Name){throw 'Signed controller version does not match package name'}
 }
}
function Get-SecurityState {$candidates=@($SecurityState,($SecurityState+'.bak1'),($SecurityState+'.bak2'));$valid=@();foreach($p in $candidates){if(Test-ProtectedFile $p){try{$x=Get-Content $p -Raw|ConvertFrom-Json;$valid+=[pscustomobject]@{path=$p;version=[version]([string]$x.highest_controller_version);data=$x}}catch{}}};if(!$valid.Count){$historyExists=$false;foreach($p in $candidates){if((Test-Path -LiteralPath $p -ErrorAction Stop) -or (Test-Path -LiteralPath ($p+'.sha256') -ErrorAction Stop)){$historyExists=$true}};if(!$historyExists){return [pscustomobject]@{highest_controller_version='0.0.0'}};throw 'Security state is malformed or failed integrity verification'};$best=$valid|Sort-Object version -Descending|Select-Object -First 1;if($best.path -ne $SecurityState){Copy-Item $best.path $SecurityState -Force;Copy-Item ($best.path+'.sha256') ($SecurityState+'.sha256') -Force;Write-Audit 'security-state-recovery' 'pass' $best.version.ToString()};return $best.data}
function Set-SecurityState([version]$Version){
 # Run is serialized by the HostAgent mutex. Never discard the highest valid floor.
 $current=Get-SecurityState
 if($Version -lt [version]$current.highest_controller_version){throw 'Refusing to lower protected controller version'}
 $body=[ordered]@{highest_controller_version=$Version.ToString();updated=(Get-Date -Format o)}|ConvertTo-Json
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes($body)
 $publish={param([string]$Path,[byte[]]$Bytes)
  $temp=$Path+'.pending-'+[guid]::NewGuid().ToString('N');$side=$temp+'.sha256'
  try{
   $stream=[IO.File]::Open($temp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
   try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
   Protect-File $temp
   $stream=[IO.File]::Open($side,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
   try{$stream.Flush($true)}finally{$stream.Dispose()}
   if([IO.File]::Exists($Path)){[IO.File]::Replace($temp,$Path,[System.Management.Automation.Language.NullString]::Value)}else{[IO.File]::Move($temp,$Path)}
   if([IO.File]::Exists($Path+'.sha256')){[IO.File]::Replace($side,$Path+'.sha256',[System.Management.Automation.Language.NullString]::Value)}else{[IO.File]::Move($side,$Path+'.sha256')}
   if(!(Test-ProtectedFile $Path)){throw 'Protected state readback failed'}
  }finally{foreach($pending in @($temp,$side)){if([IO.File]::Exists($pending)){[IO.File]::Delete($pending)}}}
 }
 if(Test-ProtectedFile $SecurityState){
  & $publish ($SecurityState+'.bak1') ([IO.File]::ReadAllBytes($SecurityState))
 }
 # Publish the new floor to a recovery slot first. The primary stays valid until
 # this pair verifies, and the verified recovery slot survives a primary fault.
 & $publish ($SecurityState+'.bak2') $bytes
 & $publish $SecurityState $bytes
}

function Get-VerifiedControllerPackage($root){
 $metaPath=Join-Path $root 'PROMETHEUS-SSD-STATUS.json';if(!(Test-Path $metaPath)){throw 'SSD metadata missing'};$meta=Get-Content $metaPath -Raw|ConvertFrom-Json;$name=Split-Path ([string]$meta.controller_release) -Leaf;$version=Convert-Version $name
 # Identity verification only: this helper does not authorize admission or launch.
 $src=Join-Path $root $name;$manifest=Join-Path $src 'SHA256-MANIFEST.json';if(!(Test-Path $manifest)){throw 'Controller manifest missing'};$sigPath=Join-Path $src 'SHA256-MANIFEST.sig';$certPath=Join-Path $src 'MANIFEST-PUBLIC.cer';if(!(Test-Path $sigPath) -or !(Test-Path $certPath)){throw 'Signed controller manifest required'}
 $cert=New-Object Security.Cryptography.X509Certificates.X509Certificate2($certPath);if($TrustedThumbprint -notcontains $cert.Thumbprint){throw 'Controller signing certificate is not trusted'};$rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert);try{$ok=$rsa.VerifyData([IO.File]::ReadAllBytes($manifest),[IO.File]::ReadAllBytes($sigPath),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)}finally{$rsa.Dispose();$cert.Dispose()};if(!$ok){throw 'Controller manifest signature invalid'}
 $entries=Get-Content $manifest -Raw|ConvertFrom-Json;foreach($e in $entries){$f=Join-Path $src ([string]$e.path);if(!(Test-Path $f)){throw ('Integrity failure: '+$e.path)};if((Get-FileHash $f -Algorithm SHA256).Hash -ne ([string]$e.sha256)){throw ('Integrity failure: '+$e.path)}};Assert-ControllerProvenance $src $entries $name;[pscustomobject]@{Meta=$meta;Name=$name;Version=$version;Source=$src}
}
function Get-Package($root){
 $pkg=Get-VerifiedControllerPackage $root
 $state=Get-SecurityState;$highest=[version]([string]$state.highest_controller_version)
 if($pkg.Version -lt $highest){throw ('Anti-rollback blocked controller '+$pkg.Version+'; highest trusted is '+$highest)}
 return $pkg
}
function Sync-Controller($pkg){$resolvedRoot=[IO.Path]::GetFullPath($ControllerRoot).TrimEnd('\');$resolvedTarget=[IO.Path]::GetFullPath((Join-Path $ControllerRoot $pkg.Name));if(!$resolvedTarget.StartsWith($resolvedRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe controller cache path'};New-Item -ItemType Directory -Force $ControllerRoot|Out-Null;$dst=Join-Path $ControllerRoot $pkg.Name;$stage=$dst+'.staging-'+[guid]::NewGuid().ToString('N');$backup=$dst+'.previous';$r1=$dst+'.recovery-1';$r2=$dst+'.recovery-2';$r3=$dst+'.recovery-3';Copy-Item $pkg.Source $stage -Recurse -Force
 try{
 # Verify the staged signed bytes before trusting their payload hashes.
 $stagedManifest=[IO.File]::ReadAllBytes((Join-Path $stage 'SHA256-MANIFEST.json'))
 $stagedCert=[Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $stage 'MANIFEST-PUBLIC.cer'))
 try {
  if($TrustedThumbprint -notcontains $stagedCert.Thumbprint){throw 'Staged signing certificate is not trusted'}
  $stagedRsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($stagedCert)
  try {if(!$stagedRsa.VerifyData($stagedManifest,[IO.File]::ReadAllBytes((Join-Path $stage 'SHA256-MANIFEST.sig')),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)){throw 'Staged manifest signature invalid'}}finally{$stagedRsa.Dispose()}
 }finally{$stagedCert.Dispose()}
 $entries=([Text.UTF8Encoding]::new($false,$true)).GetString($stagedManifest).TrimStart([char]0xFEFF)|ConvertFrom-Json
 Assert-ControllerProvenance $stage $entries $pkg.Name
 foreach($e in $entries){$f=Join-Path $stage ([string]$e.path);if(!(Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne ([string]$e.sha256)){throw ('Staged integrity failure: '+$e.path)}};if(Test-Path $r3){Remove-Item $r3 -Recurse -Force};if(Test-Path $r2){Move-Item $r2 $r3};if(Test-Path $r1){Move-Item $r1 $r2};if(Test-Path $dst){Copy-Item $dst $r1 -Recurse -Force};if(Test-Path $backup){Remove-Item $backup -Recurse -Force};if(Test-Path $dst){Move-Item $dst $backup};Move-Item $stage $dst;Set-SecurityState $pkg.Version;Write-Audit 'controller-activation' 'pass' $pkg.Name;return $dst}catch{Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue;if(!(Test-Path $dst) -and (Test-Path $backup)){Move-Item $backup $dst;Write-Audit 'controller-rollback' 'pass' $pkg.Name};throw}
}
function Test-CachedController($pkg,[string]$dst){
 $entries=Get-Content (Join-Path $pkg.Source 'SHA256-MANIFEST.json') -Raw|ConvertFrom-Json
 $names=@($entries|ForEach-Object {[string]$_.path})+@('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')
 foreach($name in $names){$a=Join-Path $pkg.Source $name;$b=Join-Path $dst $name;if(!(Test-Path -LiteralPath $b -PathType Leaf) -or (Get-FileHash -LiteralPath $a).Hash -ne (Get-FileHash -LiteralPath $b).Hash){return $false}}
 return $true
}
function Invoke-Agent {
 if(Test-EjectSuppressed){Write-Log 'info' 'suppressed eject-in-progress';return 0}
 try{Assert-AuthorizedHost}catch{Write-Log 'security' $_.Exception.Message;return 2}
 $vols=@(Find-PrometheusVolume)
 if(!$vols.Count){Write-Log 'info' 'no-prometheus-volume';return 0}
 $fail=0
 foreach($d in $vols){
  try{
   $pkg=Get-Package ($d.DeviceID+'\')
   $dst=Join-Path $ControllerRoot $pkg.Name
   $running=@(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue)
   $matches=Test-CachedController $pkg $dst
   if($Mode -eq 'Verify'){
    if(!$matches){throw 'Signed source is valid but controller cache requires repair'}
    Write-Log 'healthy' ($d.DeviceID+' '+$pkg.Name+' verified without launch')
    continue
   }
   if(!$matches){
    if($running.Count){throw 'Close the controller normally before updating its verified cache'}
    $dst=Sync-Controller $pkg
    if(!(Test-CachedController $pkg $dst)){throw 'Controller cache verification failed after synchronization'}
   }
   if($pkg.Version -gt [version]([string](Get-SecurityState).highest_controller_version)){Set-SecurityState $pkg.Version}
   # A close is intentional until a manual launch explicitly reopens the UI.
   if(Test-Path (Join-Path $HostRoot 'controller-intentional-close.flag')){
    Write-Log 'healthy' ($d.DeviceID+' '+$pkg.Name+' verified; intentional close respected')
    continue
   }
   if(Test-EjectSuppressed){return 0}
   $exe=Join-Path $dst 'Prometheus.DriveController.exe'
   # The user requested a visible controller on SSD arrival; helper shells stay hidden.
   if(!$running.Count){Start-Process $exe -WorkingDirectory $dst -WindowStyle Normal;Start-Sleep -Milliseconds 500;if(!@(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue).Count){throw 'Controller failed to start'}}
   Write-Log 'healthy' ($d.DeviceID+' '+$pkg.Name)
   Write-Audit 'host-agent' 'healthy' $pkg.Name
  }catch{$fail=1;Write-Log 'security' $_.Exception.Message;Write-Audit 'package-validation' 'quarantined' $_.Exception.Message}
 }
 return $fail
}
$acquired=$false
try{$acquired=$AgentMutex.WaitOne(15000);if(!$acquired){Write-Log 'info' 'host-agent-busy';exit 0};$rc=Invoke-Agent;if($Mode -eq 'Verify'){if($rc -eq 0){Write-Output 'Prometheus Host Agent verification passed.'}else{Write-Error 'Prometheus Host Agent verification failed.'}};exit $rc}finally{if($acquired){$AgentMutex.ReleaseMutex()};$AgentMutex.Dispose()}
