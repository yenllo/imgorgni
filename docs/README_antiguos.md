# Archivos Antiguos / No Utilizados

Esta carpeta contiene archivos que no son parte activa del flujo de trabajo principal.

## Archivos movidos:

### Backups de app.py
- `app_backup.py` - Backup anterior de la interfaz
- `app_broken.py` - Versión con errores
- `app_broken_backup.py` - Backup de versión con errores

### Scripts de utilidad temporal
- `fix_indentation.py` - Script para corregir indentación (ya no necesario)
- `diagnostico.py` - Script de diagnóstico temporal
- `migrate_tags.py` - Script de migración de tags (ya ejecutado)

### Versiones antiguas de scripts de fase
- `3_agrupacion_original.py` - Versión original sin modo force correcto
- `3_agrupacion_mejorado.py` - Versión mejorada (ahora es la principal)

## Archivos activos en la raíz del proyecto:

### Scripts de fases (en orden):
1. `1_indexado.py` - Indexación de archivos
2. `2_analisis.py` - Análisis con IA
3. `3_agrupacion.py` - Clustering de eventos (con modo force corregido)

### Aplicación principal:
- `app.py` - Interfaz web Streamlit
- `main.py` - Orquestador de fases

### Módulos de utilidad:
- `db.py` - Funciones de base de datos
- `utils_batch.py` - Procesamiento por lotes
- `utils_images.py` - Utilidades de imágenes
- `utils_log.py` - Sistema de logging
- `utils_tags.py` - Manejo de tags

### Configuración:
- `config.json` - Configuración principal
- `config_optimizado.json` - Configuración optimizada
- `requirements.txt` - Dependencias Python

### Documentación:
- `README.md` - Documentación principal
- `CHANGELOG.md` - Registro de cambios
- `GUIA_RAPIDA.md` - Guía rápida de uso
- `INICIO_RAPIDO.md` - Inicio rápido
- `OPTIMIZACIONES.md` - Documentación de optimizaciones
- `ORDEN_ARCHIVOS.md` - Orden de archivos
- `TROUBLESHOOTING.md` - Solución de problemas
