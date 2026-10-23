param([string]$Source,[string]$Scratch,[string]$Packages,[switch]$ReloadOnly)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -Global -ErrorAction Stop
if(!$ReloadOnly){New-Item -ItemType Directory -Path $Scratch -ErrorAction Stop|Out-Null}
$SecurityState=Join-Path $Scratch 'security-state.json'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse failed'}
foreach($name in @('Test-ProtectedFile','Protect-File','Get-SecurityState','Set-SecurityState','Convert-Version','Assert-ControllerProvenance','Get-Package')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing function'}
 . ([scriptblock]::Create($f[0].Extent.Text))
}
function Write-Audit {param($a,$b,$c)}
function Start-Process {throw 'Forbidden launch'}
$TrustedThumbprint=@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98')
$recovery=Join-Path $Packages 'recovery-at-new-floor'
$candidate=Join-Path $Packages 'candidate-after-recovery'
$checks=@()
if(!$ReloadOnly){
 Set-SecurityState ([version]'0.5.11')
 if((Get-Package $recovery).Version -ne [version]'0.5.12'){throw 'Signed recovery rejected'}
 $checks+='signed-recovery-with-real-floor'
 $original=${function:Test-ProtectedFile};$script:inject=$true
 function Test-ProtectedFile([string]$Path){
  $ok=& $original $Path
  if($script:inject -and $ok -and $Path -eq ($SecurityState+'.bak2') -and (Get-Content $Path -Raw|ConvertFrom-Json).highest_controller_version -eq '0.5.12'){$script:inject=$false;throw 'Injected interruption after verified backup'}
  return $ok
 }
 $failed=$false
 try{Set-SecurityState ([version]'0.5.12')}catch{if($_.Exception.Message -notlike '*Injected interruption*'){throw};$failed=$true}
 if(!$failed){throw 'Interruption not reached'}
 $checks+='interrupted-publication-reached'
}
$blocked=$false
try{$null=Get-Package $candidate}catch{if($_.Exception.Message -notlike '*Anti-rollback blocked*'){throw};$blocked=$true}
if(!$blocked){throw 'Old candidate accepted'}
$checks+='old-candidate-blocked-by-persisted-floor'
if((Get-SecurityState).highest_controller_version -ne '0.5.12'){throw 'Floor lost'}
if(!(Test-ProtectedFile $SecurityState)){throw 'Recovery pair invalid'}
$checks+='persisted-floor-and-checksum-valid'
if((Get-Package $recovery).Version -ne [version]'0.5.12'){throw 'Recovery no longer accepted'}
$checks+='signed-recovery-accepted-after-interruption'
$before=(Get-FileHash $SecurityState).Hash
$blocked=$false
try{Set-SecurityState ([version]'0.5.11')}catch{$blocked=$true}
if(!$blocked -or (Get-FileHash $SecurityState).Hash -ne $before){throw 'Downgrade changed state'}
$checks+='downgrade-write-refused-without-state-change'
@{passed=$true;host_version=$PSVersionTable.PSVersion.ToString();reload_only=[bool]$ReloadOnly;source_sha256=(Get-FileHash $Source).Hash;checks=$checks;scope='Isolated real file persistence with signed packages; no live state, task, controller activation or physical lifecycle mutation'}|ConvertTo-Json



