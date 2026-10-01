# Sube solo fotos de los HDD a Google Drive.
#
# Reanudable: si se corta (o lo cancelás con Ctrl+C), volvé a ejecutarlo y
# continúa donde quedó. rclone compara tamaño y fecha, saltea lo ya subido
# y NUNCA crea carpetas duplicadas "(1)" como el subidor web de Drive.
#
#   .\subir_a_drive.ps1          # sube E: y F:
#   .\subir_a_drive.ps1 -Solo E  # sube solo E:
#   .\subir_a_drive.ps1 -Verificar   # no sube nada, solo muestra qué falta

param(
    [string]$Solo = "",
    [switch]$Verificar
)

# --ignore-case es OBLIGATORIO: tus RAW usan extensión en mayúsculas
# (.ARW, .CR2, .JPG) y sin el flag rclone no matchea ninguno.
$extensiones = "*.{jpg,jpeg,png,gif,webp,tif,tiff,cr2,arw,nef,dng,rw2,raf,heic,heif}"

# Se usa --filter y no --include/--exclude mezclados: rclone avisa que al
# combinarlos el orden de evaluación es indeterminado. Con --filter las reglas
# se aplican en orden y gana la primera que matchea, así las exclusiones de
# sistema van primero y rclone ni intenta abrir esas carpetas.
$comunes = @(
    "--ignore-case"
    "--filter", '- /$RECYCLE.BIN/**'
    "--filter", "- /System Volume Information/**"
    "--filter", "- **/.thumbnails/**"
    "--filter", "+ $extensiones"
    "--filter", "- *"
    "--transfers", "8"
    "--checkers", "16"
    "--drive-chunk-size", "128M"
    "--fast-list"
    "--progress"
    "--stats", "30s"
    "--retries", "10"
    "--low-level-retries", "20"
)

if ($Verificar) { $comunes += "--dry-run" }

$discos = @(
    @{ Letra = "E"; Origen = "E:\"; Destino = "gdrive:Organizar/HDD_E/E:" },
    @{ Letra = "F"; Origen = "F:\"; Destino = "gdrive:Organizar/HDD_F/F:" }
)

foreach ($d in $discos) {
    if ($Solo -and $Solo -ne $d.Letra) { continue }
    if (-not (Test-Path $d.Origen)) {
        Write-Host "SALTEADO $($d.Origen) - disco no conectado" -ForegroundColor Yellow
        continue
    }

    $log = Join-Path $PSScriptRoot "subida_$($d.Letra).log"
    Write-Host "`n=== Subiendo $($d.Origen) -> $($d.Destino) ===" -ForegroundColor Cyan
    Write-Host "Log: $log`n"

    $argumentos = $comunes + @("--log-file", $log, "--log-level", "INFO")

    # Reintento automático: un disco USB sin fuente propia puede desconectarse
    # bajo carga sostenida. Si eso pasa, se espera a que Windows lo remonte y
    # se reanuda. rclone saltea lo ya subido, así que reintentar es barato.
    $intento = 0
    $maxIntentos = 20
    while ($intento -lt $maxIntentos) {
        $intento++
        if ($intento -gt 1) {
            Write-Host "  Reintento $intento/$maxIntentos ..." -ForegroundColor Yellow
        }

        & rclone copy $d.Origen $d.Destino @argumentos

        if ($LASTEXITCODE -eq 0) {
            Write-Host "OK: $($d.Letra): completado" -ForegroundColor Green
            break
        }

        # ¿Se desconectó el disco? Esperar a que vuelva antes de reintentar.
        if (-not (Test-Path $d.Origen)) {
            Write-Host "  $($d.Origen) se DESCONECTO. Esperando a que vuelva..." -ForegroundColor Red
            $espera = 0
            while (-not (Test-Path $d.Origen) -and $espera -lt 600) {
                Start-Sleep -Seconds 15
                $espera += 15
            }
            if (Test-Path $d.Origen) {
                Write-Host "  Disco de vuelta tras $espera s - reanudando" -ForegroundColor Green
            } else {
                Write-Host "  El disco no volvio en 10 min. Revisá el cable/alimentacion." -ForegroundColor Red
                break
            }
        } else {
            Write-Host "  rclone salio con codigo $LASTEXITCODE - reintentando en 30s" -ForegroundColor Yellow
            Start-Sleep -Seconds 30
        }
    }
}

Write-Host "`nListo. Para ver el estado: rclone size gdrive:Organizar/HDD_E/E:"
