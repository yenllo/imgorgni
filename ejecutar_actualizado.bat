@echo off
REM Script de ejecuciÃ³n del Organizador de Fotos - ACTUALIZADO
REM UbicaciÃ³n del cÃ³digo: C:\Git\Organizador_Fotos
REM UbicaciÃ³n de fotos: C:\Git\CarpetasExternas\fotos

SETLOCAL ENABLEDELAYEDEXPANSION

REM Colores (solo funciona en Windows 10+)
echo [92m
echo ===============================================
echo   ORGANIZADOR DE FOTOS IA
echo   Version 3.0 - Optimizado para AMD Ryzen AI
echo ===============================================
echo [0m
echo.

REM IMPORTANTE: Cambiar al directorio del proyecto
cd /d "C:\Git\Organizador_Fotos"

echo [96mDirectorio de trabajo: %CD%[0m
echo [96mCarpeta de fotos: C:\Git\CarpetasExternas\fotos[0m
echo.

REM Verificar que existe main.py
if not exist "main.py" (
    echo [91mERROR: main.py no encontrado en:[0m
    echo %CD%
    echo.
    echo [93mVerifica que el cÃ³digo estÃ© en C:\Git\Organizador_Fotos[0m
    pause
    exit /b 1
)

REM Verificar que existe config.json
if not exist "config.json" (
    echo [91mERROR: config.json no encontrado en:[0m
    echo %CD%
    echo.
    echo [93mCopia el archivo config.json actualizado a C:\Git\Organizador_Fotos[0m
    pause
    exit /b 1
)

REM Verificar que existe la carpeta de fotos
if not exist "C:\Git\CarpetasExternas\fotos" (
    echo [91mERROR: Carpeta de fotos no encontrada:[0m
    echo C:\Git\CarpetasExternas\fotos
    echo.
    echo [93mVerifica la ruta de las fotos[0m
    pause
    exit /b 1
)

REM Verificar Python
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [91mERROR: Python no encontrado en el PATH[0m
    echo.
    echo [93mInstala Python 3.10+ desde python.org[0m
    pause
    exit /b 1
)

REM Mostrar versiÃ³n de Python
echo [96mPython instalado:[0m
python --version
echo.

REM MenÃº interactivo
:MENU
echo [92m===============================================[0m
echo [92m  MENU PRINCIPAL[0m
echo [92m===============================================[0m
echo.
echo [96m1.[0m Diagnostico del sistema
echo [96m2.[0m Instalar dependencias
echo [96m3.[0m Descargar modelos ONNX
echo.
echo [96m4.[0m Fase 1: Indexacion
echo [96m5.[0m Fase 2: Analisis IA
echo [96m6.[0m Fase 3: Clustering
echo [96m7.[0m Abrir interfaz web (Streamlit)
echo.
echo [96m8.[0m Fase 1 FORCE (limpiar BD)
echo [96m9.[0m Fase 2 FORCE (re-analizar todo)
echo [96m10.[0m Fase 3 FORCE (re-clustering)
echo.
echo [96m11.[0m Ejecutar TODO (Fase 1+2+3+UI)
echo [96m0.[0m Salir
echo.
echo [92m===============================================[0m
set /p OPCION=[93mSelecciona una opcion (0-11): [0m

if "%OPCION%"=="0" goto END
if "%OPCION%"=="1" goto DIAGNOSTICO
if "%OPCION%"=="2" goto INSTALAR
if "%OPCION%"=="3" goto MODELOS
if "%OPCION%"=="4" goto FASE1
if "%OPCION%"=="5" goto FASE2
if "%OPCION%"=="6" goto FASE3
if "%OPCION%"=="7" goto UI
if "%OPCION%"=="8" goto FASE1_FORCE
if "%OPCION%"=="9" goto FASE2_FORCE
if "%OPCION%"=="10" goto FASE3_FORCE
if "%OPCION%"=="11" goto TODO

echo [91mOpcion invalida[0m
echo.
goto MENU

:DIAGNOSTICO
echo.
echo [96m===========================================[0m
echo [96m  EJECUTANDO DIAGNOSTICO[0m
echo [96m===========================================[0m
echo.
if exist "diagnostico.py" (
    python diagnostico.py
) else (
    echo [93mArchivo diagnostico.py no encontrado[0m
    echo [93mVerificando configuracion manualmente...[0m
    echo.
    echo Archivos principales:
    if exist "main.py" echo [92m âœ“[0m main.py
    if exist "1_indexado.py" echo [92m âœ“[0m 1_indexado.py
    if exist "2_analisis.py" echo [92m âœ“[0m 2_analisis.py
    if exist "3_agrupacion.py" echo [92m âœ“[0m 3_agrupacion.py
    if exist "app.py" echo [92m âœ“[0m app.py
    if exist "db.py" echo [92m âœ“[0m db.py
    if exist "config.json" echo [92m âœ“[0m config.json
    echo.
)
echo.
pause
goto MENU

:INSTALAR
echo.
echo [96m===========================================[0m
echo [96m  INSTALANDO DEPENDENCIAS[0m
echo [96m===========================================[0m
echo.
pip install opencv-python numpy Pillow rawpy exifread
pip install onnxruntime-directml onnx onnxruntime-extensions
pip install huggingface_hub ultralytics scikit-learn
pip install streamlit tqdm
echo.
echo [92mDependencias instaladas[0m
echo.
pause
goto MENU

:MODELOS
echo.
echo [96m===========================================[0m
echo [96m  DESCARGANDO MODELOS ONNX[0m
echo [96m===========================================[0m
echo.
if exist "setup_models.py" (
    python setup_models.py
) else (
    echo [93msetup_models.py no encontrado[0m
    echo [93mDescarga manual los modelos ONNX y colÃ³calos en:[0m
    echo C:\Git\Organizador_Fotos\models\
    echo.
    echo Necesitas:
    echo - yolov8n.onnx
    echo - model.onnx (CLIP/ViT)
)
echo.
pause
goto MENU

:FASE1
echo.
echo [96m===========================================[0m
echo [96m  FASE 1: INDEXACION[0m
echo [96m===========================================[0m
echo.
echo [93mAnalizando carpeta: C:\Git\CarpetasExternas\fotos[0m
echo.
python main.py -ph 1
echo.
echo [92mFase 1 completada[0m
echo.
pause
goto MENU

:FASE2
echo.
echo [96m===========================================[0m
echo [96m  FASE 2: ANALISIS IA[0m
echo [96m===========================================[0m
echo.
echo [93mEsto puede tardar 2-3 horas para 50,000 fotos[0m
echo.
python main.py -ph 2
echo.
echo [92mFase 2 completada[0m
echo.
pause
goto MENU

:FASE3
echo.
echo [96m===========================================[0m
echo [96m  FASE 3: CLUSTERING[0m
echo [96m===========================================[0m
echo.
python main.py -ph 3
echo.
echo [92mFase 3 completada[0m
echo.
pause
goto MENU

:FASE1_FORCE
echo.
echo [91m===========================================[0m
echo [91m  FASE 1 FORCE - LIMPIARA LA BD[0m
echo [91m===========================================[0m
echo.
echo [93mADVERTENCIA: Esto eliminara TODA la BD[0m
set /p CONFIRM=[93mEscribe 'SI' para confirmar: [0m
if /i not "%CONFIRM%"=="SI" (
    echo [93mOperacion cancelada[0m
    goto MENU
)
echo.
python main.py -ph 1 -f
echo.
echo [92mFase 1 FORCE completada[0m
echo.
pause
goto MENU

:FASE2_FORCE
echo.
echo [91m===========================================[0m
echo [91m  FASE 2 FORCE - RE-ANALIZAR TODO[0m
echo [91m===========================================[0m
echo.
echo [93mADVERTENCIA: Re-analizara TODAS las fotos[0m
set /p CONFIRM=[93mEscribe 'SI' para confirmar: [0m
if /i not "%CONFIRM%"=="SI" (
    echo [93mOperacion cancelada[0m
    goto MENU
)
echo.
python main.py -ph 2 -f
echo.
echo [92mFase 2 FORCE completada[0m
echo.
pause
goto MENU

:FASE3_FORCE
echo.
echo [91m===========================================[0m
echo [91m  FASE 3 FORCE - RE-CLUSTERING[0m
echo [91m===========================================[0m
echo.
echo [93mADVERTENCIA: Eliminara eventos existentes[0m
set /p CONFIRM=[93mEscribe 'SI' para confirmar: [0m
if /i not "%CONFIRM%"=="SI" (
    echo [93mOperacion cancelada[0m
    goto MENU
)
echo.
python main.py -ph 3 -f
echo.
echo [92mFase 3 FORCE completada[0m
echo.
pause
goto MENU

:UI
echo.
echo [96m===========================================[0m
echo [96m  ABRIENDO INTERFAZ WEB[0m
echo [96m===========================================[0m
echo.
echo [93mLa UI se abrira en tu navegador[0m
echo [93mPresiona Ctrl+C para detener el servidor[0m
echo.
streamlit run app.py
echo.
pause
goto MENU

:TODO
echo.
echo [96m===========================================[0m
echo [96m  EJECUTANDO TODO (FASE 1+2+3+UI)[0m
echo [96m===========================================[0m
echo.
echo [93mEsto puede tardar 3-5 horas...[0m
set /p CONFIRM=[93mEscribe 'SI' para confirmar: [0m
if /i not "%CONFIRM%"=="SI" (
    echo [93mOperacion cancelada[0m
    goto MENU
)
echo.

echo [96m--- Ejecutando Fase 1 ---[0m
python main.py -ph 1
if %ERRORLEVEL% NEQ 0 (
    echo [91mError en Fase 1[0m
    pause
    goto MENU
)

echo.
echo [96m--- Ejecutando Fase 2 ---[0m
python main.py -ph 2
if %ERRORLEVEL% NEQ 0 (
    echo [91mError en Fase 2[0m
    pause
    goto MENU
)

echo.
echo [96m--- Ejecutando Fase 3 ---[0m
python main.py -ph 3
if %ERRORLEVEL% NEQ 0 (
    echo [91mError en Fase 3[0m
    pause
    goto MENU
)

echo.
echo [92m===========================================[0m
echo [92m  TODAS LAS FASES COMPLETADAS[0m
echo [92m===========================================[0m
echo.
echo [96mAbriendo interfaz web...[0m
start streamlit run app.py
echo.
pause
goto MENU

:END
echo.
echo [92mGracias por usar el Organizador de Fotos IA[0m
echo.

ENDLOCAL
exit /b 0
