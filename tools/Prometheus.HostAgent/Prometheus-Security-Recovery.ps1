param([ValidateSet('VerifyAudit','VerifySnapshots','VerifyState','VerifyAll')][string]$Mode='VerifyAll',[string]$HostRoot=(Join-Path $env:LOCALAPPDATA 'Prometheus'))
$ErrorActionPreference='Stop'
$Audit=Join-Path $HostRoot 'security-audit.jsonl'
$State=Join-Path $HostRoot 'security-state.json'
$Auth=Join-Path $HostRoot 'authorized-host.json'
$Controllers=Join-Path $HostRoot 'Controllers'
function HashText([string]$Text){$sha=[Security.Cryptography.SHA256]::Create();try{([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-',''))}finally{$sha.Dispose()}}
function VerifySidecar([string]$Path){$side=$Path+'.sha256';if(!(Test-Path $Path) -or !(Test-Path $side)){return $false};return ((Get-FileHash $Path -Algorithm SHA256).Hash -eq (Get-Content $side -Raw).Trim())}
function VerifyAudit {
 if(!(Test-Path $Audit)){return [pscustomobject]@{name='audit';ok=$true;records=0;detail='no audit records yet'}}
 $prev='GENESIS';$n=0
 foreach($line in Get-Content $Audit){if(!$line){continue};$n++;try{$r=$line|ConvertFrom-Json}catch{return [pscustomobject]@{name='audit';ok=$false;records=$n;detail='malformed JSON'}}
  if(([string]$r.previous) -ne $prev){return [pscustomobject]@{name='audit';ok=$false;records=$n;detail='chain link mismatch'}}
  $body=[ordered]@{timestamp=$r.timestamp;event=$r.event;result=$r.result;host=$r.host;detail=$r.detail;previous=$r.previous}|ConvertTo-Json -Compress
  if((HashText $body) -ne ([string]$r.hash)){return [pscustomobject]@{name='audit';ok=$false;records=$n;detail='record hash mismatch'}}
  $prev=[string]$r.hash
 }
 [pscustomobject]@{name='audit';ok=$true;records=$n;detail='hash chain verified'}
}
function VerifyState {
 $stateOk=VerifySidecar $State;$authOk=VerifySidecar $Auth
 [pscustomobject]@{name='state';ok=($stateOk -and $authOk);security_state=$stateOk;authorized_host=$authOk;detail=$(if($stateOk -and $authOk){'protected state verified'}else{'protected state missing or corrupt'})}
}
function VerifySnapshots {
 $dirs=@(Get-ChildItem $Controllers -Directory -ErrorAction SilentlyContinue|Where-Object {$_.Name -match '\.recovery-[123]$'});$bad=@()
 foreach($d in $dirs){$m=Join-Path $d.FullName 'SHA256-MANIFEST.json';if(!(Test-Path $m)){$bad+=$d.Name;continue};try{foreach($e in (Get-Content $m -Raw|ConvertFrom-Json)){$f=Join-Path $d.FullName ([string]$e.path);if(!(Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne ([string]$e.sha256)){$bad+=$d.Name;break}}}catch{$bad+=$d.Name}}
 [pscustomobject]@{name='snapshots';ok=($bad.Count -eq 0);generations=$dirs.Count;bad=@($bad);detail=$(if($bad.Count){'snapshot verification failed'}else{'recovery generations verified'})}
}
$results=@();if($Mode -in @('VerifyAudit','VerifyAll')){$results+=VerifyAudit};if($Mode -in @('VerifyState','VerifyAll')){$results+=VerifyState};if($Mode -in @('VerifySnapshots','VerifyAll')){$results+=VerifySnapshots}
$out=[pscustomobject]@{timestamp=(Get-Date -Format o);ok=(@($results|Where-Object {!$_.ok}).Count -eq 0);checks=$results};$out|ConvertTo-Json -Depth 6;if(!$out.ok){exit 2}
