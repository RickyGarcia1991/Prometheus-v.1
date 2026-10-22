[CmdletBinding()]
param(
 [Parameter(Mandatory)][string]$ExpectedProfile,
 [Parameter(Mandatory)][string]$ExpectedHostBinding,
 [Parameter(Mandatory)][UInt64]$ExpectedSsdBytes,
 [Parameter(Mandatory)][string]$ExpectedVolumeUniqueId,
 [Parameter(Mandatory)][string]$ExpectedDiskUniqueId
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Get-PreflightHash([string]$Text){$h=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLower()}finally{$h.Dispose()}}
function Convert-PreflightControllerPointer([string]$Raw){
 if($Raw -cmatch '\A(?<leaf>Prometheus-Controller-v[0-9]+\.[0-9]+\.[0-9]+)\z'){return @{leaf=$Matches.leaf;legacy=$false}}
 if($Raw -cmatch '\A[A-Za-z]:[\\/](?<leaf>Prometheus-Controller-v[0-9]+\.[0-9]+\.[0-9]+)\z'){return @{leaf=$Matches.leaf;legacy=$true}}
 throw 'Controller pointer malformed'
}
function Assert-NoPreflightReparse([string]$Path){
 $p=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
 while($p){if($p.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse component refused'};if($p -is [IO.FileInfo]){$p=$p.Directory}else{$p=$p.Parent}}
}
function Test-ControllerPreflightSnapshot($s,$expected){
 if(!$s.complete -or $s.profile -ine $expected.profile -or $s.host -cne $expected.host){throw 'Host/profile identity mismatch or unreadable'}
 if($s.volumes.Count -ne 1){throw 'SSD identity must match exactly one volume'}
 $v=$s.volumes[0]
 if($v.label -cne 'Prometheus-2TB' -or [UInt64]$v.bytes -ne [UInt64]$expected.bytes -or $v.unique -cne $expected.volume -or $v.disk -cne $expected.disk){throw 'SSD physical identity mismatch'}
 if(!$v.healthy -or $s.metadata_bytes -le 0 -or $s.metadata_bytes -gt 1048576 -or $s.metadata_sha256 -notmatch '^[a-f0-9]{64}$'){throw 'SSD health or metadata invalid'}
 if($s.controller_release -notmatch '^Prometheus-Controller-v[0-9]+\.[0-9]+\.[0-9]+$'){throw 'Controller pointer malformed'}
 if($s.ambiguous_processes -gt 0){throw 'Unresolved process ownership'}
 [pscustomobject]@{read_only=$true;activation_ready=$false;host_binding=$s.host;volume_binding=(Get-PreflightHash ($v.unique+'|'+$v.disk));metadata_sha256=$s.metadata_sha256;metadata_bytes=$s.metadata_bytes;controller_release=$s.controller_release;owned_controller_count=$s.controllers;owned_supervisor_count=$s.supervisors;remaining_gates=@('Exclusive lifecycle/eject locks and fresh state recheck','Active R5 supervisor migration with owned process preservation; not R3 disabled-task upgrade','Signed adapter/package binding and durable pointer transaction','Explicit launch/readiness acceptance')}
}
function Assert-PhysicalEnrollmentRoot([string]$Root){
 if(-not ('PrometheusUpgradePhysicalRoot' -as [type])){
  Add-Type -TypeDefinition @"
using System;using System.Text;using System.Runtime.InteropServices;using Microsoft.Win32.SafeHandles;
public static class PrometheusUpgradePhysicalRoot {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] public static extern int GetCurrentPackageFullName(ref uint length,StringBuilder name);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern SafeFileHandle CreateFile(string p,uint a,uint s,IntPtr sec,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder b,uint n,uint flags);
}
"@
 }
 [uint32]$packageLength=0;if([PrometheusUpgradePhysicalRoot]::GetCurrentPackageFullName([ref]$packageLength,$null) -ne 15700){throw 'Packaged/MSIX execution context refused'}
 $handle=[PrometheusUpgradePhysicalRoot]::CreateFile($Root,128,3,[IntPtr]::Zero,3,33554432,[IntPtr]::Zero)
 try{
  if($handle.IsInvalid){throw 'Physical root handle unavailable'}
  $buffer=[Text.StringBuilder]::new(32768);$length=[PrometheusUpgradePhysicalRoot]::GetFinalPathNameByHandle($handle,$buffer,32768,0)
  if(!$length -or $length -ge 32768 -or $buffer.ToString().TrimEnd('\') -ine ('\\?\'+[IO.Path]::GetFullPath($Root).TrimEnd('\'))){throw 'Profile root resolves elsewhere; MSIX/redirection refused'}
  return $handle
 }catch{$handle.Dispose();throw}
}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$sid=$identity.User.Value
$profileKey=Get-ItemProperty -LiteralPath ('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\'+$sid) -ErrorAction Stop
$profile=[Environment]::ExpandEnvironmentVariables([string]$profileKey.ProfileImagePath)
if([IO.Path]::GetFullPath($ExpectedProfile).TrimEnd('\') -ine $profile.TrimEnd('\')){throw 'Expected current-user profile mismatch'}
$hostBinding=Get-PreflightHash ((Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid+'|'+$sid)
if($hostBinding -cne $ExpectedHostBinding){throw 'Host binding mismatch'}
$hostRoot=Join-Path $profile 'AppData/Local/Prometheus'
Assert-NoPreflightReparse $hostRoot
$profileHandle=Assert-PhysicalEnrollmentRoot $hostRoot
$driveHandle=$null
try{
 $volumes=@(Get-Volume -ErrorAction Stop|Where-Object {$_.FileSystemLabel -ceq 'Prometheus-2TB'})
 if($volumes.Count -ne 1){throw 'Unique labeled SSD required'}
 $v=$volumes[0]
 if(!$v.DriveLetter -or [UInt64]$v.Size -ne $ExpectedSsdBytes -or [string]$v.UniqueId -cne $ExpectedVolumeUniqueId){throw 'SSD volume mismatch'}
 $partition=@(Get-Partition -DriveLetter $v.DriveLetter -ErrorAction Stop)
 if($partition.Count -ne 1){throw 'Ambiguous partition'}
 $disk=Get-Disk -Number $partition[0].DiskNumber -ErrorAction Stop
 if([string]$disk.UniqueId -cne $ExpectedDiskUniqueId){throw 'Physical disk mismatch'}
 $drive=([string]$v.DriveLetter)+':\'
 Assert-NoPreflightReparse $drive
 $driveHandle=Assert-PhysicalEnrollmentRoot $drive
 $meta=Join-Path $drive 'PROMETHEUS-SSD-STATUS.json'
 Assert-NoPreflightReparse $meta
 $stream=[IO.File]::Open($meta,'Open','Read','Read')
 try{if($stream.Length -le 0 -or $stream.Length -gt 1048576){throw 'Metadata size bound'};$bytes=New-Object byte[] ([int]$stream.Length);$offset=0;while($offset -lt $bytes.Length){$n=$stream.Read($bytes,$offset,$bytes.Length-$offset);if(!$n){throw 'Metadata short read'};$offset+=$n}}finally{$stream.Dispose()}
 $hash=[Security.Cryptography.SHA256]::Create();try{$metaHash=([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-','').ToLower()}finally{$hash.Dispose()}
 $parsed=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)|ConvertFrom-Json -ErrorAction Stop
 $pointer=Convert-PreflightControllerPointer ([string]$parsed.controller_release)
 $release=$pointer.leaf
 $allowedController=@((Join-Path $drive ($release+'\Prometheus.DriveController.exe')),(Join-Path $hostRoot ('Controllers\'+$release+'\Prometheus.DriveController.exe')))
 $controllers=0;$supervisors=0;$ambiguous=0
 foreach($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)){
  $supervisorScript=Join-Path $hostRoot 'Prometheus-USB-Reconnect-Supervisor.ps1'
  $isChild=([string]$process.Name -in @('powershell.exe','pwsh.exe') -and [string]$process.CommandLine -match [regex]::Escape($supervisorScript))
  if($process.Name -in @('Prometheus.DriveController.exe','Prometheus-QuietHost-v3.exe') -or $isChild){
   $owner=Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction Stop
   if($owner.ReturnValue -ne 0 -or [string]$owner.Sid -cne $sid){$ambiguous++;continue}
  }
  if($process.Name -ieq 'Prometheus.DriveController.exe'){
   if([string]$process.ExecutablePath -iin $allowedController){$controllers++}else{$ambiguous++}
  }
  if($process.Name -ieq 'Prometheus-QuietHost-v3.exe'){
   if([string]$process.ExecutablePath -ieq (Join-Path $hostRoot 'Prometheus-QuietHost-v3.exe') -and [string]$process.CommandLine -match '\sreconnect\s*$'){$supervisors++}else{$ambiguous++}
  }
  if($isChild){
   $shells=@((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'),(Join-Path $PSHOME 'pwsh.exe'))
   $scriptArgument='(?i)(?:^|\s)-File\s+"'+[regex]::Escape($supervisorScript)+'"(?:\s|$)'
   if([string]$process.ExecutablePath -iin $shells -and [string]$process.CommandLine -match $scriptArgument){$supervisors++}else{$ambiguous++}
  }
 }
 $snapshot=@{complete=$true;profile=$profile;host=$hostBinding;volumes=@(@{label=[string]$v.FileSystemLabel;bytes=[UInt64]$v.Size;unique=[string]$v.UniqueId;disk=[string]$disk.UniqueId;healthy=([string]$v.HealthStatus -eq 'Healthy' -and [string]$disk.HealthStatus -eq 'Healthy')});metadata_bytes=$bytes.Length;metadata_sha256=$metaHash;controller_release=$release;controllers=$controllers;supervisors=$supervisors;ambiguous_processes=$ambiguous}
 $result=Test-ControllerPreflightSnapshot $snapshot @{profile=$ExpectedProfile;host=$ExpectedHostBinding;bytes=$ExpectedSsdBytes;volume=$ExpectedVolumeUniqueId;disk=$ExpectedDiskUniqueId}
 $result|Add-Member -NotePropertyName legacy_drive_prefix_ignored -NotePropertyValue ([bool]$pointer.legacy)
 $result|ConvertTo-Json -Depth 5
}finally{if($driveHandle){$driveHandle.Dispose()};$profileHandle.Dispose()}
