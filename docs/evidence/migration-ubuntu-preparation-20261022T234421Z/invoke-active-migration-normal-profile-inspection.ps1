# Reviewed launch plan only until parent review; creates/removes one unique inspection task.
# Never selects or changes any installed Prometheus task or process.
param()
$ErrorActionPreference='Stop'
$base=[IO.Path]::GetFullPath($PSScriptRoot)
$runId=[guid]::NewGuid().ToString('N');$name='Codex-ActiveMigration-Inspect-'+$runId
$worker=Join-Path $base 'run-active-migration-readonly.ps1'
$workerPin='5eff42f3f490d1d9a13bfa1c9778a40f633a05cb2a8e0785cf13543e17680711'
$pwsh=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe'
$argsText='-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$worker+'" -RunId '+$runId
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$report=Join-Path $base ('active-migration-normal-profile-'+$runId+'.private.json')
$errors=[Collections.Generic.List[string]]::new();$inspection=$null;$cleanupComplete=$false;$guard=$null
function Write-NewRecord([string]$Path,$Record){$bytes=[Text.Encoding]::UTF8.GetBytes(($Record|ConvertTo-Json -Depth 12));$s=[IO.File]::Open($Path,'CreateNew','Write','None');try{$s.Write($bytes,0,$bytes.Length);$s.Flush($true)}finally{$s.Dispose()}}
function Assert-OwnTask {
 $t=Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop
 $owner=[string]$t.Principal.UserId;if($owner -notmatch '^S-1-'){$owner=([Security.Principal.NTAccount]::new($owner)).Translate([Security.Principal.SecurityIdentifier]).Value}
 if($t.Description -cne $runId -or $owner -cne $sid -or $t.Principal.RunLevel -ne 0 -or $t.Principal.LogonType -ne 3 -or @($t.Actions).Count -ne 1 -or $t.Actions[0].Execute -ine $pwsh -or $t.Actions[0].Arguments -cne $argsText -or $t.Actions[0].WorkingDirectory -ine $base -or @($t.Triggers|Where-Object {$null -ne $_}).Count -ne 0){throw 'Generated inspection task identity mismatch'}
 return $t
}
# Persist precise ownership before registration, including an ambiguous registration outcome.
Write-NewRecord (Join-Path $base ('active-migration-normal-profile-'+$runId+'.ownership.private.json')) @{run_id=$runId;task=$name;task_path='\';description=$runId;principal_sid=$sid;execute=$pwsh;arguments=$argsText;working_directory=$base;worker_sha256=$workerPin;report=$report;read_only_inspection=$true}
try{
 $guard=[IO.File]::Open($worker,'Open','Read','Read')
 if((Get-FileHash -LiteralPath $worker).Hash.ToLowerInvariant() -cne $workerPin){throw 'Normal-profile inspection worker changed'}
 if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){throw 'Unexpected existing generated task'}
 $action=New-ScheduledTaskAction -Execute $pwsh -Argument $argsText -WorkingDirectory $base
 $principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
 $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 $definition=New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $runId
 Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $definition|Out-Null
 Start-ScheduledTask -InputObject (Assert-OwnTask)
 $deadline=[DateTime]::UtcNow.AddSeconds(150)
 while(!(Test-Path -LiteralPath $report) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 200}
 if(!(Test-Path -LiteralPath $report)){throw 'Inspection report absent after bounded wait; inspect private ownership record'}
 $bytes=[IO.File]::ReadAllBytes($report);if($bytes.Length -gt 1048576){throw 'Inspection report exceeds bound'}
 $record=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)|ConvertFrom-Json
 if($record.run_id -cne $runId -or $record.read_only -ne $true -or $record.mutation_ready -ne $false){throw 'Inspection report scope/binding mismatch'}
 if($record.PSObject.Properties.Name -contains 'worker_failure'){throw $record.worker_failure}
 $inspection=$record.inspection
}catch{$errors.Add($_.Exception.Message)}
finally{
 try{
  if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){
   $deadline=[DateTime]::UtcNow.AddSeconds(15)
   while((Assert-OwnTask).State -eq 'Running' -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 200}
   if((Assert-OwnTask).State -eq 'Running'){throw 'Generated inspection still running; preserve task and ownership evidence'}
   $null=Assert-OwnTask;Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false
  }
  $cleanupComplete=!(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue)
 }catch{$errors.Add('Cleanup: '+$_.Exception.Message)}
 if($guard){$guard.Dispose()}
}
Write-NewRecord (Join-Path $base ('active-migration-normal-profile-'+$runId+'.launch.private.json')) @{run_id=$runId;errors=@($errors);task_removed=$cleanupComplete;read_only=$true;mutation_ready=$false}
$public=@{run_id=$runId;status=$(if($inspection){$inspection.status}else{'inspection_failed'});inspection_only=$true;mutation_ready=$false;production_ready=$false;cross_session_exclusion_proven=$false;task_removed=$cleanupComplete;error_count=$errors.Count;worker_sha256=$workerPin;collector_sha256=$(if($inspection){$inspection.collector_sha256}else{$null});observed_worker_count=$(if($inspection -and $inspection.evidence.observed_workers){@($inspection.evidence.observed_workers).Count}else{0});runtime_phase=$(if($inspection){$inspection.current_runtime_phase}else{'unobserved'});private_report_sha256=$(if(Test-Path -LiteralPath $report){(Get-FileHash -LiteralPath $report).Hash.ToLowerInvariant()}else{$null})}
Write-NewRecord (Join-Path $base ('active-migration-normal-profile-'+$runId+'.public.json')) $public
$public|ConvertTo-Json -Depth 4
if($errors.Count -or !$cleanupComplete){exit 1}
