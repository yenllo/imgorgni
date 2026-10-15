# 📁 Optimización por Tipo de Archivo

## 🎯 Objetivo

Procesar archivos en orden de velocidad para:
- ✅ Obtener resultados rápidos al inicio
- ✅ Mejor uso de caché
- ✅ Feedback temprano de progreso
- ✅ Dejar procesos lentos para el final

---

## 📊 Clasificación de Archivos

### ⚡ Tipo 0: Rápidos (Priority High)
**Extensiones:** `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`

**Características:**
- Procesamiento directo con PIL/OpenCV
- Sin decodificación RAW
- ~95% de fotos típicas
- Velocidad: **~0.5-2 segundos por foto**

**Orden de procesamiento:** **PRIMERO**

---

### 🔶 Tipo 1: Medios (Priority Medium)
**Extensiones:** `.tif`, `.tiff`

**Características:**
- Archivos más grandes
- Pueden tener múltiples capas
- Menos comunes (~3% típicamente)
- Velocidad: **~2-5 segundos por foto**

**Orden de procesamiento:** **SEGUNDO**

---

### 🐢 Tipo 2: Lentos (Priority Low)
**Extensiones:** `.cr2`, `.arw`, `.nef`, `.rw2`, `.raf`, `.raw`, `.dng`

**Características:**
- Requieren `rawpy` para decodificación
- Archivos muy grandes (20-60MB)
- Procesamiento intensivo
- ~2% de colecciones típicas
- Velocidad: **~5-15 segundos por foto**

**Orden de procesamiento:** **ÚLTIMO**

---

## 🚀 Implementación

### Fase 1: Indexación

```python
def classify_ext(path: str) -> int:
    ext = Path(path).suffix.lower()
    
    # Rápidos
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return 0
    
    # Medios
    if ext in {".tif", ".tiff"}:
        return 1
    
    # Lentos (RAW)
    if ext in SUPPORTED_RAW_EXTS:
        return 2
    
    return 1
```

**Ordenamiento:**
```python
all_files.sort(key=lambda fp: (classify_ext(fp), get_file_size_safe(fp)))
```

**Resultado:**
1. Todos los JPG/PNG (pequeños → grandes)
2. Todos los TIFF (pequeños → grandes)
3. Todos los RAW (pequeños → grandes)

---

### Fase 2: Análisis IA

```python
def classify_file_type(row):
    _, file_path = row
    ext = Path(file_path).suffix.lower()
    
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return 0
    elif ext in {".tif", ".tiff"}:
        return 1
    elif ext in {".cr2", ".arw", ".nef", ...}:
        return 2
    return 1

rows.sort(key=classify_file_type)
```

---

### Fase 3: Clustering

Aunque no procesa archivos directamente, ordena los datos por tipo para:
- Mejor localidad de caché
- Clustering más eficiente
- Métricas más consistentes

---

## 📈 Beneficios

### 1. **Feedback Temprano**
```
✅ 15 min: 45,000 JPG procesados
✅ 20 min: 2,000 TIFF procesados
⏳ 45 min: 1,500 RAW procesando... (lento pero casi terminado)
```

### 2. **Mejor Uso de Caché**
- Archivos similares procesados juntos
- Menos swapping de memoria
- Mejor hit rate en caché de disco

### 3. **Cancelación Segura**
- Si cancelas a mitad de proceso, ya tienes procesados los archivos importantes
- Los RAW pueden reprocesarse después sin perder mucho tiempo

### 4. **Optimización de Recursos**
- Workers más eficientes con archivos homogéneos
- Menos overhead de cambio de contexto

---

## 🔧 Configuración

No requiere configuración adicional. El ordenamiento es automático.

**Estadísticas mostradas:**
```
📊 Distribución:
   ⚡ JPG/PNG/GIF/WEBP: 47,532 (rápidos)
   🔶 TIFF: 1,845 (medios)
   🐢 RAW: 1,623 (lentos)
```

---

## ⏱️ Tiempos Estimados

### Dataset Típico (50,000 fotos)
- **95% JPG/PNG:** 45,000 fotos × 1s = **45 min**
- **3% TIFF:** 1,500 fotos × 3s = **5 min**
- **2% RAW:** 1,000 fotos × 10s = **10 min**

**Total:** ~60 minutos (ordenado) vs ~65 minutos (aleatorio)

**Ganancia:** No es mucha diferencia en tiempo total, pero:
- ✅ Primeros resultados en 15 minutos
- ✅ Progreso visible desde el inicio
- ✅ Cancelación segura sin pérdida significativa

---

## 🎯 Casos de Uso

### Fotógrafo Profesional (90% RAW)
```
📊 Distribución:
   ⚡ JPG/PNG: 5,000 (previews)
   🐢 RAW: 45,000 (trabajo principal)
```
**Beneficio:** Previews listos rápido, RAW al final

### Usuario Casual (98% JPG)
```
📊 Distribución:
   ⚡ JPG/PNG: 49,000
   🐢 RAW: 1,000 (algunas fotos especiales)
```
**Beneficio:** Casi todo procesado en <1 hora, RAW al final sin molestar

### Archivo Mixto
```
📊 Distribución:
   ⚡ JPG/PNG: 35,000
   🔶 TIFF: 10,000 (escaneos)
   🐢 RAW: 5,000
```
**Beneficio:** Progreso equilibrado, feedback continuo

---

## 🔍 Verificación

Para confirmar que funciona:

```python
# Ejecuta Fase 1
python main.py -ph 1

# Salida esperada:
📊 Distribución:
   ⚡ JPG/PNG/GIF/WEBP: 45,678 (rápidos)
   🔶 TIFF: 2,345 (medios)
   🐢 RAW: 1,234 (lentos)

# Observa progress bar: primeros 90% rápidos, últimos 10% más lentos
```

---

## ⚠️ Notas Técnicas

1. **El ordenamiento no afecta la BD:** Los archivos se procesan en orden pero se guardan con su ID original.

2. **Reanudable:** Si cancelas y reinicias, seguirá el orden correcto.

3. **Sin overhead:** El `sort()` es O(n log n) pero insignificante comparado con el procesamiento real.

4. **Compatible con FORCE:** El modo `--force` también respeta el ordenamiento.

---

## 📚 Referencias

- `1_indexado.py` línea ~32: `classify_ext()`
- `2_analisis.py` línea ~310: `classify_file_type()`
- `3_agrupacion.py` línea ~490: `classify_row_type()`

---

**Versión:** 3.1  
**Fecha:** Octubre 2025  
**Autor:** Sistema de Fototeca IA
