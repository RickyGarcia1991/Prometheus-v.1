$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('prometheus-backup-copy-'+[guid]::NewGuid().ToString('N'))
$source=Join-Path $root 'source'
$backup=Join-Path $root 'backup'
$restore=Join-Path $root 'restore'
[IO.Directory]::CreateDirectory($source)|Out-Null
[IO.Directory]::CreateDirectory((Join-Path $source 'empty'))|Out-Null
$file=Join-Path $source 'payload.bin'
[IO.File]::WriteAllBytes($file,[byte[]](1,2,3,4,5))
[IO.File]::WriteAllText(($file+':fixture-stream'),'file-stream-fixture')
[IO.File]::WriteAllText(($source+':directory-stream'),'directory-stream-fixture')
[IO.File]::SetLastWriteTimeUtc($file,[datetime]'2020-01-02T03:04:05Z')
function Fingerprint([string]$Path) {
 $p=Join-Path $Path 'payload.bin'
 $sha=[Security.Cryptography.SHA256]::Create()
 try {
  return [ordered]@{
   file_hash=[Convert]::ToHexString($sha.ComputeHash([IO.File]::ReadAllBytes($p)))
   file_stream=[IO.File]::ReadAllText(($p+':fixture-stream'))
   directory_stream=[IO.File]::ReadAllText(($Path+':directory-stream'))
   modified=[IO.File]::GetLastWriteTimeUtc($p).Ticks
   file_acl=(Get-Acl -LiteralPath $p).Sddl
   empty_directory=[IO.Directory]::Exists((Join-Path $Path 'empty'))
  }
 } finally {$sha.Dispose()}
}
$before=Fingerprint $source
$result=[ordered]@{scope='Generated temporary files only';source_changed=$false;full_backup_complete=$false;passed=$false;errors=@();copy_exit_codes=@()}
try {
 foreach($pair in @(@($source,$backup),@($backup,$restore))) {
  & robocopy.exe $pair[0] $pair[1] /E /COPY:DATS /DCOPY:DAT /R:0 /W:0 /XJ /NP /NFL /NDL /NJH /NJS | Out-Null
  $result.copy_exit_codes+= $LASTEXITCODE
  if($LASTEXITCODE -ge 8){throw 'Robocopy reported failure'}
 }
 $b=Fingerprint $backup;$r=Fingerprint $restore;$after=Fingerprint $source
 $expected=$before|ConvertTo-Json -Compress
 if(($after|ConvertTo-Json -Compress) -cne $expected){$result.source_changed=$true;throw 'Source changed'}
 if(($b|ConvertTo-Json -Compress) -cne $expected){throw 'Backup fingerprint mismatch'}
 if(($r|ConvertTo-Json -Compress) -cne $expected){throw 'Restore fingerprint mismatch'}
 $result.passed=$true
} catch {$result.errors+= $_.Exception.Message}
$result.fixture_root=$root
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'ubuntu-backup-copy-native.private.json')
$result.Remove('fixture_root')
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'ubuntu-backup-copy-native-public.json')
$result | ConvertTo-Json -Depth 6
if(-not $result.passed){exit 1}
