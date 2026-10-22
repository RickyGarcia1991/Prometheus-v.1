param([string]$Source,[string]$Scratch,[string]$Packages,[switch]$ReloadOnly)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -Global -ErrorAction Stop
if(!$ReloadOnly){New-Item -ItemType Directory -Path $Scratch -ErrorAction Stop|Out-Null}
$SecurityState=Join-Path $Scratch 'security-state.json'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse failed'}
foreach($name in @('Test-ProtectedFile','Protect-File','Get-SecurityState','Set-SecurityState','Convert-Version','Assert-ControllerProvenance','Get-Package','Sync-Controller','Test-CachedController')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing function'}
 . ([scriptblock]::Create($f[0].Extent.Text))
}
function Write-Audit {param($a,$b,$c)}
function Start-Process {throw 'Forbidden launch'}
$TrustedThumbprint=@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98')
$recovery=Join-Path $Packages 'recovery-at-new-floor'
$candidate=Join-Path $Packages 'candidate-after-recovery'

$ControllerRoot=Join-Path $Scratch 'Controllers'
$resolved=[IO.Path]::GetFullPath($ControllerRoot)
if(!$resolved.StartsWith([IO.Path]::GetFullPath($Scratch).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe test cache'}
$checks=@()
if(!$ReloadOnly){
 $old=Get-Package $candidate
 $oldDst=Sync-Controller $old
 if(!(Test-CachedController $old $oldDst)){throw 'Initial cache mismatch'}
 $checks+='candidate-cache-installed-and-verified'
 $pkg=Get-Package $recovery
 $originalSet=${function:Set-SecurityState}
 function Set-SecurityState([version]$Version){& $originalSet $Version;throw 'Injected interruption after floor commit'}
 $failed=$false
 try{$null=Sync-Controller $pkg}catch{if($_.Exception.Message -notlike '*Injected interruption*'){throw};$failed=$true}
 if(!$failed){throw 'Fault was not exercised'}
 $checks+='interrupted-after-cache-and-floor-publication'
 ${function:Set-SecurityState}=$originalSet
 if(!(Test-CachedController $old $oldDst)){throw 'Prior cache not preserved'}
 $checks+='prior-cache-preserved'
}
$pkg=Get-Package $recovery
$dst=Sync-Controller $pkg
if(!(Test-CachedController $pkg $dst)){throw 'Recovery cache mismatch'}
if((Get-SecurityState).highest_controller_version -ne '0.5.12'){throw 'Floor not retained'}
$checks+='recovery-retry-verifies-cache-and-floor'
$blocked=$false
try{$null=Get-Package $candidate}catch{if($_.Exception.Message -notlike '*Anti-rollback blocked*'){throw};$blocked=$true}
if(!$blocked){throw 'Downgrade accepted'}
$checks+='older-candidate-refused'
if(@(Get-ChildItem $ControllerRoot -Directory -Filter '*.staging-*').Count){throw 'Staging residue'}
$checks+='no-incomplete-staging-directory'
@{passed=$true;host_version=$PSVersionTable.PSVersion.ToString();reload_only=[bool]$ReloadOnly;source_sha256=(Get-FileHash $Source).Hash;checks=$checks;scope='Isolated signed cache synchronization and persisted-floor recovery; no live pointer, process launch, activation or physical lifecycle'}|ConvertTo-Json
