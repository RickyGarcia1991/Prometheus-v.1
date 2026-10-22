# Worker for reviewed unique temporary normal-profile inspection task. Never activation.
param([Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$RunId)
$ErrorActionPreference='Stop'
$base=[IO.Path]::GetFullPath($PSScriptRoot)
$pins=@{
 'inspect_active_host_migration.py'='cec966b5dc36666e9272d8fd37c1ec5f7594cc6d86b9b3ce6040dde5eb35fff4'
 'active_migration_preflight.py'='29b1450e8e83316d59c60783ec8f0669cb8b0e0a1dbbed88f4fa278ded6fd1ad'
 'collect-active-host-migration-preflight.ps1'='399606ea06f58a85c65c8d6c53a831c58bcfc893e35cbbb9ddeceafbff8fefcd'
}
$guards=[Collections.Generic.List[IO.Stream]]::new()
$report=Join-Path $base ('active-migration-normal-profile-'+$RunId+'.private.json')
try{
 foreach($name in $pins.Keys){$path=Join-Path $base $name;$guards.Add([IO.File]::Open($path,'Open','Read','Read'));if((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -cne $pins[$name]){throw ('Inspector dependency changed: '+$name)}}
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $profile=(Get-ItemProperty -LiteralPath ('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\'+$sid)).ProfileImagePath
 $python=Join-Path ([Environment]::ExpandEnvironmentVariables($profile)) 'AppData\Local\Programs\Python\Python314\python.exe'
 if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw 'Reviewed normal-profile Python interpreter unavailable'}
 # -B prevents imported inspector modules from writing bytecode caches.
 $text=& $python -B (Join-Path $base 'inspect_active_host_migration.py')
 $exit=$LASTEXITCODE;$inspection=($text -join [Environment]::NewLine)|ConvertFrom-Json
 if($inspection.inspection_only -ne $true -or $inspection.mutation_ready -ne $false -or $inspection.can_execute -ne $false){throw 'Inspector scope contract mismatch'}
 $record=@{run_id=$RunId;captured_utc=[DateTime]::UtcNow.ToString('o');read_only=$true;mutation_ready=$false;source_sha256=$pins;inspection_exit=$exit;inspection=$inspection}
}catch{$record=@{run_id=$RunId;captured_utc=[DateTime]::UtcNow.ToString('o');read_only=$true;mutation_ready=$false;source_sha256=$pins;worker_failure=$_.Exception.Message}}
finally{foreach($guard in $guards){$guard.Dispose()}}
$bytes=[Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Depth 12))
$pending=$report+'.pending-'+[guid]::NewGuid().ToString('N')
$stream=[IO.File]::Open($pending,'CreateNew','Write','None');try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
# Publish only after complete flush/close; an interrupted pending file is retained.
[IO.File]::Move($pending,$report)
