param([string]$ResourceRoot="D:\Prometheus-Resources")
$ErrorActionPreference="Stop"
$items=@(
 @{Project="wikipedia";File="wikipedia_en_all_nopic_2026-06.zim";Hash="441a56d9e05b2d98f8ae9acb7986a513ed47904d73852c92dc6b7d50baa122e5"},
 @{Project="wiktionary";File="wiktionary_en_all_nopic_2026-08.zim";Hash="5276f63a2e451518ec5b7c4452ca9a4f72fae48991da3fe7ebc47df74bae760a"},
 @{Project="wikisource";File="wikisource_en_all_nopic_2026-09.zim";Hash="6180cd199142862fb0d276861ccc5b48a40f7bae427b8427b2b6a573796e2f98"}
)
foreach($i in $items){
 $dir=Join-Path $ResourceRoot ("Knowledge\Kiwix\"+$i.Project);New-Item -ItemType Directory -Force $dir|Out-Null
 $final=Join-Path $dir $i.File;$part=$final+".part";$url="https://download.kiwix.org/zim/$($i.Project)/$($i.File)"
 if(Test-Path $final){if((Get-FileHash $final -Algorithm SHA256).Hash.ToLower() -eq $i.Hash){Write-Host "VERIFIED existing $($i.File)";continue};throw "Existing file hash mismatch: $final"}
 Write-Host "DOWNLOADING $url"
 & curl.exe -L --fail --retry 5 --retry-delay 10 -C - -o $part $url
 if($LASTEXITCODE){throw "Download failed: $($i.File)"}
 $actual=(Get-FileHash $part -Algorithm SHA256).Hash.ToLower()
 if($actual -ne $i.Hash){throw "SHA256 mismatch for $($i.File): $actual"}
 Move-Item $part $final -Force;Write-Host "VERIFIED $($i.File)"
}
