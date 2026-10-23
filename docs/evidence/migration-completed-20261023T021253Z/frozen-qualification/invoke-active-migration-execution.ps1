# Prepared live launcher. No default operation and no automatic retry/rollback.
param(
 [Parameter(Mandatory)][ValidateSet('initialize','resume-initialization','resume','rollback')][string]$Operation,
 [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$WorkerSha256,
 [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$EntrySha256,
 [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ContractSha256,
 [ValidateRange(0,50)][int]$WaitSeconds=50
)
$ErrorActionPreference='Stop'
$base=[IO.Path]::GetFullPath($PSScriptRoot);$runId=[guid]::NewGuid().ToString('N')
$name='Codex-ActiveMigration-Execution-'+$runId
$worker=Join-Path $base 'run-active-migration-execution.ps1'
$pwsh=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe'
$argsText='-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$worker+'" -RunId '+$runId+' -Operation '+$Operation+' -ContractSha256 '+$ContractSha256+' -EntrySha256 '+$EntrySha256
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$report=Join-Path $base ('production-migration-profile-'+$runId+'.private.json')
$errors=[Collections.Generic.List[string]]::new();$guard=$null;$taskXml=$null;$completed=$false;$removed=$false;$result=$null
function Write-NewRecord([string]$Path,$Record){$bytes=[Text.Encoding]::UTF8.GetBytes(($Record|ConvertTo-Json -Depth 12));$s=[IO.File]::Open($Path,'CreateNew','Write','None');try{$s.Write($bytes,0,$bytes.Length);$s.Flush($true)}finally{$s.Dispose()}}
function Assert-OwnTask {
 $task=Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop
 $owner=[string]$task.Principal.UserId;if($owner -notmatch '^S-1-'){$owner=([Security.Principal.NTAccount]::new($owner)).Translate([Security.Principal.SecurityIdentifier]).Value}
 if($task.Description -cne $runId -or $owner -cne $sid -or $task.Principal.RunLevel -ne 0 -or $task.Principal.LogonType -ne 3 -or @($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ine $pwsh -or $task.Actions[0].Arguments -cne $argsText -or $task.Actions[0].WorkingDirectory -ine $base -or @($task.Triggers|Where-Object {$null -ne $_}).Count -ne 0 -or $task.Settings.ExecutionTimeLimit -cne 'PT0S'){throw 'Exact generated execution task binding changed'}
 if($taskXml -and (Export-ScheduledTask -TaskName $name -TaskPath '\') -cne $taskXml){throw 'Generated execution XML changed'}
 return $task
}
Write-NewRecord (Join-Path $base ('production-migration-profile-'+$runId+'.ownership.private.json')) @{run_id=$runId;task=$name;task_path='\';principal_sid=$sid;execute=$pwsh;arguments=$argsText;working_directory=$base;worker_sha256=$WorkerSha256;entry_sha256=$EntrySha256;contract_sha256=$ContractSha256;operation=$Operation;report=$report;automatic_retry=$false;automatic_rollback=$false}
try {
 $guard=[IO.File]::Open($worker,'Open','Read','Read')
 if((Get-FileHash -LiteralPath $worker).Hash.ToLowerInvariant() -cne $WorkerSha256){throw 'Reviewed execution worker changed'}
 if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){throw 'Existing generated task refused'}
 $action=New-ScheduledTaskAction -Execute $pwsh -Argument $argsText -WorkingDirectory $base
 $principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
 # PT0S explicitly disables Scheduler's automatic execution time termination.
 $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 $definition=New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $runId
 Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $definition|Out-Null
 $null=Assert-OwnTask;$taskXml=Export-ScheduledTask -TaskName $name -TaskPath '\'
 Write-NewRecord (Join-Path $base ('production-migration-profile-'+$runId+'.task.private.json')) @{run_id=$runId;xml=$taskXml}
 Start-ScheduledTask -InputObject (Assert-OwnTask)
 $deadline=[DateTime]::UtcNow.AddSeconds($WaitSeconds)
 while(!(Test-Path -LiteralPath $report) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 200}
 if(Test-Path -LiteralPath $report){
  $bytes=[IO.File]::ReadAllBytes($report);if($bytes.Length -gt 1048576){throw 'Worker result bound'}
  $result=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)|ConvertFrom-Json
  if($result.run_id -cne $runId -or $result.operation -cne $Operation -or $result.contract_sha256 -cne $ContractSha256 -or $result.entry_sha256 -cne $EntrySha256 -or $result.worker_finished -ne $true){throw 'Worker result binding'}
  $completed=$true
  # Finished worker evidence is necessary but never sufficient to unregister a
  # Running/Queued task. A later read-only finalizer may clean pending tasks.
  $task=Assert-OwnTask
  if($task.State -eq 'Ready'){$null=Assert-OwnTask;Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false;$removed=$true}
 }
} catch {$errors.Add($_.Exception.Message)}
finally {if($guard){try{$guard.Dispose()}catch{$errors.Add('Worker handle close: '+$_.Exception.Message)}}}
$out=@{run_id=$runId;operation=$Operation;status=$(if($completed){'worker_completed'}else{'pending_or_refused'});passed=($completed -and $result.passed -eq $true -and $errors.Count -eq 0);task_removed=$removed;errors=@($errors);automatic_retry=$false;automatic_rollback=$false}
Write-NewRecord (Join-Path $base ('production-migration-profile-'+$runId+'.launch.private.json')) $out
$out|ConvertTo-Json -Depth 6
if($errors.Count -or ($completed -and !$result.passed)){exit 1}
exit 0
