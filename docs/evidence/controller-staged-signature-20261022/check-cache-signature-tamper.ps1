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

$pkg=Get-Package $candidate
$originalCopy=Get-Command Copy-Item -CommandType Cmdlet
function Copy-Item {
 param($Path,$Destination,[switch]$Recurse,[switch]$Force)
 & $originalCopy -Path $Path -Destination $Destination -Recurse:$Recurse -Force:$Force
 if($Destination -like '*.staging-*'){
  $payload=Join-Path $Destination 'Prometheus.DriveController.dll'
  [IO.File]::AppendAllText($payload,'UNSIGNED STAGED MUTATION')
  $manifest=Join-Path $Destination 'SHA256-MANIFEST.json'
  $entries=Get-Content $manifest -Raw|ConvertFrom-Json
  foreach($e in $entries){if($e.path -eq 'Prometheus.DriveController.dll'){$e.sha256=(Get-FileHash $payload).Hash}}
  $entries|ConvertTo-Json -Depth 8|Set-Content $manifest
 }
}
$refused=$false
try{$null=Sync-Controller $pkg}catch{$refused=$true}
if(!$refused){throw 'DEFECT: coherent unsigned staged mutation accepted'}
if(Test-Path (Join-Path $ControllerRoot $pkg.Name)){throw 'Rejected cache was published'}
if((Get-SecurityState).highest_controller_version -ne '0.0.0'){throw 'Rejected cache advanced floor'}
@{passed=$true;case='coherent-unsigned-stage-mutation-refused';source_sha256=(Get-FileHash $Source).Hash}|ConvertTo-Json
