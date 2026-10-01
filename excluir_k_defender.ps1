Add-MpPreference -ExclusionPath "K:\"
Write-Output "Defender: K:\ excluido"
Get-MpPreference | Select-Object -ExpandProperty ExclusionPath
