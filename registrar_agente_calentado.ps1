# Registra "rbfotos agente calentado": agente_calentado.py al iniciar sesion.
# 28/10/2026: la tarea habia desaparecido (junto con las de registrar_tarea_ig.ps1) desde el
# 18/10; sin agente no hay latido, la Pi calienta ella misma y los pedidos nunca llegan aca.
#   powershell -ExecutionPolicy Bypass -File .\registrar_agente_calentado.ps1
$dir = $PSScriptRoot
$pyw = (Get-Command python).Source -replace 'python\.exe$', 'pythonw.exe'
$accion = New-ScheduledTaskAction -Execute $pyw -Argument 'agente_calentado.py' -WorkingDirectory $dir
$ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'rbfotos agente calentado' -Action $accion -Settings $ajustes -Force `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
    -Description 'Toma los "Calentar ahora" del panel de la Pi y los calienta en la notebook.' | Out-Null
Start-ScheduledTask -TaskName 'rbfotos agente calentado'
Get-ScheduledTask -TaskName 'rbfotos agente calentado' | Select-Object TaskName, State
