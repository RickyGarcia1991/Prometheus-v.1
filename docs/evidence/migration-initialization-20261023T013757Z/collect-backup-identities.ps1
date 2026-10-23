# Read-only identity capture. Does not authorize backup or disk mutation.
$ErrorActionPreference='Stop'
$targets=@()
foreach($spec in @(@('OS',493705228288,'NTFS'),@('Prometheus-2TB',1950372327424,'NTFS'),@('PROMETHEUS',32076988416,'FAT32'))){
 $matches=@(Get-Volume | Where-Object {$_.FileSystemLabel -ceq $spec[0] -and $_.Size -eq $spec[1]})
 if($matches.Count -ne 1){throw 'Exact volume identity is missing or ambiguous'}
 $v=$matches[0]
 if($v.HealthStatus -ne 'Healthy' -or $v.FileSystem -cne $spec[2] -or !$v.DriveLetter){throw 'Volume health or filesystem changed'}
 $parts=@(Get-Partition -DriveLetter $v.DriveLetter)
 if($parts.Count -ne 1){throw 'Ambiguous partition'}
 $p=$parts[0];$d=Get-Disk -Number $p.DiskNumber
 if(!$v.UniqueId -or !$d.UniqueId -or !$d.SerialNumber -or ($d.PartitionStyle -eq 'GPT' -and !$p.Guid)){throw 'Incomplete physical identity'}
 $targets+=@{Label=$spec[0];Volume=($v|Select-Object DriveLetter,FileSystem,FileSystemLabel,Size,SizeRemaining,UniqueId,Path,HealthStatus);Partition=($p|Select-Object DiskNumber,PartitionNumber,Guid,Offset,Size,Type);Disk=($d|Select-Object Number,FriendlyName,SerialNumber,UniqueId,Size,PartitionStyle,BusType,IsBoot,IsSystem);Layout=@(Get-Partition -DiskNumber $p.DiskNumber|Sort-Object PartitionNumber|Select-Object PartitionNumber,Guid,Offset,Size,Type)}
}
if(@($targets|ForEach-Object {$_.Disk.UniqueId}|Sort-Object -Unique).Count -ne 3){throw 'Source and destination disks overlap'}
@{ReadOnly=$true;ObservedUtc=[DateTime]::UtcNow.ToString('o');Targets=$targets}|ConvertTo-Json -Depth 12
