param([ValidatePattern('^[A-Za-z]:$')][string]$Drive='D:',[switch]$InspectOnly)
$ErrorActionPreference='Stop'
try {
 $volume=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$Drive'"
 if(!$volume -or $volume.VolumeName -ne 'Prometheus-2TB'){throw 'Prometheus SSD identity does not match.'}
 $partition=Get-CimAssociatedInstance -InputObject $volume -Association Win32_LogicalDiskToPartition | Select-Object -First 1
 $disk=Get-CimAssociatedInstance -InputObject $partition -Association Win32_DiskDriveToDiskPartition | Select-Object -First 1
 if(!$disk){throw 'Target disk could not be resolved.'} # UASP USB disks can report InterfaceType=SCSI; verify USB ancestry below.
 Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class SafeUsbRemoval {
 [DllImport("cfgmgr32.dll",CharSet=CharSet.Unicode)] public static extern uint CM_Locate_DevNodeW(out uint node,string id,uint flags);
 [DllImport("cfgmgr32.dll")] public static extern uint CM_Get_Parent(out uint parent,uint node,uint flags);
 [DllImport("cfgmgr32.dll",CharSet=CharSet.Unicode)] public static extern uint CM_Get_Device_IDW(uint node,StringBuilder id,uint size,uint flags);
 [DllImport("cfgmgr32.dll",CharSet=CharSet.Unicode)] public static extern uint CM_Request_Device_EjectW(uint node,out int veto,StringBuilder name,uint size,uint flags);
}
'@
 [uint32]$node=0
 if([SafeUsbRemoval]::CM_Locate_DevNodeW([ref]$node,$disk.PNPDeviceID,0) -ne 0){throw 'Cannot locate USB disk device.'}
 $usbFound=$false
 for($i=0;$i -lt 12;$i++){
  $id=New-Object Text.StringBuilder 1024
  if([SafeUsbRemoval]::CM_Get_Device_IDW($node,$id,1024,0) -ne 0){throw 'Cannot resolve USB device ancestry.'}
  if($id.ToString() -match '^USB\\VID_'){ $usbFound=$true;break }
  [uint32]$parent=0
  if([SafeUsbRemoval]::CM_Get_Parent([ref]$parent,$node,0) -ne 0){break}
  $node=$parent
 }
 if(!$usbFound){throw 'Exact USB device could not be identified. No removal attempted.'}
 if($InspectOnly){Write-Output ("Verified target: "+$Drive+" "+$volume.VolumeName+" "+$id.ToString());exit 0}
 $local=Join-Path $env:LOCALAPPDATA 'Prometheus'
 $prefix=$Drive+'\'
 $processes=@(Get-CimInstance Win32_Process | Where-Object {$_.ProcessId -ne $PID -and (($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.Contains($prefix)))})
 if($processes.Count){throw ('Drive-backed processes remain: '+(($processes | ForEach-Object {$_.Name+' PID '+$_.ProcessId}) -join ', '))}
 @{active=$true;drive=$Drive;started=(Get-Date).ToString('o');phase='windows-removal'} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $local 'eject-mode.json')
 [int]$veto=0;$name=New-Object Text.StringBuilder 1024
 $result=[SafeUsbRemoval]::CM_Request_Device_EjectW($node,[ref]$veto,$name,1024,0)
 if($result -ne 0){
  Write-Output ("Windows veto: result="+$result+" type="+$veto+" holder="+$name.ToString())
  Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-PnP';Id=225;StartTime=(Get-Date).AddMinutes(-2)} -ErrorAction SilentlyContinue | Where-Object {$_.Message.Contains($id.ToString())} | Select-Object -First 1 -ExpandProperty Message | Write-Output
  exit 2
 }
 for($i=0;$i -lt 20;$i++){if(!(Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$Drive'")){Write-Output 'Windows confirmed volume removal; safe to unplug.';exit 0};Start-Sleep -Milliseconds 250}
 throw 'Windows accepted the request but the volume remains mounted. Keep it connected.'
}catch{Write-Output ('Eject blocked: '+$_.Exception.Message);exit 2}
