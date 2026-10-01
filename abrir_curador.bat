@echo off
REM ---------------------------------------------------------------------
REM  Abre el curador y lo deja accesible desde el telefono.
REM
REM  Doble click y listo: levanta el servidor, abre el navegador y muestra
REM  el link para el telefono. Para cerrarlo, cerra esta ventana negra.
REM
REM  La primera vez hace falta UNA cosa mas, una sola vez en la vida de la
REM  maquina: abrir el puerto en el firewall. En PowerShell COMO ADMIN:
REM
REM    New-NetFirewallRule -DisplayName "Curador IMGorgni" -Direction Inbound ^
REM      -LocalPort 5000 -Protocol TCP -Action Allow -Profile Private
REM
REM  El telefono tiene que estar en el mismo wifi que la notebook.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

REM La clave de acceso. Cambiala por la que quieras; sin esto el archivo
REM entero queda abierto a cualquiera que este en el wifi.
set CLAVE=renzo-vds-2026
set PUERTO=5000

echo.
echo   Levantando el curador...
echo.

REM El token va en la URL: al abrirse a la red, la clave rige tambien acá.
start "" "http://127.0.0.1:%PUERTO%/?t=%CLAVE%"
python web_curador.py --host 0.0.0.0 --port %PUERTO% --token %CLAVE%

REM Si python cierra solo, la ventana queda abierta para poder leer el error.
echo.
echo   El curador se cerro. Revisa el mensaje de arriba.
pause
