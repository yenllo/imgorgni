# Registra las tareas de seleccion_ig.py --noche, que analiza solo las FECHAS NUEVAS de
# Fotos\Galeria\Trabajo (ver fechas_nuevas en seleccion_ig.py). Renzo saca fotos sabados y a veces
# jueves/domingos, asi que (1/10/2026):
#   - "IMGorgni seleccion IG":           despierta la notebook vie/dom/lun 3:30 (la noche despues de
#                                        cada dia de fotos). Solo enchufada.
#   - "IMGorgni seleccion IG cada hora": revisa cada hora mientras la notebook esta prendida, SIN
#                                        despertarla. Sin fecha nueva termina en segundos.
# Las dos pueden coincidir: el candado de seleccion_ig.py deja trabajar a una sola.
#   powershell -ExecutionPolicy Bypass -File .\registrar_tarea_ig.ps1
$dir = $PSScriptRoot
if (-not [Environment]::GetEnvironmentVariable("CLAUDE_CODE_OAUTH_TOKEN", "User")) {
    Write-Warning "sin CLAUDE_CODE_OAUTH_TOKEN de usuario: la etapa de Claude va a fallar (claude setup-token + setx)"
}
$py = (Get-Command python).Source
New-Item -ItemType Directory -Force (Join-Path $dir '_para_publicar') | Out-Null

$accion = New-ScheduledTaskAction -Execute 'cmd.exe' -WorkingDirectory $dir `
    -Argument ('/c set PYTHONIOENCODING=utf-8&& "{0}" seleccion_ig.py --noche >> _para_publicar\noche.log 2>&1' -f $py)
# IgnoreNew: si la anterior sigue (un carrete tarda ~1 h), no se apila otra de la misma tarea.
$despertar = New-ScheduledTaskSettingsSet -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -DontStopOnIdleEnd -MultipleInstances IgnoreNew -StartWhenAvailable
# 7/10/2026: con los defaults de Windows ("no iniciar con bateria" y "detener al pasar a bateria")
# la de cada hora no corrio de 12 a 14 con la notebook desenchufada, y un reinicio mato Autocull a
# mitad. Esta corre igual con bateria, y StartWhenAvailable recupera la hora perdida.
$sinDespertar = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -DontStopOnIdleEnd -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName 'IMGorgni seleccion IG' -Action $accion -Settings $despertar -Force `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday, Sunday, Monday -At 3:30) `
    -Description 'Fechas nuevas de Trabajo -> seleccion para Instagram. Despierta la notebook vie/dom/lun 3:30.' | Out-Null

$cadaHora = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'IMGorgni seleccion IG cada hora' -Action $accion -Settings $sinDespertar -Force `
    -Trigger $cadaHora `
    -Description 'Fechas nuevas de Trabajo -> seleccion para Instagram. Cada hora con la notebook prendida, sin despertarla.' | Out-Null

# 8/10/2026: edicion de lo que eligio Renato. A las 3:00 pide en la Pi las listas de las galerias donde
# apreto "Termine mi seleccion" (como el boton "Lista solicitada"), las encola y, solo si hay algo
# pendiente, abre Lightroom con el catalogo Automatico y avisa por ntfy como salio (editar_renato.py).
# Sin nada pendiente no abre Lightroom. Antes del 8/10 esto no estaba programado: se hizo a mano.
$editar = New-ScheduledTaskAction -Execute 'cmd.exe' -WorkingDirectory $dir `
    -Argument ('/c set PYTHONIOENCODING=utf-8&& "{0}" editar_renato.py --abrir >> _lightroom\noche.log 2>&1' -f $py)
$editarAjustes = New-ScheduledTaskSettingsSet -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -DontStopOnIdleEnd -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'IMGorgni editar Renato' -Action $editar -Settings $editarAjustes -Force `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 3:00) `
    -Description 'Selecciones terminadas de Renato -> Lightroom (catalogo Automatico), sin exportar. Avisa por ntfy.' | Out-Null

# La tarea de prueba del 1/10 ya no hace falta
Unregister-ScheduledTask -TaskName 'IMGorgni seleccion IG prueba' -Confirm:$false -ErrorAction SilentlyContinue

Get-ScheduledTask -TaskName 'IMGorgni*' | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ Tarea = $_.TaskName; Estado = $_.State; Proxima = $i.NextRunTime }
}
