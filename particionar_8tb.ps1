Initialize-Disk -Number 2 -PartitionStyle GPT
$part = New-Partition -DiskNumber 2 -UseMaximumSize -AssignDriveLetter
Format-Volume -Partition $part -FileSystem NTFS -AllocationUnitSize 65536 -NewFileSystemLabel "Fotos8TB" -Confirm:$false
Get-Volume | Where-Object {$_.FileSystemLabel -eq "Fotos8TB"} | Select-Object DriveLetter, FileSystemLabel, FileSystem, @{N='SizeGB';E={[math]::Round($_.Size/1GB,1)}}
