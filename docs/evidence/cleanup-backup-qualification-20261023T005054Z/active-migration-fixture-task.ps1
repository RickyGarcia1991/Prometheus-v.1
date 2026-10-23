param([Parameter(Mandatory)][string]$Root,[ValidateSet('Create','Observe','Disable','Enable','Start','Remove','CleanupDisable')][string]$Mode)
$ErrorActionPreference='Stop'
$full=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
if((Split-Path $full -Parent) -ine $temp -or (Split-Path $full -Leaf) -cnotmatch '^prometheus-active-chain-store-[a-f0-9]{32}$'){throw 'Exact generated root required'}
$item=Get-Item -LiteralPath $full
while($item){if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse refused'};$item=$item.Parent}
$marker=Get-Content -LiteralPath (Join-Path $full 'STORE-FIXTURE.json') -Raw|ConvertFrom-Json
if($marker.root -ine $full -or $marker.purpose -cne 'generated-active-migration-store-v1'){throw 'Generated marker mismatch'}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Python=Join-Path $HOME '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
$statePath=Join-Path $full 'task-ownership.json'
$config=Get-Content -LiteralPath (Join-Path $full 'task-spec.json') -Raw|ConvertFrom-Json -AsHashtable
if($config.token -cnotmatch '^[a-f0-9]{32}$' -or $config.enrollment -cnotmatch '^[a-f0-9]{32}$' -or $config.owner -cne $sid){throw 'Fixture spec identity'}
$name='Codex-Prometheus-Fixture-'+$config.token
$id='Prometheus reconnect enrollment '+$config.enrollment
$driver=Join-Path $full 'driver.ps1'
$arguments='-NoProfile -NonInteractive -File "'+$driver+'" -Root "'+$full+'" -Token '+$config.token
$otherArguments='UNUSED-'+$config.token
$source=Join-Path $PSScriptRoot 'check-active-migration-native-primitives.ps1'
if((Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant() -cne '1d52937667de78eea278eb29818c8d48eecc1a63ae1b4f53e10b5027e16746ac'){throw 'Reviewed primitive changed'}
$ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$null,[ref]$null)
foreach($functionName in @('Assert-OwnedFixtureTask','Task-Config','Get-FixtureIdentity')){
 $nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $functionName},$false))
 if($nodes.Count -ne 1){throw 'Primitive missing'};. ([scriptblock]::Create($nodes[0].Extent.Text))
}
if($Mode -eq 'Create'){
 if(Test-Path -LiteralPath $statePath){throw 'Task ownership create-only'}
 if(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue){throw 'Task already exists'}
 $state=@{created=[DateTime]::UtcNow.ToString('o');sid=$sid;root=$full;task=$name;arguments=$arguments}
 $bytes=[Text.Encoding]::UTF8.GetBytes(($state|ConvertTo-Json -Compress))
 $stream=[IO.File]::Open($statePath,'CreateNew','Write','None');try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
 $action=New-ScheduledTaskAction -Execute $Python -Argument $arguments
 $principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
 $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 $task=New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $id
 Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $task -ErrorAction Stop|Out-Null
}else{
 $state=Get-Content -LiteralPath $statePath -Raw|ConvertFrom-Json -AsHashtable
 if($state.sid -cne $sid -or $state.root -ine $full -or $state.task -cne $name -or $state.arguments -cne $arguments){throw 'Task ownership changed'}
}
if($Mode -in @('Remove','CleanupDisable') -and !(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue)){
 @{removed=$true;processes=@();name=$name}|ConvertTo-Json -Compress;return
}
$task=Assert-OwnedFixtureTask
$xml=Task-Config
if($Mode -notin @('Create','Observe')){
 $baseline=[IO.File]::ReadAllText((Join-Path $full 'task-baseline.xml'))
 if($xml -cne $baseline){throw 'Task normalized configuration changed'}
}
switch($Mode){
 'Disable' {Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null}
 'CleanupDisable' {Disable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null}
 'Enable' {Enable-ScheduledTask -InputObject (Assert-OwnedFixtureTask)|Out-Null}
 'Start' {Start-ScheduledTask -InputObject (Assert-OwnedFixtureTask)}
 'Remove' {Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false;@{removed=$true;name=$name}|ConvertTo-Json -Compress;return}
}
$task=Assert-OwnedFixtureTask
$matches=@()
foreach($w in @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'")){
 if([string]$w.CommandLine -ceq ('"'+$Python+'" '+$arguments) -or [string]$w.CommandLine -ceq ($Python+' '+$arguments)){
  $row=Get-FixtureIdentity ([int]$w.ProcessId) $arguments
  $row.owner=$sid;$row.session=[int]$w.SessionId;$row.parent=[int]$w.ParentProcessId
  $row.created=([DateTime]::new([long]$row.ticks,[DateTimeKind]::Utc)).ToFileTimeUtc().ToString()
  $row.image=$row.path;$matches+=@($row)
 }
}
@{name=$name;enabled=[bool]$task.Settings.Enabled;state=[string]$task.State;xml=(Task-Config);arguments=$arguments;image=$Python;owner=$sid;processes=$matches}|ConvertTo-Json -Depth 8 -Compress
