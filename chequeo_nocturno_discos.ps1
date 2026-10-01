# Chequeo de superficie completo (sector por sector) del HDD sobreviviente
# y del SSD de 240GB. Usa chkdsk /r (herramienta nativa de Windows): lee cada
# sector, intenta recuperar lo que este dañado, y marca los sectores malos
# para que el sistema de archivos no los vuelva a usar. Es lento (horas para
# un disco de 1TB por USB) -- pensado para dejar corriendo toda la noche.
#
# Ajusta las letras de unidad abajo segun como te las asigne Windows al
# reconectar los discos.
#
#   powershell -ExecutionPolicy Bypass -File chequeo_nocturno_discos.ps1

$discos = @("E", "G")   # HDD sobreviviente y SSD 240GB -- NO poner F (ese ya sabemos que esta fallando)
$log = "C:\Renzo\Proyectos\IMGorgni\chequeo_nocturno_discos.log"

function Escribir($msg) {
    $linea = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Output $linea
    Add-Content -Path $log -Value $linea
}

Escribir "=== Arrancando chequeo nocturno ==="

foreach ($d in $discos) {
    if (-not (Test-Path "${d}:\")) {
        Escribir "$d`: no esta conectado, salteando"
        continue
    }
    Escribir "$d`: arrancando chkdsk /r (esto puede tardar varias horas)..."
    $salida = chkdsk "${d}:" /r /x 2>&1
    Add-Content -Path $log -Value $salida
    Escribir "$d`: terminado"
}

Escribir "=== Chequeo nocturno completo. Revisa $log ==="
