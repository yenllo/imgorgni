$vol = Get-Volume | Where-Object {$_.FileSystemLabel -eq "Fotos8TB"}
Set-Partition -DriveLetter $vol.DriveLetter -NewDriveLetter K
Set-Volume -DriveLetter K -NewFileSystemLabel "FirstDriveK"
Get-Volume -DriveLetter K | Select-Object DriveLetter, FileSystemLabel, @{N='SizeGB';E={[math]::Round($_.Size/1GB,1)}}
