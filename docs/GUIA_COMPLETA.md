# 📸 Guía Completa - Organizador de Fotos con IA

## 📁 Estructura de Carpetas Actualizada

```
C:\Git\
├── Organizador_Fotos\          # 👈 Código del proyecto
│   ├── main.py
│   ├── 1_indexado.py
│   ├── 2_analisis.py
│   ├── 3_agrupacion.py
│   ├── app.py
│   ├── config.json             # 👈 Archivo de configuración
│   ├── Marca_rb_fotos_.png     # Marca de agua
│   ├── photos.db               # Base de datos (se creará)
│   └── models\                 # Modelos ONNX (crear carpeta)
│       ├── yolov8n.onnx
│       └── model.onnx
│
└── CarpetasExternas\
    └── fotos\                  # 👈 Tus fotos aquí
        ├── 2020\
        ├── 2021\
        ├── 2022\
        └── ...
```

## 🚀 Instalación Paso a Paso

### 1️⃣ Preparar el Entorno

```bash
# Abrir CMD o PowerShell como Administrador
cd C:\Git\Organizador_Fotos

# Verificar Python (debe ser 3.10 o superior)
python --version
```

### 2️⃣ Copiar Archivos de Configuración

1. **Copia `config.json`** actualizado a `C:\Git\Organizador_Fotos\`
2. **Copia `ejecutar_actualizado.bat`** a `C:\Git\Organizador_Fotos\`
3. **Copia `verificar_sistema.py`** a `C:\Git\Organizador_Fotos\`

### 3️⃣ Verificar el Sistema

```bash
cd C:\Git\Organizador_Fotos
python verificar_sistema.py
```

Esto verificará:
- ✅ Que todos los archivos estén presentes
- ✅ Que las carpetas existan
- ✅ Que Python y las dependencias estén instaladas
- ✅ Que la configuración sea correcta

### 4️⃣ Instalar Dependencias (si faltan)

```bash
# Opción 1: Todas de una vez
pip install -r requirements.txt

# Opción 2: Una por una
pip install opencv-python numpy Pillow rawpy exifread
pip install onnxruntime-directml onnx onnxruntime-extensions
pip install huggingface_hub ultralytics scikit-learn
pip install streamlit tqdm
```

### 5️⃣ Descargar Modelos ONNX

```bash
# Si existe setup_models.py
python setup_models.py

# Si no, descarga manual:
# - YOLOv8n: https://github.com/ultralytics/assets/releases
# - CLIP/ViT: Desde Hugging Face
# Colócalos en C:\Git\Organizador_Fotos\models\
```

## 🎮 Uso del Sistema

### Método 1: Interfaz Interactiva (Recomendado)

```bash
# Doble clic en ejecutar_actualizado.bat
# O desde CMD:
cd C:\Git\Organizador_Fotos
ejecutar_actualizado.bat
```

**Menú Principal:**
1. **Diagnóstico** - Verifica que todo esté listo
2. **Instalar dependencias** - Si faltan módulos
3. **Descargar modelos** - Obtiene los modelos ONNX
4. **Fase 1** - Indexación (~45 min para 50k fotos)
5. **Fase 2** - Análisis IA (~2.5 horas)
6. **Fase 3** - Clustering (~1 min)
7. **Interfaz Web** - Ver y gestionar eventos

### Método 2: Comando Manual

```bash
cd C:\Git\Organizador_Fotos

# Fase 1: Indexación
python main.py -ph 1

# Fase 2: Análisis con IA
python main.py -ph 2

# Fase 3: Agrupación en eventos
python main.py -ph 3

# Interfaz web
streamlit run app.py
```

### Método 3: Ejecutar Todo de Una Vez

```bash
# Desde el menú del .bat, opción 11
# O manualmente:
python main.py -ph 1 && python main.py -ph 2 && python main.py -ph 3 && streamlit run app.py
```

## 📊 Qué Hace Cada Fase

### **Fase 1: Indexación (45 min)**
- 📁 Escanea todas las fotos en `C:\Git\CarpetasExternas\fotos`
- 🔍 Detecta y mueve duplicados a `_duplicados_contenido`
- 🚫 Filtra marcas de agua (si está configurado)
- 📱 Filtra screenshots móviles (si está configurado)
- 💾 Guarda metadatos en `photos.db`

### **Fase 2: Análisis IA (2.5 horas)**
- 🤖 Detecta objetos con YOLOv8 (personas, animales, objetos)
- 🎨 Genera embeddings visuales con CLIP/ViT
- 🏷️ Asigna etiquetas automáticas
- 🚀 Usa NPU/GPU con DirectML para acelerar

### **Fase 3: Clustering (1 min)**
- 📅 Agrupa fotos por fecha y similitud visual
- 🎉 Crea eventos automáticamente
- 🔗 Fusiona eventos similares
- 📝 Genera nombres descriptivos

### **Interfaz Web**
- 🖼️ Visualiza eventos con miniaturas
- ✏️ Renombra y edita eventos
- 🔍 Busca por tags, fechas, objetos
- 📤 Exporta eventos a carpetas

## ⚙️ Configuración Avanzada

### Ajustar Rendimiento (config.json)

**Para 16GB RAM:**
```json
{
  "max_workers_phase1": 8,
  "sqlite_cache_mb": 512,
  "image_batch_size": 8,
  "parallel_loading_workers": 4
}
```

**Para 32GB RAM (recomendado):**
```json
{
  "max_workers_phase1": 16,
  "sqlite_cache_mb": 1024,
  "image_batch_size": 16,
  "parallel_loading_workers": 8
}
```

**Para 64GB RAM:**
```json
{
  "max_workers_phase1": 32,
  "sqlite_cache_mb": 2048,
  "image_batch_size": 32,
  "parallel_loading_workers": 16
}
```

### Habilitar/Deshabilitar Características

```json
{
  "enable_mobile_filter": false,  // Filtro de screenshots
  "enable_yolo": true,            // Detección de objetos
  "enable_features": true,        // Embeddings visuales
  "watermark_threshold": 0.85     // Sensibilidad marca de agua
}
```

## 🔧 Solución de Problemas

### Error: "No encuentra main.py"
```bash
# Verifica que estés en el directorio correcto
cd C:\Git\Organizador_Fotos
dir *.py
```

### Error: "ModuleNotFoundError"
```bash
# Instala la dependencia faltante
pip install [nombre_del_modulo]
```

### Error: "ONNX model not found"
```bash
# Crea la carpeta models
mkdir C:\Git\Organizador_Fotos\models

# Descarga los modelos
python setup_models.py
```

### La Fase 2 es muy lenta
- Verifica que `onnxruntime-directml` esté instalado
- Ajusta `image_batch_size` en config.json (menor = menos RAM)
- Cierra otras aplicaciones para liberar memoria

### No encuentra las fotos
- Verifica que la ruta en config.json sea correcta
- Asegúrate de que haya fotos en `C:\Git\CarpetasExternas\fotos`

## 📈 Rendimiento Esperado

Para **50,000 fotos** en AMD Ryzen AI 7 con 32GB RAM:

| Fase | Tiempo | Velocidad |
|------|--------|-----------|
| Fase 1 | ~45 min | 18 fotos/seg |
| Fase 2 | ~150 min | 5 fotos/seg |
| Fase 3 | ~1 min | - |
| **Total** | **~196 min** | **~3.3 horas** |

## 🎯 Tips para Mejor Rendimiento

1. **Usa SSD** en lugar de HDD para las fotos
2. **Cierra otros programas** durante el procesamiento
3. **Organiza las fotos por año/mes** antes de procesar
4. **Excluye carpetas irrelevantes** (ediciones, exports, etc.)
5. **Ejecuta de noche** para no interrumpir tu trabajo

## 📝 Modo FORCE (Reprocesar)

Si necesitas volver a procesar todo:

```bash
# Limpiar BD y reindexar
python main.py -ph 1 -f

# Re-analizar todas las fotos
python main.py -ph 2 -f

# Re-hacer clustering
python main.py -ph 3 -f
```

## 🆘 Soporte

Si algo no funciona:

1. Ejecuta `python verificar_sistema.py`
2. Revisa los archivos de log en la carpeta del proyecto
3. Verifica que las rutas en `config.json` sean correctas
4. Asegúrate de tener espacio en disco suficiente

## ✨ Características Principales

- 🚀 **Optimizado para NPU/GPU** con DirectML
- 🔍 **Detección inteligente** de objetos y escenas
- 📊 **Agrupación automática** en eventos
- 🎯 **Filtrado automático** de duplicados y screenshots
- 💾 **Base de datos SQLite** para búsquedas rápidas
- 🌐 **Interfaz web** moderna con Streamlit
- ⚡ **Procesamiento paralelo** para máximo rendimiento

---

**Última actualización:** Octubre 2025  
**Versión:** 3.0 Optimizada  
**Compatibilidad:** Windows 10/11, Python 3.10+
