# ⚡ INICIO RÁPIDO - Solución al Error "No encuentra main.py"

## 🎯 Solución Inmediata (1 clic)

**Haz doble clic en:**
```
ejecutar.bat
```

Este script:
- ✅ Verifica que estás en el directorio correcto
- ✅ Comprueba que Python está instalado
- ✅ Muestra un menú interactivo
- ✅ Ejecuta las fases automáticamente

---

## 📋 Usando el Script Batch

### Opción 1: Doble Clic (Más Fácil)
1. Abre `C:\Script\Organizador2`
2. Haz doble clic en `ejecutar.bat`
3. Selecciona la opción del menú

### Opción 2: Desde CMD/PowerShell
```bash
cd C:\Script\Organizador2
ejecutar.bat
```

---

## 🎮 Menú del Script

```
===============================================
  MENU PRINCIPAL
===============================================

1. Diagnostico del sistema
2. Fase 1: Indexacion
3. Fase 2: Analisis IA
4. Fase 3: Clustering
5. Abrir interfaz web (Streamlit)

6. Fase 1 FORCE (limpiar BD)
7. Fase 2 FORCE (re-analizar todo)
8. Fase 3 FORCE (re-clustering)

9. Ejecutar TODO (Fase 1+2+3+UI)
0. Salir
===============================================
```

---

## 🔧 Diagnóstico Manual

Si el script batch no funciona, ejecuta:

```bash
# 1. Navega al directorio
cd C:\Script\Organizador2

# 2. Ejecuta diagnóstico
python diagnostico.py

# 3. Lee el reporte completo
```

---

## 🐛 Errores Comunes y Soluciones

### Error: "main.py no encontrado"

**Causa:** Ejecutando desde directorio incorrecto.

**Solución:**
```bash
# CMD/PowerShell
cd C:\Script\Organizador2

# Verifica
dir main.py
```

### Error: "Python no encontrado"

**Causa:** Python no está en el PATH.

**Solución:**
```bash
# Encuentra Python
where python

# Si no aparece, reinstala Python con "Add to PATH" marcado
```

### Error: "ModuleNotFoundError"

**Causa:** Dependencias no instaladas.

**Solución:**
```bash
cd C:\Script\Organizador2
pip install -r requirements.txt
```

---

## ✅ Verificación Rápida

Antes de ejecutar, verifica:

```bash
# Estás en el directorio correcto?
cd
# Debe mostrar: C:\Script\Organizador2

# Existe main.py?
dir main.py
# Debe mostrar: main.py

# Python funciona?
python --version
# Debe mostrar: Python 3.10.x o superior

# Config existe?
dir config.json
# Debe mostrar: config.json
```

---

## 🚀 Primera Ejecución Completa

### Usando el Script Batch (Recomendado)
1. Abre `ejecutar.bat`
2. Selecciona opción `1` (Diagnóstico)
3. Si todo está ✅, selecciona opción `9` (Ejecutar TODO)
4. Espera 3-5 horas
5. La UI se abrirá automáticamente

### Manual (Avanzado)
```bash
cd C:\Script\Organizador2

# Diagnóstico
python diagnostico.py

# Si todo OK:
python main.py -ph 1    # ~45 min
python main.py -ph 2    # ~2.5 horas
python main.py -ph 3    # ~1 min

# Abrir UI
streamlit run app.py
```

---

## 📞 Si Nada Funciona

1. **Captura pantalla** del error completo
2. **Ejecuta:**
   ```bash
   cd C:\Script\Organizador2
   python diagnostico.py > diagnostico.txt
   notepad diagnostico.txt
   ```
3. **Copia el contenido** de `diagnostico.txt`
4. **Comparte** el error completo

---

## 🎯 Comandos de Emergencia

Si `ejecutar.bat` no funciona, prueba esto:

```bash
# PowerShell (como Administrador)
cd C:\Script\Organizador2
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python.exe .\main.py -ph 1
```

---

## 📚 Documentación Adicional

- `TROUBLESHOOTING.md` - Guía completa de solución de problemas
- `OPTIMIZACIONES.md` - Detalles de las mejoras de rendimiento
- `GUIA_RAPIDA.md` - Comandos rápidos
- `CHANGELOG.md` - Historial de cambios

---

## ✨ Tips

1. **Siempre usa `ejecutar.bat`** - Es la forma más segura
2. **Ejecuta diagnóstico primero** - Detecta problemas antes de empezar
3. **Lee los mensajes** - Los errores suelen ser auto-explicativos
4. **Usa rutas absolutas** - Si los relativos fallan

---

**Última actualización:** Octubre 2025  
**Versión:** 3.0 Optimizado
