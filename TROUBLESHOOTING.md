# ðŸš¨ SoluciÃ³n de Problemas - Fase 1

## Error: "No encuentra main.py"

### ðŸ” DiagnÃ³stico RÃ¡pido

Ejecuta este script para identificar el problema:

```bash
python diagnostico.py
```

Este script verificarÃ¡:
- âœ… Archivos principales
- âœ… ConfiguraciÃ³n
- âœ… Dependencias instaladas
- âœ… Modelos ONNX
- âœ… Base de datos

---

## ðŸ› ï¸ Soluciones Comunes

### 1. **Ejecutar desde el directorio correcto**

```bash
# Navega al directorio del proyecto
cd C:\Script\Organizador2

# Verifica que estÃ¡s en el lugar correcto
dir main.py

# Si ves main.py, ejecuta:
python main.py -ph 1
```

### 2. **Ruta absoluta**

Si el problema persiste, usa rutas absolutas:

```bash
python C:\Script\Organizador2\main.py -ph 1 -c C:\Script\Organizador2\config.json
```

### 3. **Verificar Python correcto**

```bash
# Ver versiÃ³n de Python
python --version

# Debe ser Python 3.10 o superior
# Si tienes mÃºltiples versiones, usa:
python.exe --version
py -3.10 --version
```

### 4. **Reinstalar dependencias**

```bash
cd C:\Script\Organizador2
pip install -r requirements.txt --upgrade
```

---

## ðŸ“‹ Checklist Pre-EjecuciÃ³n

Antes de ejecutar `python main.py -ph 1`, verifica:

- [ ] EstÃ¡s en `C:\Script\Organizador2`
- [ ] Existe `main.py` en el directorio actual
- [ ] Existe `config.json` en el directorio actual
- [ ] `config.json` tiene `root_dir` y `db_path` correctos
- [ ] La carpeta `root_dir` existe (donde estÃ¡n las fotos)
- [ ] Dependencias instaladas (`pip list | findstr onnxruntime`)

---

## ðŸ› Errores EspecÃ­ficos y Soluciones

### Error: "ModuleNotFoundError: No module named 'db'"

**Causa:** No estÃ¡s ejecutando desde el directorio correcto.

**SoluciÃ³n:**
```bash
cd C:\Script\Organizador2
python main.py -ph 1
```

---

### Error: "FileNotFoundError: [Errno 2] No such file or directory: 'config.json'"

**Causa:** `config.json` no estÃ¡ en el directorio actual.

**SoluciÃ³n:**
```bash
# Verificar ubicaciÃ³n de config.json
dir config.json

# Si no existe, cÃ³pialo:
copy C:\Script\Organizador2\config.json .

# O especifica ruta completa:
python main.py -ph 1 -c C:\Script\Organizador2\config.json
```

---

### Error: "ImportError: DLL load failed"

**Causa:** Falta onnxruntime-directml.

**SoluciÃ³n:**
```bash
pip uninstall onnxruntime
pip install onnxruntime-directml
```

---

### Error: "âŒ Error: No se encontrÃ³ el script: 1_indexado.py"

**Causa:** Archivos del proyecto incompletos.

**SoluciÃ³n:**
```bash
# Verifica que existen todos los archivos
dir *.py

# DeberÃ­as ver:
# 1_indexado.py
# 2_analisis.py
# 3_agrupacion.py
# main.py
# db.py
# utils_images.py
# utils_log.py
# app.py
```

---

## âœ… Test de Funcionamiento

Una vez solucionado, prueba:

```bash
# 1. DiagnÃ³stico
python diagnostico.py

# 2. Si todo estÃ¡ OK, ejecuta Fase 1
python main.py -ph 1

# DeberÃ­as ver:
# ============================================================
# ðŸ” Iniciando Fase 1: IndexaciÃ³n
# [HH:MM:SS] Fase 1: Preparando BD y cargando Ã­ndices existentes
# ...
```

---

## ðŸ†˜ Si Nada Funciona

1. **Captura pantalla del error completo**
2. **Ejecuta y copia salida:**
   ```bash
   python diagnostico.py > diagnostico.txt
   notepad diagnostico.txt
   ```
3. **Verifica contenido de config.json:**
   ```bash
   type config.json
   ```

---

## ðŸ“ž InformaciÃ³n de Debug

Para reportar el problema, incluye:

```bash
# Sistema operativo
ver

# VersiÃ³n de Python
python --version

# UbicaciÃ³n de archivos
dir *.py
dir config.json

# Directorio actual
cd

# Salida de diagnÃ³stico
python diagnostico.py
```

---

## ðŸŽ¯ Comando Definitivo (DeberÃ­a Funcionar)

```bash
# Abrir PowerShell o CMD
# Navegar al directorio
cd C:\Script\Organizador2

# Ejecutar con rutas explÃ­citas
python.exe "%cd%\main.py" -ph 1 -c "%cd%\config.json"
```

Si esto tampoco funciona, el problema es mÃ¡s profundo (Python corrupto, permisos, etc.).

---

## ðŸ’¾ Estructura Correcta del Proyecto

```
C:\Script\Organizador2\
â”œâ”€â”€ main.py                 â† Punto de entrada
â”œâ”€â”€ 1_indexado.py          â† Fase 1
â”œâ”€â”€ 2_analisis.py          â† Fase 2
â”œâ”€â”€ 3_agrupacion.py        â† Fase 3
â”œâ”€â”€ app.py                 â† Interfaz web
â”œâ”€â”€ db.py                  â† Base de datos
â”œâ”€â”€ utils_images.py        â† Utilidades imÃ¡genes
â”œâ”€â”€ utils_log.py           â† Logging
â”œâ”€â”€ utils_batch.py         â† Batch processing
â”œâ”€â”€ config.json            â† ConfiguraciÃ³n â­
â”œâ”€â”€ requirements.txt       â† Dependencias
â”œâ”€â”€ diagnostico.py         â† Script diagnÃ³stico
â”œâ”€â”€ fotos\                 â† Fotos (definido en config.json)
â”œâ”€â”€ models\                â† Modelos ONNX
â”‚   â”œâ”€â”€ yolov8n.onnx
â”‚   â””â”€â”€ model.onnx
â””â”€â”€ photos.db              â† Base de datos (se crea automÃ¡ticamente)
```

---

**Ãšltima actualizaciÃ³n:** Octubre 2025
