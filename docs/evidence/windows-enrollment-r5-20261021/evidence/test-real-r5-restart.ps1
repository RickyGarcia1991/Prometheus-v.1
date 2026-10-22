$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'Prometheus';$name='Prometheus USB Reconnect Supervisor';$id='7b06d1cde83a4b8ebb5a8ac1fa086887'
$exe=Join-Path $root 'Prometheus-QuietHost-v3.exe';$script=Join-Path $root 'Prometheus-USB-Reconnect-Supervisor.ps1'
$out=Join-Path $PSScriptRoot 'real-r5-restart-acceptance.json'
function Snap {
 $all=@(Get-CimInstance Win32_Process)
 $hosts=@($all|Where-Object {$_.ExecutablePath -ieq $exe -and $_.CommandLine -match 'reconnect'})
 $children=@($all|Where-Object {$_.Name -ieq 'powershell.exe' -and $_.CommandLine -like ('*"'+$script+'"*')})
 $ports=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|Where-Object {$_.LocalPort -in @(54555,54559)}|Select-Object LocalPort,OwningProcess)
 @{hosts=@($hosts|Select-Object ProcessId,CreationDate);children=@($children|Select-Object ProcessId,ParentProcessId,CreationDate);ports=$ports;controllers=@($all|Where-Object {([string]$_.ExecutablePath).StartsWith((Join-Path $root 'Controllers')+'\',[StringComparison]::OrdinalIgnoreCase)}|Select-Object ProcessId,CreationDate);authorization=(Get-FileHash (Join-Path $root 'authorized-host.json')).Hash;arrival=((Get-Content (Join-Path $root 'arrival-status.json') -Raw)|ConvertFrom-Json).phase}
}
function AccountSid([string]$value){if($value -match '^S-1-'){return ([Security.Principal.SecurityIdentifier]::new($value)).Value};return ([Security.Principal.NTAccount]::new($value)).Translate([Security.Principal.SecurityIdentifier]).Value}
function AssertTask {
 $t=Get-ScheduledTask -TaskName $name -TaskPath "\"
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 if(@($t.Actions).Count -ne 1 -or @($t.Triggers).Count -ne 1){throw 'Task action/trigger count changed'}
 if((AccountSid $t.Principal.UserId) -cne $sid -or $t.Principal.RunLevel -ne 0 -or $t.Principal.LogonType -ne 3 -or $t.Triggers[0].CimClass.CimClassName -cne 'MSFT_TaskLogonTrigger' -or (AccountSid $t.Triggers[0].UserId) -cne $sid -or $t.Actions.WorkingDirectory -ine $root -or $t.Settings.MultipleInstances -ne 2 -or $t.Settings.DisallowStartIfOnBatteries -or $t.Settings.StopIfGoingOnBatteries -or !$t.Settings.StartWhenAvailable -or $t.Settings.ExecutionTimeLimit -cne 'PT0S'){throw 'Task principal, trigger or settings changed'}
 if($t.Description -cne ('Prometheus reconnect enrollment '+$id) -or $t.Actions.Execute -ine $exe -or $t.Actions.Arguments -cne 'reconnect' -or !$t.Settings.Enabled){throw 'Task ownership changed'}
 if((Export-ScheduledTask -TaskName $name -TaskPath "\") -cne $script:taskXml){throw 'Task definition changed'}
}
$r=[ordered]@{passed=$false;physical_usb_cycle_tested=$false;full_windows_reboot_tested=$false;stage='baseline'}
try{
 $script:taskXml=Export-ScheduledTask -TaskName $name -TaskPath "\";AssertTask
 $before=Snap
 if($before.hosts.Count -ne 1 -or $before.children.Count -ne 1 -or $before.children[0].ParentProcessId -ne $before.hosts[0].ProcessId){throw 'Expected one owned launcher and supervisor'}
 if(@($before.ports|Where-Object {$_.LocalPort -eq 54555}).Count -ne 1){throw 'Installed app must be listening before survival test'}
 $r.before=$before
 Start-ScheduledTask -TaskName $name -TaskPath "\";Start-Sleep -Seconds 2
 $duplicate=Snap
 if($duplicate.hosts.Count -ne 1 -or $duplicate.hosts[0].CreationDate -ne $before.hosts[0].CreationDate -or $duplicate.hosts[0].ProcessId -ne $before.hosts[0].ProcessId -or $duplicate.children.Count -ne 1 -or $duplicate.children[0].ProcessId -ne $before.children[0].ProcessId){throw 'Duplicate start changed process ownership'}
 if($duplicate.children[0].CreationDate -ne $before.children[0].CreationDate -or $duplicate.children[0].ParentProcessId -ne $duplicate.hosts[0].ProcessId){throw 'Duplicate start changed supervisor identity'}
 $r.duplicate=$duplicate
 $r.duplicate_start_passed=$true;$r.stage='stop';AssertTask
 Stop-ScheduledTask -TaskName $name -TaskPath "\"
 $until=(Get-Date).AddSeconds(20)
 do{$stopped=Snap;if(!$stopped.hosts.Count -and !$stopped.children.Count){break};Start-Sleep -Milliseconds 250}while((Get-Date) -lt $until)
 if($stopped.hosts.Count -or $stopped.children.Count){throw 'Owned supervisor survived task stop'}
 $r.stopped=$stopped
 $r.stop_removed_launcher_and_supervisor=$true
 foreach($port in $before.ports){if(@($stopped.ports|Where-Object {$_.LocalPort -eq $port.LocalPort -and $_.OwningProcess -eq $port.OwningProcess}).Count -ne 1){throw 'Independent application did not survive supervisor stop'}}
 foreach($controller in $before.controllers){if(@($stopped.controllers|Where-Object {$_.ProcessId -eq $controller.ProcessId -and $_.CreationDate -eq $controller.CreationDate}).Count -ne 1){throw 'Owned controller did not survive supervisor stop'}}
 $r.independent_application_ports_preserved=$true;$r.stage='restart';AssertTask
 Start-ScheduledTask -TaskName $name -TaskPath "\"
 $until=(Get-Date).AddSeconds(30)
 do{$after=Snap;if($after.hosts.Count -eq 1 -and $after.children.Count -eq 1){break};Start-Sleep -Milliseconds 500}while((Get-Date) -lt $until)
 if($after.hosts.Count -ne 1 -or $after.children.Count -ne 1 -or $after.children[0].ParentProcessId -ne $after.hosts[0].ProcessId -or $after.hosts[0].ProcessId -eq $before.hosts[0].ProcessId){throw 'Fresh single supervisor was not restored'}
 foreach($port in $before.ports){if(@($after.ports|Where-Object {$_.LocalPort -eq $port.LocalPort -and $_.OwningProcess -eq $port.OwningProcess}).Count -ne 1){throw 'Independent application did not survive supervisor restart'}}
 foreach($controller in $before.controllers){if(@($after.controllers|Where-Object {$_.ProcessId -eq $controller.ProcessId -and $_.CreationDate -eq $controller.CreationDate}).Count -ne 1){throw 'Owned controller did not survive supervisor restart'}}
 if($after.authorization -cne $before.authorization){throw 'Authorization changed'}
 $r.after=$after;$r.passed=$true;$r.stage='complete';$r.test_scope='scheduled supervisor duplicate-start, stop and restart; independent app survival'
}catch{$r.error=$_.Exception.Message}finally{$r|ConvertTo-Json -Depth 7|Set-Content $out}
if(!$r.passed){exit 1}


