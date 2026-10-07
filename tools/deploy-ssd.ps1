param([string]$Root='D:\')
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot
if((Get-Volume -DriveLetter $Root.Substring(0,1)).FileSystemLabel -ne 'Prometheus-2TB'){throw 'SSD identity mismatch'}
$commit=(& git -C $repo rev-parse HEAD).Trim()
if($LASTEXITCODE){throw 'Git revision unavailable'}
$release=Join-Path $Root ('Prometheus-v0.5.2-dev-'+$commit.Substring(0,7))
if(Test-Path $release){throw 'Release already exists; inspect before replacing'}
$zip=Join-Path $repo 'build\ssd-source.zip'
& git -C $repo archive --format=zip --output=$zip HEAD
if($LASTEXITCODE){throw 'Source archive failed'}
Expand-Archive $zip $release
$controller=Join-Path $Root 'Prometheus-Controller-v0.5.2'
$hostController=Join-Path $env:LOCALAPPDATA 'Prometheus\Controllers\Prometheus-Controller-v0.5.2'
New-Item -ItemType Directory -Force $controller,$hostController | Out-Null
Copy-Item (Join-Path $repo 'build\controller-v0.5.2\*') $controller
Copy-Item (Join-Path $repo 'tools\Prometheus.DriveController\Prometheus-Drive-Engine.ps1') $controller
$manifest=@(Get-ChildItem $controller -File | ForEach-Object {
 $dest=Join-Path $hostController $_.Name
 Copy-Item $_.FullName $dest -Force
 $hash=(Get-FileHash $_.FullName).Hash
 if((Get-FileHash $dest).Hash -ne $hash){throw 'Controller host copy mismatch'}
 [ordered]@{file=$_.Name;sha256=$hash;size=$_.Length}
})
$manifest | ConvertTo-Json | Set-Content (Join-Path $controller 'SHA256-MANIFEST.json')
$sourceManifest=@(Get-ChildItem $release -File -Recurse | ForEach-Object {
 [ordered]@{path=$_.FullName.Substring($release.Length+1);sha256=(Get-FileHash $_.FullName).Hash;size=$_.Length}
})
$sourceManifest | ConvertTo-Json | Set-Content (Join-Path $release 'SHA256-MANIFEST.json')
# Check exported source against tracked working files, excluding generated manifest.
foreach($row in $sourceManifest){
 $gitPath=$row.path.Replace('\','/')
 $expected=(& git -C $repo rev-parse ('HEAD:'+$gitPath)).Trim()
 $actual=(& git -C $repo hash-object --no-filters (Join-Path $release $row.path)).Trim()
 if($actual -ne $expected){throw ('Source export mismatch: '+$row.path)}
}
$launcher=Join-Path $Root 'START-PROMETHEUS-SSD.cmd'
$backup=$launcher+'.before-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.bak'
Copy-Item $launcher $backup
$text=[IO.File]::ReadAllText($launcher)
$text=$text.Replace('Prometheus-v0.4.0-191fcc5',(Split-Path $release -Leaf)).Replace('0.4.0-dev','development '+$commit.Substring(0,7)).Replace('191fcc5c760d6fe488bcb6a7117a8fe40a8c3c51',$commit)
[IO.File]::WriteAllText($launcher,$text,[Text.Encoding]::ASCII)
$media=Join-Path $env:USERPROFILE 'Documents\Removable-Media-Status'
Copy-Item (Join-Path $media 'Prometheus-Drive-Watcher.ps1') (Join-Path $media ('Prometheus-Drive-Watcher.ps1.before-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.bak'))
Copy-Item (Join-Path $repo 'tools\Prometheus.DriveController\Prometheus-Drive-Watcher.ps1') $media -Force
[ordered]@{version='0.5.2-dev';git_commit=$commit;branch='feature/ai-orchestration';built=(Get-Date).ToString('o');code_release=$release;controller_release=$controller;host_controller=$hostController;note='Verified source and controller copies; original releases and all data retained'} | ConvertTo-Json | Set-Content (Join-Path $Root 'PROMETHEUS-SSD-STATUS.json')
Write-Output ('DEPLOYED '+$commit)
