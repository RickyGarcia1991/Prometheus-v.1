$ErrorActionPreference='Stop'
$src='D:\Prometheus-Recovery\Gemma4-26b'
$archive='C:\Users\rolon\Documents\Codex\Recovery-Archives\Flash-Gemma4-26b-20261022'
$receipt=Get-Content -LiteralPath (Join-Path $archive 'ARCHIVE-RECEIPT.json') -Raw | ConvertFrom-Json
if($receipt.source -cne $src -or $receipt.archive -cne $archive){throw 'Receipt scope mismatch'}
$v=Get-Volume -DriveLetter D
$p=Get-Partition -DriveLetter D;$disk=$p | Get-Disk
$pin=Get-Content -LiteralPath 'work/companion-release/flash-cleanup-identity.private.json' -Raw | ConvertFrom-Json
if($v.FileSystemLabel -cne 'PROMETHEUS' -or $v.Size -ne 32076988416 -or $disk.UniqueId -cne $pin.uniqueId -or $disk.SerialNumber -cne $pin.serial -or $disk.Size -ne $pin.size -or $p.Offset -ne $pin.offset){throw 'Physical flash identity changed'}
$resolved=(Resolve-Path -LiteralPath $src).ProviderPath
if($resolved -cne $src -or [IO.Path]::GetFullPath($src) -cne $src){throw 'Unexpected deletion path'}
foreach($ancestor in @('D:\','D:\Prometheus-Recovery',$src,$archive)) {if((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse path refused'}}
$items=@(Get-ChildItem -LiteralPath $src -Recurse -Force)
if($items | Where-Object {$_.Attributes -band [IO.FileAttributes]::ReparsePoint}){throw 'Reparse child refused'}
$files=@($items | Where-Object {-not $_.PSIsContainer})
$inventory=(Get-Content -LiteralPath 'work/companion-release/flash-inventory.private.json' -Raw | ConvertFrom-Json).files
if($files.Count -ne $receipt.count -or $receipt.files.Count -ne $receipt.count){throw 'File count changed'}
foreach($f in $files){
 $rel=$f.FullName.Substring($src.Length+1)
 $row=@($receipt.files | Where-Object path -CEQ $rel)
 $old=@($inventory | Where-Object path -CEQ ('Prometheus-Recovery/Gemma4-26b/'+$rel.Replace('\','/')))
 if($row.Count -ne 1 -or $old.Count -ne 1 -or $row[0].bytes -ne $f.Length -or $old[0].bytes -ne $f.Length -or $old[0].mtime_ns -ne (($f.LastWriteTimeUtc.Ticks-[datetime]::UnixEpoch.Ticks)*100)){throw "Source changed: $rel"}
 $copy=Join-Path $archive $rel
 if((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -cne $row[0].sha256){throw "Archive readback mismatch: $rel"}
 if((Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash -cne $row[0].sha256){throw "Source hash changed: $rel"}
 Write-Output "Final verified $rel"
}
$docBackup=Join-Path $archive 'previous-flash-instructions'
New-Item -ItemType Directory -Path $docBackup -ErrorAction Stop | Out-Null
foreach($name in @('PROMETHEUS-CURRENT-RECOVERY.txt','PROMETHEUS-RECOVERY-CONTENTS.txt','START-HERE-Emergency-Recovery.md','START-HERE-Emergency-Recovery.txt')){
 $original=Join-Path 'D:\' $name
 Copy-Item -LiteralPath $original -Destination (Join-Path $docBackup $name)
 if((Get-FileHash -LiteralPath $original).Hash -ne (Get-FileHash -LiteralPath (Join-Path $docBackup $name)).Hash){throw 'Instruction backup mismatch'}
}
# Repeat physical and inventory checks after hashing, immediately before removal.
$finalVolume=Get-Volume -DriveLetter D
$finalPartition=Get-Partition -DriveLetter D
$finalDisk=$finalPartition | Get-Disk
if($finalVolume.FileSystemLabel -cne 'PROMETHEUS' -or $finalVolume.Size -ne 32076988416 -or $finalDisk.UniqueId -cne $pin.uniqueId -or $finalDisk.SerialNumber -cne $pin.serial -or $finalDisk.Size -ne $pin.size -or $finalPartition.Offset -ne $pin.offset){throw 'Flash changed during verification'}
$finalItems=@(Get-ChildItem -LiteralPath $src -Recurse -Force)
if($finalItems | Where-Object {$_.Attributes -band [IO.FileAttributes]::ReparsePoint}){throw 'Reparse child appeared'}
$finalFiles=@($finalItems | Where-Object {-not $_.PSIsContainer})
if($finalFiles.Count -ne $files.Count){throw 'Source inventory changed during verification'}
foreach($f in $finalFiles){
 $old=@($files | Where-Object FullName -CEQ $f.FullName)
 if($old.Count -ne 1 -or $f.Length -ne $old[0].Length -or $f.LastWriteTimeUtc -ne $old[0].LastWriteTimeUtc){throw 'Source changed during verification'}
}
Remove-Item -LiteralPath $src -Recurse -Force
New-Item -ItemType Directory -Path $src | Out-Null
$note="Optional Gemma4:26b archive relocated intact to $archive. See ARCHIVE-RECEIPT.json there and FLASH-CLEANUP-20261022.json at the flash root. Current core and rollback remain on this flash."
Set-Content -LiteralPath (Join-Path $src 'ARCHIVED-READ-ME.txt') -Value $note -Encoding UTF8
foreach($name in @('PROMETHEUS-CURRENT-RECOVERY.txt','PROMETHEUS-RECOVERY-CONTENTS.txt')){Copy-Item -LiteralPath 'work/companion-release/flash-recovery-current-draft.txt' -Destination (Join-Path 'D:\' $name)}
foreach($name in @('START-HERE-Emergency-Recovery.md','START-HERE-Emergency-Recovery.txt')){
 $oldText=Get-Content -LiteralPath (Join-Path $docBackup $name) -Raw
 $banner="CURRENT RECOVERY UPDATE (2026-10-22): Read PROMETHEUS-CURRENT-RECOVERY.txt first. Current core is dev55; rollback is dev54r2. Instructions below are preserved historical paths. Gemma archive is relocated to the Dell internal drive; its old USB restore command is no longer present. No Ubuntu boot media has been created.`r`n`r`n--- Historical instructions ---`r`n`r`n"
 Set-Content -LiteralPath (Join-Path 'D:\' $name) -Value ($banner+$oldText) -Encoding UTF8
}
$after=Get-Volume -DriveLetter D
$result=[pscustomobject]@{utc=[datetime]::UtcNow.ToString('o');label=$v.FileSystemLabel;volume_bytes=$v.Size;removed_directory=$src;archived_to=$archive;verified_files=$receipt.count;archived_bytes=($receipt.files | Measure-Object bytes -Sum).Sum;free_before=$v.SizeRemaining;free_after=$after.SizeRemaining;freed_bytes=$after.SizeRemaining-$v.SizeRemaining;keys_and_private_backups_preserved=$true;current='dev55';rollback='dev54r2';formatted=$false;ubuntu_installer_created=$false}
$result | ConvertTo-Json | Set-Content -LiteralPath 'work/companion-release/flash-cleanup-result.json' -Encoding UTF8
Copy-Item -LiteralPath 'work/companion-release/flash-cleanup-result.json' -Destination 'D:\FLASH-CLEANUP-20261022.json'
Copy-Item -LiteralPath 'work/companion-release/flash-cleanup-result.json' -Destination (Join-Path $archive 'CLEANUP-RECEIPT.json')
$result | ConvertTo-Json

