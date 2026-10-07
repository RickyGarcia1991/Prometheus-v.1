param([ValidateSet('Run','Verify')][string]$Mode='Run')
$ErrorActionPreference='Stop'
$HostRoot=Join-Path $env:LOCALAPPDATA 'Prometheus'
$Log=Join-Path $HostRoot 'host-agent.log'
$EjectLock=Join-Path $HostRoot 'eject-mode.json'
$ControllerRoot=Join-Path $HostRoot 'Controllers'
function Write-Log([string]$Level,[string]$Message){New-Item -ItemType Directory -Force $HostRoot|Out-Null;Add-Content -Path $Log -Value ((Get-Date -Format o)+' '+$Level+' '+$Message)}
function Find-PrometheusVolume {@(Get-CimInstance Win32_LogicalDisk|Where-Object {$_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -eq 3})}
function Test-EjectSuppressed {
 if(!(Test-Path $EjectLock)){return $false}
 try{$lock=Get-Content $EjectLock -Raw|ConvertFrom-Json;if(!$lock.active){return $false};$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime;$started=[datetime]$lock.started;if($started -lt $boot){Remove-Item $EjectLock -Force -ErrorAction SilentlyContinue;Write-Log 'info' 'cleared-stale-eject-lock-after-reboot';return $false};return $true}catch{return $true}
}
function Get-Package($root){
 $metaPath=Join-Path $root 'PROMETHEUS-SSD-STATUS.json';if(!(Test-Path $metaPath)){throw 'SSD metadata missing'}
 $meta=Get-Content $metaPath -Raw|ConvertFrom-Json;$name=Split-Path ([string]$meta.controller_release) -Leaf
 if($name -notmatch '^Prometheus-Controller-v[0-9.]+$'){throw 'Untrusted controller release name'}
 $src=Join-Path $root $name;$manifest=Join-Path $src 'SHA256-MANIFEST.json';if(!(Test-Path $manifest)){throw 'Controller manifest missing'}
 $sigPath=Join-Path $src 'SHA256-MANIFEST.sig';$certPath=Join-Path $src 'MANIFEST-PUBLIC.cer';if(!(Test-Path $sigPath) -or !(Test-Path $certPath)){throw 'Signed controller manifest required'}
 $cert=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath);if($cert.Thumbprint -ne '898F702114B0F889763589C4057DC19CF1657CD5'){throw 'Controller signing certificate is not trusted'};$rsa=[System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($cert);$ok=$rsa.VerifyData([IO.File]::ReadAllBytes($manifest),[IO.File]::ReadAllBytes($sigPath),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1);$rsa.Dispose();$cert.Dispose();if(!$ok){throw 'Controller manifest signature invalid'}
 $entries=Get-Content $manifest -Raw|ConvertFrom-Json
 foreach($e in $entries){$f=Join-Path $src ([string]$e.path);if(!(Test-Path $f)){throw ('Integrity failure: '+$e.path)};if((Get-FileHash $f -Algorithm SHA256).Hash -ne ([string]$e.sha256)){throw ('Integrity failure: '+$e.path)}}
 [pscustomobject]@{Meta=$meta;Name=$name;Source=$src;Manifest=$manifest}
}
function Sync-Controller($pkg){
 New-Item -ItemType Directory -Force $ControllerRoot|Out-Null
 $dst=Join-Path $ControllerRoot $pkg.Name;$stage=$dst+'.staging-'+[guid]::NewGuid().ToString('N');$backup=$dst+'.previous'
 Copy-Item $pkg.Source $stage -Recurse -Force
 $entries=Get-Content (Join-Path $stage 'SHA256-MANIFEST.json') -Raw|ConvertFrom-Json
 foreach($e in $entries){$f=Join-Path $stage ([string]$e.path);if(!(Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne ([string]$e.sha256)){Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue;throw ('Staged integrity failure: '+$e.path)}}
 if(Test-Path $backup){Remove-Item $backup -Recurse -Force}
 if(Test-Path $dst){Move-Item $dst $backup}
 Move-Item $stage $dst
 return $dst
}
function Invoke-Agent {
 if(Test-EjectSuppressed){Write-Log 'info' 'suppressed eject-in-progress';return 0}
 $vols=@(Find-PrometheusVolume);if($vols.Count -eq 0){Write-Log 'info' 'no-prometheus-volume';return 0}
 $fail=0
 foreach($d in $vols){try{$root=$d.DeviceID+'\';$pkg=Get-Package $root;$running=@(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue);$dst=Join-Path $ControllerRoot $pkg.Name
   if(!$running.Count){$dst=Sync-Controller $pkg}else{Write-Log 'info' ('update-deferred controller-running '+$pkg.Name)}
   $exe=Join-Path $dst 'Prometheus.DriveController.exe';if(!(Test-Path $exe)){throw 'Cached controller executable missing'}
   if(!$running.Count){Start-Process $exe -WorkingDirectory $dst;Start-Sleep -Milliseconds 500;if(!@(Get-Process Prometheus.DriveController -ErrorAction SilentlyContinue).Count){throw 'Controller failed to start'}}
   Write-Log 'healthy' ($d.DeviceID+' '+$pkg.Name)
 }catch{$fail=1;Write-Log 'error' $_.Exception.Message}}
 return $fail
}
$rc=Invoke-Agent
if($Mode -eq 'Verify'){if($rc -eq 0){Write-Output 'Prometheus Host Agent verification passed.'}else{Write-Error 'Prometheus Host Agent verification failed.'}}
exit $rc
