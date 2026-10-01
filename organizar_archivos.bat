@echo off
echo ====================================
echo   Organizando archivos antiguos
echo ====================================
echo.

REM Crear estructura de carpetas
mkdir archivos_antiguos\backups 2>nul
mkdir archivos_antiguos\versiones_anteriores 2>nul
mkdir archivos_antiguos\scripts_temporales 2>nul

REM Mover backups
if exist app_backup.py (
    move app_backup.py archivos_antiguos\backups\
    echo [OK] app_backup.py movido
)

if exist app_broken_backup.py (
    move app_broken_backup.py archivos_antiguos\backups\
    echo [OK] app_broken_backup.py movido
)

REM Mover versiones anteriores
if exist app_broken.py (
    move app_broken.py archivos_antiguos\versiones_anteriores\
    echo [OK] app_broken.py movido
)

if exist config_optimizado.json (
    move config_optimizado.json archivos_antiguos\versiones_anteriores\
    echo [OK] config_optimizado.json movido
)

REM Mover scripts temporales
if exist fix_indentation.py (
    move fix_indentation.py archivos_antiguos\scripts_temporales\
    echo [OK] fix_indentation.py movido
)

if exist migrate_tags.py (
    move migrate_tags.py archivos_antiguos\scripts_temporales\
    echo [OK] migrate_tags.py movido
)

echo.
echo ====================================
echo   Organizacion completada
echo ====================================
echo.
echo Archivos movidos a:
echo - archivos_antiguos\backups\
echo - archivos_antiguos\versiones_anteriores\
echo - archivos_antiguos\scripts_temporales\
echo.
pause
