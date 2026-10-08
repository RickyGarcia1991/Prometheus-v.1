param([string]$Root='D:\')
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot
if((Get-Volume -DriveLetter $Root.Substring(0,1)).FileSystemLabel -ne 'Prometheus-2TB'){throw 'SSD identity mismatch'}
$commit=(& git -C $repo rev-parse HEAD).Trim()
if($LASTEXITCODE){throw 'Git revision unavailable'}
$release=Join-Path $Root ('Prometheus-v0.8.0-dev-'+$commit.Substring(0,7))
if(Test-Path $release){throw 'Release already exists; inspect before replacing'}
$zip=Join-Path $repo 'build\ssd-source.zip'
& git -C $repo archive --format=zip --output=$zip HEAD
if($LASTEXITCODE){throw 'Source archive failed'}
Expand-Archive $zip $release
$controller=Join-Path $Root 'Prometheus-Controller-v0.5.4'
$hostController=Join-Path $env:LOCALAPPDATA 'Prometheus\Controllers\Prometheus-Controller-v0.5.4'
if(!(Test-Path $controller)){throw 'Signed controller v0.5.4 is missing from the SSD'}
if(!(Test-Path $hostController)){throw 'Verified host controller v0.5.4 is missing'}
foreach($required in @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')){if(!(Test-Path (Join-Path $controller $required))){throw ('Signed controller artifact missing: '+$required)}}
$sourceManifest=@(Get-ChildItem $release -File -Recurse | ForEach-Object {
 [ordered]@{path=$_.FullName.Substring($release.Length+1);sha256=(Get-FileHash $_.FullName).Hash;size=$_.Length}
})
$sourceManifest | ConvertTo-Json | Set-Content (Join-Path $release 'SHA256-MANIFEST.json')
# Check exported source against tracked working files, excluding generated manifest.
foreach($row in $sourceManifest){
 $gitPath=$row.path.Replace('\','/')
 $expected=(& git -C $repo rev-parse ('HEAD:'+$gitPath)).Trim()
 $actual=(& git -C $repo hash-object ('--path='+$gitPath) (Join-Path $release $row.path)).Trim()
 if($actual -ne $expected){throw ('Source export mismatch: '+$row.path)}
}
$launcher=Join-Path $Root 'START-PROMETHEUS-SSD.cmd'
$backup=$launcher+'.before-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.bak'
Copy-Item $launcher $backup
$text=[IO.File]::ReadAllText((Join-Path $release 'tools\START-PROMETHEUS-SSD.cmd')).Replace("`r`n","`n")
$releaseName=Split-Path $release -Leaf
$text=[regex]::Replace($text,'(?m)^set "PACKAGE=.*"$',('set "PACKAGE=%ROOT%\'+$releaseName+'"'))
$text=[regex]::Replace($text,'(?m)^echo Prometheus version:.*$',('echo Prometheus version: v0.8.0-dev '+$commit.Substring(0,7)))
$text=[regex]::Replace($text,'(?m)^echo Source checkpoint:.*$',('echo Source checkpoint: '+$commit))
[IO.File]::WriteAllText($launcher,$text,[Text.Encoding]::ASCII)
$media=Join-Path $env:USERPROFILE 'Documents\Removable-Media-Status'
Copy-Item (Join-Path $media 'Prometheus-Drive-Watcher.ps1') (Join-Path $media ('Prometheus-Drive-Watcher.ps1.before-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.bak'))
Copy-Item (Join-Path $release 'tools\Prometheus.DriveController\Prometheus-Drive-Watcher.ps1') $media -Force
$branch=(& git -C $repo branch --show-current).Trim()
$metaPath=Join-Path $Root 'PROMETHEUS-SSD-STATUS.json';$oldMeta=if(Test-Path $metaPath){Get-Content $metaPath -Raw|ConvertFrom-Json}else{$null}
[ordered]@{version='0.8.0-dev';git_commit=$commit;branch=$branch;built=(Get-Date).ToString('o');code_release=$release;controller_release=$controller;controller_version='0.5.4';controller_commit='e158724db89f31d9eb8158b22b21ea72c0486456';host_controller=$hostController;host_agent_release=(Join-Path $Root 'Prometheus-Host-Agent-v0.6.2');host_agent_version='0.6.2';host_agent_commit='d70ba90';note='Verified source release; signed controller and Host Agent are independently versioned';validation=$oldMeta.validation;stabilization_validation=$oldMeta.stabilization_validation} | ConvertTo-Json -Depth 8 | Set-Content $metaPath
Write-Output ('DEPLOYED '+$commit)
