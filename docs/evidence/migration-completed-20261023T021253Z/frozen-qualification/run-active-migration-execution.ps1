# Explicit production operation worker. Preparation only until reviewed launcher pins it.
param(
 [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$RunId,
 [Parameter(Mandatory)][ValidateSet('initialize','resume-initialization','resume','rollback')][string]$Operation,
 [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ContractSha256,
 [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$EntrySha256
)
$ErrorActionPreference='Stop'
$base=[IO.Path]::GetFullPath($PSScriptRoot)
$entry=Join-Path $base 'execute_active_host_migration.py'
$contract=Join-Path $base 'production-migration-execution-contract.private.json'
$report=Join-Path $base ('production-migration-profile-'+$RunId+'.private.json')
$stdout=Join-Path $base ('production-migration-profile-'+$RunId+'.stdout.private.txt')
$stderr=Join-Path $base ('production-migration-profile-'+$RunId+'.stderr.private.txt')
$guards=[Collections.Generic.List[IO.Stream]]::new()
$record=@{run_id=$RunId;operation=$Operation;contract_sha256=$ContractSha256;entry_sha256=$EntrySha256;worker_finished=$false;passed=$false}
try {
 foreach($item in @(@($entry,$EntrySha256),@($contract,$ContractSha256))){
  $guards.Add([IO.File]::Open($item[0],'Open','Read','Read'))
  if((Get-FileHash -LiteralPath $item[0]).Hash.ToLowerInvariant() -cne $item[1]){throw 'Reviewed execution input changed'}
 }
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $profile=(Get-ItemProperty -LiteralPath ('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\'+$sid)).ProfileImagePath
 $python=Join-Path ([Environment]::ExpandEnvironmentVariables($profile)) 'AppData\Local\Programs\Python\Python314\python.exe'
 if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw 'Normal-profile Python unavailable'}
 if((Test-Path -LiteralPath $stdout) -or (Test-Path -LiteralPath $stderr)){throw 'Existing execution output preserved'}
 # Deliberately synchronous with no process timeout or kill. The launcher can
 # yield pending while this exact task/worker continues to own the operation.
 & $python -B $entry $Operation --contract-sha256 $ContractSha256 1> $stdout 2> $stderr
 $record.python_exit=$LASTEXITCODE
 $raw=[IO.File]::ReadAllBytes($stdout);if($raw.Length -gt 65536){throw 'Execution stdout bound'}
 $summary=[Text.UTF8Encoding]::new($false,$true).GetString($raw)|ConvertFrom-Json
 if($summary.attempt -notmatch '^[a-f0-9]{32}$' -or $summary.passed -isnot [bool]){throw 'Execution attempt summary schema'}
 $expected=Join-Path $base ('production-migration-attempt-'+$summary.attempt+'.result.private.json')
 if($summary.private_result -cne $expected){throw 'Execution result path binding'}
 $resultBytes=[IO.File]::ReadAllBytes($expected);if($resultBytes.Length -gt 1048576){throw 'Execution result bound'}
 $result=[Text.UTF8Encoding]::new($false,$true).GetString($resultBytes)|ConvertFrom-Json
 if($result.attempt -cne $summary.attempt -or $result.operation -cne $Operation -or $result.passed -ne $summary.passed){throw 'Execution result binding'}
 $record.attempt=$summary.attempt;$record.result_sha256=(Get-FileHash -LiteralPath $expected).Hash.ToLowerInvariant()
 $record.passed=($LASTEXITCODE -eq 0 -and $result.passed -eq $true)
} catch {$record.worker_failure=$_.Exception.Message}
finally {
 $cleanup=[Collections.Generic.List[string]]::new()
 foreach($guard in $guards){try{$guard.Dispose()}catch{$cleanup.Add($_.Exception.Message)}}
 $record.cleanup_errors=@($cleanup);if($cleanup.Count){$record.passed=$false}
}
$record.worker_finished=$true;$record.finished_utc=[DateTime]::UtcNow.ToString('o')
$bytes=[Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Depth 10))
$pending=$report+'.pending'
$stream=[IO.File]::Open($pending,'CreateNew','Write','None');try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
[IO.File]::Move($pending,$report)
if(!$record.passed){exit 1}
