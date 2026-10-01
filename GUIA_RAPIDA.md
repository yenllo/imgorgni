# ðŸš€ GuÃ­a RÃ¡pida - Organizador de Fotos IA

## ðŸ“‹ Comandos BÃ¡sicos

### Fase 1: IndexaciÃ³n
```bash
# Indexar nuevas fotos (continÃºa desde Ãºltima ejecuciÃ³n)
python main.py -ph 1

# Forzar re-indexaciÃ³n completa
python main.py -ph 1 --force
```

### Fase 2: AnÃ¡lisis IA
```bash
# Analizar fotos pendientes
python main.py -ph 2

# Forzar re-anÃ¡lisis de TODAS las fotos
python main.py -ph 2 -f
```

### Fase 3: Clustering de Eventos
```bash
# Crear eventos desde cero
python main.py -ph 3

# Forzar re-clustering (elimina eventos existentes)
python main.py -ph 3 --force

# Clustering + abrir UI automÃ¡ticamente
python main.py -ph 3 --start-ui
```

### Interfaz Web
```bash
# Iniciar manualmente
streamlit run app.py
```

## âš™ï¸ ParÃ¡metros Disponibles

| ParÃ¡metro | Corto | DescripciÃ³n |
|-----------|-------|-------------|
| `--phase` | `-ph` | Fase a ejecutar (1, 2 o 3) |
| `--force` | `-f` | Forzar reprocesamiento completo |
| `--config` | `-c` | Ruta a config.json personalizado |
| `--start-ui` | - | Abrir UI despuÃ©s de Fase 3 |

## ðŸ”„ Modo FORCE: Â¿CuÃ¡ndo usarlo?

### âœ… Usar `--force` cuando:
- Cambiaste parÃ¡metros de clustering (`cluster_time_window_hours`, etc.)
- Actualizaste modelos ONNX (YOLO, CLIP)
- Quieres medir rendimiento de optimizaciones
- Los eventos quedaron mal agrupados

### âŒ NO usar `--force` cuando:
- Solo aÃ±adiste fotos nuevas (automÃ¡ticamente las detecta)
- Primera ejecuciÃ³n (no hay progreso que ignorar)
- Proceso anterior se interrumpiÃ³ (retoma automÃ¡ticamente)

## ðŸ“Š Flujo TÃ­pico de Trabajo

```bash
# 1. Primera vez - Todas las fases
python main.py -ph 1                    # Indexar
python main.py -ph 2                    # Analizar con IA
python main.py -ph 3 --start-ui         # Agrupar y abrir UI

# 2. AÃ±adiste fotos nuevas
python main.py -ph 1                    # Solo indexa nuevas
python main.py -ph 2                    # Solo analiza nuevas
python main.py -ph 3 -f --start-ui      # Re-clustering completo

# 3. Cambiaste config.json (umbrales)
python main.py -ph 3 -f                 # Solo re-agrupar

# 4. Nuevos modelos ONNX
python main.py -ph 2 -f                 # Re-analizar todo
python main.py -ph 3 -f                 # Re-agrupar
```

## ðŸŽ¯ Ejemplos de Uso Real

### Ejemplo 1: Setup inicial
```bash
# Escanear 50k fotos por primera vez
python main.py -ph 1
# â±ï¸ ~1.5 horas

python main.py -ph 2
# â±ï¸ ~4 horas (con NPU AMD)

python main.py -ph 3 --start-ui
# â±ï¸ ~2 minutos + UI abierta
```

### Ejemplo 2: AÃ±adir 500 fotos nuevas
```bash
# Solo procesar las nuevas
python main.py -ph 1
python main.py -ph 2
python main.py -ph 3 -f  # Force para re-agrupar todo
```

### Ejemplo 3: Ajustar clustering
```bash
# Editar config.json:
# cluster_cosine_threshold: 0.85 â†’ 0.75 (mÃ¡s permisivo)

# Re-agrupar con nuevos parÃ¡metros
python main.py -ph 3 --force
```

### Ejemplo 4: Testear optimizaciones
```bash
# Medir velocidad con optimizaciones nuevas
python main.py -ph 2 -f -c config_test.json
```

## ðŸ› ï¸ Troubleshooting

### "No hay fotos nuevas"
âœ… Normal si ya ejecutaste Fase 1. Usa `-f` solo si quieres re-escanear.

### "Eventos fragmentados (CumpleaÃ±os en 3 partes)"
âœ… Ejecuta Fase 3 con `--force` para re-clustering.
âœ… Ajusta `cluster_cosine_threshold` en config.json (bajar a 0.75-0.70).

### "AnÃ¡lisis muy lento"
âœ… Verifica que `preferred_provider` = "DML" en config.json.
âœ… Revisa Task Manager: debe usar NPU/GPU AMD.

### "Out of memory"
âœ… Reduce `batch_size` en config.json (64 â†’ 32).
âœ… Reduce `photos_per_page` en la UI (200 â†’ 100).

## ðŸ“ Notas Importantes

1. **Fase 1** es inteligente: solo indexa archivos nuevos (skip de paths existentes).
2. **Fase 2** usa `needs_analysis` flag: solo analiza fotos marcadas.
3. **Fase 3** con `--force` elimina eventos pero NO toca fotos ni anÃ¡lisis.
4. **Modo force** NO borra fotos originales, solo resetea progreso de esa fase.

## ðŸŽ¨ PersonalizaciÃ³n de Eventos en UI

DespuÃ©s de clustering automÃ¡tico:
1. Entra a la UI: `streamlit run app.py`
2. Busca eventos con ðŸ¤– (auto-generados)
3. Renombra con âœï¸ (se marcan con âœï¸)
4. Fusiona eventos similares con ðŸ”—
5. Divide eventos con âœ‚ï¸

Los eventos personalizados (âœï¸) aparecen primero en listas de fusiÃ³n.

## ðŸ“ˆ MÃ©tricas de Rendimiento

| Hardware | Fase 1 (50k) | Fase 2 (50k) | Fase 3 |
|----------|-------------|-------------|---------|
| Sin optimizar | ~3h | ~8h | ~5min |
| **Optimizado (32GB + NPU)** | **~1.5h** | **~4h** | **~2min** |

Mejoras aplicadas:
- âœ… 64 workers en indexaciÃ³n
- âœ… Batch size 64 + DML provider
- âœ… Cache SQLite 256MB
- âœ… Clustering paralelo (todos los cores)
- âœ… Auto-merge de eventos similares

---

**VersiÃ³n**: 2.0 Optimizado  
**Hardware**: AMD Ryzen AI 7 + 32GB RAM + NPU  
**Modelos**: YOLOv8n + CLIP ViT-B/32
