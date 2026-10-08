param([string]$Drive='D:',[switch]$Json)
$ErrorActionPreference='Stop'
$results=New-Object System.Collections.Generic.List[object]
function Check([string]$name,[bool]$ok,[string]$detail){
 $results.Add([pscustomobject]@{name=$name;passed=$ok;detail=$detail})
}
$root=$Drive.TrimEnd('\')+'\'
$disk=Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$Drive+"'")
Check 'Expected SSD identity' ($null -ne $disk -and $disk.VolumeName -eq 'Prometheus-2TB') ([string]$disk.VolumeName)
if(!$disk -or $disk.VolumeName -ne 'Prometheus-2TB'){throw 'Wrong or missing SSD: refusing to inspect other volumes'}
$metadataPath=Join-Path $root 'PROMETHEUS-SSD-STATUS.json'
Check 'Release metadata present' (Test-Path -LiteralPath $metadataPath) $metadataPath
if(!(Test-Path $metadataPath)){throw 'Missing release metadata'}
$meta=Get-Content -LiteralPath $metadataPath -Raw|ConvertFrom-Json
foreach($pair in @(@('Core',$meta.code_release),@('Controller',$meta.controller_release),@('Host agent',$meta.host_agent_release))){
 $expected=Join-Path $root (Split-Path ([string]$pair[1]) -Leaf)
 Check ($pair[0]+' release present') (Test-Path -LiteralPath $expected -PathType Container) $expected
}
$hostDir=[string]$meta.host_controller
$ssdController=Join-Path $root (Split-Path ([string]$meta.controller_release) -Leaf)
foreach($name in @('Prometheus.DriveController.exe','Prometheus.DriveController.dll','Prometheus.DriveController.runtimeconfig.json')){
 $a=Join-Path $hostDir $name;$b=Join-Path $ssdController $name
 $ok=(Test-Path -LiteralPath $a -PathType Leaf) -and (Test-Path -LiteralPath $b -PathType Leaf)
 if($ok){$ok=(Get-FileHash -LiteralPath $a -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $b -Algorithm SHA256).Hash}
 Check ('Controller SHA256 '+$name) $ok $name
}
$launcher=Join-Path $root 'START-PROMETHEUS-CONTROLLER.cmd'
$script=if(Test-Path $launcher){Get-Content $launcher -Raw}else{''}
Check 'No legacy watcher spawn' (!$script.Contains('$watch=Join-Path')) $launcher
Check 'Launcher respects intentional close' ($script.Contains('controller-intentional-close.flag')) $launcher
Check 'Launcher refuses mismatched hashes' ($script.Contains('exit 4')) $launcher
$flag=Join-Path $env:LOCALAPPDATA 'Prometheus\controller-intentional-close.flag'
$running=@(Get-CimInstance Win32_Process -Filter "Name='Prometheus.DriveController.exe'")
Check 'Intentional close respected' (!(Test-Path $flag) -or $running.Count -eq 0) ("flag="+(Test-Path $flag)+" processes="+$running.Count)
$pass=@($results|Where-Object passed).Count
$report=[pscustomobject]@{time=(Get-Date).ToString('o');drive=$Drive;passed=$pass;total=$results.Count;checks=$results}
if($Json){$report|ConvertTo-Json -Depth 5}else{$results|Format-Table -AutoSize;Write-Output ("SUMMARY "+$pass+"/"+$results.Count)}
if($pass -ne $results.Count){exit 1}
