$ErrorActionPreference='Stop'
$src='D:\Prometheus-Recovery\Gemma4-26b'
$dst='C:\Users\rolon\Documents\Codex\Recovery-Archives\Flash-Gemma4-26b-20261022'
$v=Get-Volume -DriveLetter D
if($v.FileSystemLabel -ne 'PROMETHEUS' -or $v.Size -ne 32076988416){throw 'Flash identity mismatch'}
if(Test-Path -LiteralPath $dst){throw 'Archive destination exists; inspect before retry'}
$items=@(Get-ChildItem -LiteralPath $src -Recurse -Force)
if($items | Where-Object {$_.Attributes -band [IO.FileAttributes]::ReparsePoint}){throw 'Reparse point refused'}
New-Item -ItemType Directory -Path $dst | Out-Null
$records=@()
foreach($f in ($items | Where-Object {-not $_.PSIsContainer})){
 $rel=$f.FullName.Substring($src.Length+1)
 $out=Join-Path $dst $rel
 New-Item -ItemType Directory -Path (Split-Path $out) -Force | Out-Null
 $before=(Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
 Copy-Item -LiteralPath $f.FullName -Destination $out
 $after=(Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash
 if($before -ne $after){throw "Archive mismatch: $rel"}
 $records += [pscustomobject]@{path=$rel;bytes=$f.Length;sha256=$before}
 Write-Output "Verified $rel"
}
[pscustomobject]@{source=$src;archive=$dst;files=$records;count=$records.Count;verifiedUtc=[DateTime]::UtcNow.ToString('o');sourceDeleted=$false} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $dst 'ARCHIVE-RECEIPT.json') -Encoding UTF8
