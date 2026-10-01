# Registra la tarea nocturna de seleccion_ig.py (3:30, despierta la notebook, solo enchufada).
# Con -Prueba ademas lanza YA una corrida sobre la final del 11/7 como tarea (fuera de cualquier sandbox).
param([switch]$Prueba)
$dir = $PSScriptRoot
if (-not [Environment]::GetEnvironmentVariable("CLAUDE_CODE_OAUTH_TOKEN", "User")) {
    Write-Warning "sin CLAUDE_CODE_OAUTH_TOKEN de usuario: la etapa de Claude va a fallar (claude setup-token + setx)"
}
$py = (Get-Command python).Source
New-Item -ItemType Directory -Force (Join-Path $dir '_para_publicar') | Out-Null
$set = New-ScheduledTaskSettingsSet -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 5) -DontStopOnIdleEnd

function Accion($argsPy, $log) {
    New-ScheduledTaskAction -Execute 'cmd.exe' -WorkingDirectory $dir `
        -Argument ('/c set PYTHONIOENCODING=utf-8&& "{0}" seleccion_ig.py {1} >> _para_publicar\{2} 2>&1' -f $py, $argsPy, $log)
}

Register-ScheduledTask -TaskName 'IMGorgni seleccion IG' -Action (Accion '--noche' 'noche.log') `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 3:30) -Settings $set -Force `
    -Description 'Elige las mejores fotos de los trabajos nuevos para Instagram (seleccion_ig.py --noche)' | Out-Null

if ($Prueba) {
    Register-ScheduledTask -TaskName 'IMGorgni seleccion IG prueba' `
        -Action (Accion '"Liga ExScuola\Liga ExScuola 2026-1\2026-07-11 F9 (Final)"' 'prueba.log') `
        -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddYears(5)) -Settings $set -Force | Out-Null
    Start-ScheduledTask -TaskName 'IMGorgni seleccion IG prueba'
}
Get-ScheduledTask -TaskName 'IMGorgni seleccion IG*' | Select TaskName, State
