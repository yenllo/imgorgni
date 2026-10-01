# Espera a que termine la Fase 2 del material de OneDrive y encadena la Fase 5,
# que es lo unico que faltaba del pipeline (Fase 3 NO: reagrupa los eventos que
# ya estan ordenados a mano).
#
# El chequeo de "ya hay una corriendo" es porque el 2026-08-04 habia dos chats
# de Claude trabajando sobre la misma carpeta al mismo tiempo.
Set-Location C:\Renzo\Proyectos\IMGorgni
$log = "C:\Renzo\Proyectos\IMGorgni\cierre_pipeline_od.log"
function Escribir($t) { "$((Get-Date).ToString('HH:mm:ss'))  $t" | Tee-Object -FilePath $log -Append }

$pidF2 = (Get-Content .\fase2_od.pid -EA SilentlyContinue | Select-Object -First 1).Trim()
Escribir "esperando a la Fase 2 (pid $pidF2)"
while (Get-Process -Id $pidF2 -EA SilentlyContinue) { Start-Sleep -Seconds 60 }
Escribir "Fase 2 termino"

$otra = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match '5_duplicados' }
if ($otra) {
  Escribir "ya hay un 5_duplicados.py corriendo (pid $($otra.ProcessId)) - no arranco otro"
  exit 0
}

Escribir "arrancando Fase 5 (duplicados, completa)"
python 5_duplicados.py -c config_K.json --csv duplicados.csv *>&1 |
  Tee-Object -FilePath $log -Append | Select-Object -Last 25
Escribir "Fase 5 lista - el curador reimporta duplicados.csv solo al reiniciarlo"
