# Entrena el ranker de Autocull con lo que Renato (Liga ExScuola) eligio a lo largo de las fechas.
#   powershell -ExecutionPolicy Bypass -File .\entrenar_ranker_renato.ps1
# Toma horas la primera vez (analiza ~15 carretes); cada carrete queda en _autocull_aprender\ y si se
# corta, volver a correrlo sigue desde donde quedo. Resultado: ranker_renato.json + entrenar.log.
# Espera a que termine la prueba de seleccion_ig si esta corriendo, para no pelear por la CPU.

$dir = $PSScriptRoot
$AC = "C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"
$base = if (Test-Path "K:\Fotos\Galeria\Trabajo") { "K:" } else { "\\192.168.1.62\Fotos" }
$L = "$base\Fotos\Galeria\Trabajo\Liga ExScuola"
$E = "$L\Liga ExScuola 2026-1\Entrega_LigaExScuola2026"

while ((Get-ScheduledTask -TaskName 'IMGorgni seleccion IG prueba' -ErrorAction SilentlyContinue).State -eq 'Running') {
    Write-Host "esperando a que termine la prueba de seleccion_ig..."; Start-Sleep 60
}

# Primero los JPEG de 2026 (rapidos); al final los RAW de 2025 (cada ARW pesa 42 MB por la red).
# 2025: carrete = RAW\ y seleccion = RAW\Seleccionadas, asi los nombres calzan (en la Final 2025 los
# JPEG se llaman 114918_IMG_0002.jpg; F6 y F7 casi no tienen JPEG).
$pares = @(
    "$L\Liga ExScuola 2026-1\2026-04-25 F1 - 25-04-2026", "$E\Fecha1",
    "$L\Liga ExScuola 2026-1\2026-05-09 F2 - 09-05-2026", "$E\Fecha2",
    "$L\Liga ExScuola 2026-1\2026-05-16 F3 - 16-05-2026", "$E\Fecha3",
    "$L\Liga ExScuola 2026-1\2026-05-30 F4 - 30-05-2026", "$E\Fecha4",
    "$L\Liga ExScuola 2026-1\2026-06-06 F5 - 06-06-2026", "$E\Fecha5",
    "$L\Liga ExScuola 2026-1\2026-06-13 F6 - 13-06-2026", "$E\Fecha6",
    "$L\Liga ExScuola 2026-1\2026-06-20 F7 - 20-06-2026", "$E\Fecha7",
    "$L\Liga ExScuola 2026-1\2026-07-04 F8 - 04-07-2026", "$E\Fecha8",
    "$L\Liga ExScuola 2026-1\2026-07-11 F9 (Final)", "$E\Fecha9",
    "$L\Liga ExScuola 2026-2\F1 - 13-09-2026\jpeg", "$dir\data\selecciones_renato\2026-2_F1_13-09-2026_galeria.csv",
    "$L\Liga ExScuola 2025-1\2025-05-10 10-05-2025\RAW", "$L\Liga ExScuola 2025-1\2025-05-10 10-05-2025\RAW\Seleccionadas",
    "$L\Liga ExScuola 2025-2\2025-10-04 F4 - 04-10-2025\RAW", "$L\Liga ExScuola 2025-2\2025-10-04 F4 - 04-10-2025\RAW\Seleccionadas",
    "$L\Liga ExScuola 2025-2\2025-10-18 F6 - 18-10-2025\RAW", "$L\Liga ExScuola 2025-2\2025-10-18 F6 - 18-10-2025\RAW\Seleccionadas",
    "$L\Liga ExScuola 2025-2\2025-11-07 F7 - 08-11-2025\RAW", "$L\Liga ExScuola 2025-2\2025-11-07 F7 - 08-11-2025\RAW\Seleccionadas",
    "$L\Liga ExScuola 2025-2\2025-11-22 F8 - 22-11-2025 (Final)\RAW", "$L\Liga ExScuola 2025-2\2025-11-22 F8 - 22-11-2025 (Final)\RAW\Seleccionadas"
)
$args_py = @("-m", "autocull", "aprender", "-o", "ranker_renato.json")
for ($i = 0; $i -lt $pares.Count; $i += 2) { $args_py += @("--par", $pares[$i], $pares[$i + 1]) }

Set-Location $dir
New-Item -ItemType Directory -Force _autocull_aprender | Out-Null
$env:PYTHONIOENCODING = "utf-8"
& $AC @args_py 2>&1 | Where-Object { $_ -notmatch "W0000|INFO:|feedback tensors" } | Tee-Object -FilePath "_autocull_aprender\entrenar.log"
