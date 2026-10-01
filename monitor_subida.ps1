# Monitor de la subida a Drive: progreso, velocidad real y ETA.
# Corré esto en una ventana APARTE, sin tocar la que está subiendo.
#
#   .\monitor_subida.ps1           # actualiza cada 5 min hasta terminar
#   .\monitor_subida.ps1 -UnaVez   # una sola medición y sale

param(
    [switch]$UnaVez,
    [int]$IntervaloSegundos = 300
)

# Totales reales medidos sobre los discos (fotos que el filtro va a subir)
$objetivos = @(
    @{ Nombre = "E:"; Destino = "gdrive:Organizar/HDD_E/E:"; Total = 59786 },
    @{ Nombre = "F:"; Destino = "gdrive:Organizar/HDD_F/F:"; Total = 90405 }
)

$previo = @{}

function Medir($d) {
    $salida = & rclone size $d.Destino --json 2>$null
    if (-not $salida) { return $null }
    try { return $salida | ConvertFrom-Json } catch { return $null }
}

while ($true) {
    $ahora = Get-Date
    Write-Host "`n===== $($ahora.ToString('HH:mm:ss')) =====" -ForegroundColor Cyan
    $totalFaltan = 0

    foreach ($d in $objetivos) {
        $m = Medir $d
        if (-not $m) { Write-Host "$($d.Nombre): sin datos"; continue }

        $subidos = $m.count
        $gb = [math]::Round($m.bytes / 1GB, 1)
        $pct = [math]::Round($subidos * 100.0 / $d.Total, 1)
        $faltan = $d.Total - $subidos
        $totalFaltan += $faltan

        $linea = "{0}  {1,6:N0}/{2,6:N0} archivos ({3,5:N1}%)  {4,7:N1} GB" -f `
                 $d.Nombre, $subidos, $d.Total, $pct, $gb

        # Velocidad: comparar contra la medición anterior
        if ($previo.ContainsKey($d.Nombre)) {
            $p = $previo[$d.Nombre]
            $seg = ($ahora - $p.Hora).TotalSeconds
            $dGB = ($m.bytes - $p.Bytes) / 1GB
            if ($seg -gt 10 -and $dGB -gt 0) {
                $gbh = $dGB * 3600 / $seg
                $mbps = $dGB * 1024 * 8 * 1000 / $seg   # GB->Mb
                $archNuevos = $subidos - $p.Count
                if ($archNuevos -gt 0) {
                    $horasETA = $faltan / ($archNuevos * 3600 / $seg) / 3600
                    $linea += "  |  {0:N0} GB/h ({1:N0} Mbps)  ETA {2:N1} h" -f $gbh, $mbps, $horasETA
                } else {
                    $linea += "  |  {0:N0} GB/h" -f $gbh
                }
            }
        }

        $color = if ($pct -ge 100) { "Green" } else { "White" }
        Write-Host $linea -ForegroundColor $color

        $previo[$d.Nombre] = @{ Hora = $ahora; Bytes = $m.bytes; Count = $subidos }
    }

    if ($totalFaltan -le 0) {
        Write-Host "`nSUBIDA COMPLETA" -ForegroundColor Green
        break
    }
    if ($UnaVez) { break }

    Write-Host "(faltan $($totalFaltan.ToString('N0')) archivos - proxima medicion en $($IntervaloSegundos/60) min)" -ForegroundColor DarkGray
    Start-Sleep -Seconds $IntervaloSegundos
}
