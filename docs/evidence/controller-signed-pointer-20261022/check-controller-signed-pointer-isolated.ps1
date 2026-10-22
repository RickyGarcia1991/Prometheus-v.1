param([string]$Source,[string]$Scratch,[ValidateSet('verify','interrupt','resume')][string]$Mode)
$ErrorActionPreference='Stop'
$resolved=[IO.Path]::GetFullPath($Scratch).TrimEnd('\')
$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if($resolved -cne $Scratch.TrimEnd('\') -or !$resolved.StartsWith($temp+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolved -Leaf) -notlike 'prometheus-signed-pointer-*'){throw 'Generated temporary root required'}
$part=Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
while($part){if($part.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse root refused'};$part=$part.Parent}
if((Get-FileHash $Source).Hash -ine '26f47da1f529f7d7b2b469cbc30b4376ee8b82be3f624bf2a3247e69bfc896a5'){throw 'HostAgent source changed'}
$SecurityState=Join-Path $Scratch 'security-state.json'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($name in @('Test-ProtectedFile','Protect-File','Get-SecurityState','Set-SecurityState','Convert-Version','Assert-ControllerProvenance','Get-Package')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing extracted function'}
 . ([scriptblock]::Create($f[0].Extent.Text))
}
function Write-Audit {param($a,$b,$c)}
function Start-Process {throw 'Forbidden launch'}
$TrustedThumbprint=@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98')
$candidate=Join-Path $Scratch 'candidate'
$recovery=Join-Path $Scratch 'recovery'
if($Mode -eq 'verify'){
 Set-SecurityState ([version]'0.5.10')
 $before=(Get-FileHash $SecurityState).Hash
 $checks=@()
 foreach($pair in @(@($candidate,'0.5.11'),@($recovery,'0.5.12'))){
  $pkg=Get-Package $pair[0]
  if($pkg.Version.ToString() -ne $pair[1]){throw 'Version mismatch'}
  $stage=Join-Path $Scratch ('stage-'+$pair[1]);Copy-Item -LiteralPath $pair[0] -Destination $stage -Recurse
  $verified=Get-Package $stage
  if($verified.Version -ne $pkg.Version){throw 'Stage version mismatch'}
  if((Get-FileHash $SecurityState).Hash -ne $before){throw 'Stage advanced floor'}
  $checks+=('signed-source-and-stage-'+$pair[1])
 }
 $stage=Join-Path $Scratch 'stage-0.5.11'
 $pkgdir=Join-Path $stage 'Prometheus-Controller-v0.5.11'
 foreach($name in @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','RELEASE-PROVENANCE.json')){
  $path=Join-Path $pkgdir $name;$saved=[IO.File]::ReadAllBytes($path)
  try{[IO.File]::WriteAllBytes($path,([byte[]]@(1,2,3)));$refused=$false;try{$null=Get-Package $stage}catch{$refused=$true};if(!$refused){throw 'Tampered closure accepted'}}finally{[IO.File]::WriteAllBytes($path,$saved)}
  $checks+=('tamper-refused-'+$name)
 }
 if((Get-FileHash $SecurityState).Hash -ne $before){throw 'Verification mutated floor'}
 @{checks=$checks;floor=(Get-SecurityState).highest_controller_version;runtime=$PSVersionTable.PSVersion.ToString();launch=$false}|ConvertTo-Json
}elseif($Mode -eq 'interrupt'){
 $null=Get-Package $recovery
 $original=${function:Test-ProtectedFile}
 function Test-ProtectedFile([string]$Path){
  $ok=& $original $Path
  if($ok -and $Path -eq ($SecurityState+'.bak2') -and ((Get-Content $Path -Raw|ConvertFrom-Json).highest_controller_version -eq '0.5.12')){[Environment]::Exit(77)}
  return $ok
 }
 Set-SecurityState ([version]'0.5.12')
 throw 'Interruption not reached'
}else{
 $floor=(Get-SecurityState).highest_controller_version
 if($floor -ne '0.5.12'){throw 'Recovery slot did not restore floor'}
 $null=Get-Package $recovery
 $blocked=$false;try{$null=Get-Package $candidate}catch{if($_.Exception.Message -notlike '*Anti-rollback blocked*'){throw};$blocked=$true}
 if(!$blocked){throw 'Candidate downgrade accepted'}
 Set-SecurityState ([version]'0.5.12')
 @{floor=$floor;candidate_blocked=$true;runtime=$PSVersionTable.PSVersion.ToString();launch=$false}|ConvertTo-Json
}
