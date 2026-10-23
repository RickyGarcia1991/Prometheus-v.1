param([string]$Source,[string]$Scratch,[ValidateSet('verify','admit','read-floor','set-floor')][string]$Mode,[string]$PackageRoot,[string]$Version)
$ErrorActionPreference='Stop'
$resolved=[IO.Path]::GetFullPath($Scratch).TrimEnd('\');$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if($resolved -cne $Scratch.TrimEnd('\') -or !$resolved.StartsWith($temp+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolved -Leaf) -notlike 'prometheus-full-signed-*'){throw 'Generated temporary root required'}
$part=Get-Item -LiteralPath $resolved -Force
while($part){if($part.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse refused'};$part=$part.Parent}
if((Get-FileHash $Source).Hash -ine 'aac3f3ca83ec340ba6a4764e313a4a6c3fd12be80cbe25bb45126cd9ce42e38d'){throw 'Source changed'}
$SecurityState=Join-Path $Scratch 'security-state.json'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($name in @('Test-ProtectedFile','Protect-File','Get-SecurityState','Set-SecurityState','Convert-Version','Assert-ControllerProvenance','Get-VerifiedControllerPackage','Get-Package')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
 if($f.Count -ne 1){throw 'Missing function'};. ([scriptblock]::Create($f[0].Extent.Text))
}
function Write-Audit {param($a,$b,$c)}
function Start-Process {throw 'Forbidden launch'}
$TrustedThumbprint=@('898F702114B0F889763589C4057DC19CF1657CD5','1E0F99439DD072E014D81C5574B325C97EA4AC98')
if($Mode -eq 'verify' -or $Mode -eq 'admit'){
 $full=[IO.Path]::GetFullPath($PackageRoot);if(!$full.StartsWith($resolved+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Package outside fixture'}
 $pkg=if($Mode -eq 'verify'){Get-VerifiedControllerPackage $full}else{Get-Package $full}
 @{version=$pkg.Version.ToString();manifest=(Get-FileHash (Join-Path $pkg.Source 'SHA256-MANIFEST.json')).Hash.ToLower()}|ConvertTo-Json -Compress
}elseif($Mode -eq 'read-floor'){@{floor=(Get-SecurityState).highest_controller_version}|ConvertTo-Json -Compress}
else{Set-SecurityState ([version]$Version);@{floor=(Get-SecurityState).highest_controller_version}|ConvertTo-Json -Compress}
