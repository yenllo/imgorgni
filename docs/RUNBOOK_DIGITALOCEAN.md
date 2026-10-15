# Runbook — Procesar la fototeca completa en DigitalOcean

**Crédito: $200, vence el 31 de julio.** Hoy es 23 de julio → quedan 8 días.

## Dataset real (medido 23/07)

| Disco | Fotos | Tamaño |
|---|---|---|
| `E:\` | 49,874 | 166 GB |
| `F:\` | 90,405 | 918 GB |
| **Total** | **140,279** | **1,084 GB** |

De esas, **34,183 son RAW** (ARW 14,684 · DNG 9,587 · CR2 9,052 · NEF 860) y 105,234 son JPG.
Los RAW son el 80% del peso y la parte lenta de procesar.

## Costos

| Fase | Máquina | Costo |
|---|---|---|
| Subida | Droplet $6/mo + Volume 1.5 TB | ~$40 (8 días) |
| Procesamiento (~6–8 h) | GPU RTX 4000 Ada @ $0.76/hr | ~$6 |
| Bajada | Droplet chico | ~$2 |

**Total ~$50–60 de los $200.** El costo no es el problema: **el tiempo de subida sí.**

## Estrategia: Google Drive como puente

Como vas a consolidar todo en Google Drive igual (tenés fotos en Drive, OneDrive, HDD, SSD y notebook), **el orden óptimo cambia y te conviene**:

```
[HDD + SSD + notebook + OneDrive]  --tu internet-->  Google Drive
                                                          |
                                          red de datacenter (rápida)
                                                          v
                                                   DigitalOcean
```

**Drive → DigitalOcean va por la red del datacenter, no por tu conexión.** Ese tramo son horas, no días, y no depende de tu velocidad de subida. Con esto:

- La subida lenta desde casa la hacés **una sola vez**, hacia Drive, y te sirve como backup permanente.
- El droplet solo se enciende cuando los datos ya están en Drive → **no pagás días de volume esperando**.
- Si algo sale mal en el procesamiento, volvés a bajar de Drive sin resubir nada desde casa.

**Lo único que sigue atado a tu internet es HDD → Drive.** Medí tu subida real en https://fast.com:

| Tu subida | 1,084 GB a Drive |
|---|---|
| 100 Mbps | ~24 h |
| 50 Mbps | ~48 h |
| 20 Mbps | ~120 h (5 días) |

Para 1 TB en Drive necesitás **Google One 2 TB** (~$10/mes). Es el único costo fuera del crédito de DO.

> **Subí primero los JPG** (105k fotos, 255 GB) y después los RAW (829 GB). Con los JPG arriba ya podés hacer una corrida completa mientras el resto sigue subiendo. El pipeline es reanudable: Fase 1 saltea lo ya indexado y Fase 2 solo procesa lo que tiene `needs_analysis=1`.

---

## Paso 0 — Sobre el acceso a GPU: no hace falta pedir nada

**Los GPU Droplets RTX 4000 Ada, RTX 6000 Ada y L40S están disponibles sin solicitud** desde mayo de 2025. Los creás como cualquier droplet normal, al instante.

Solo los H100/H200 en grandes volúmenes pueden requerir hablar con ventas — y **no los necesitás**:

| GPU | VRAM | Precio | ¿Alcanza? |
|---|---|---|---|
| **RTX 4000 Ada** | 20 GB | **$0.76/hr** | **Sí, de sobra** |
| RTX 6000 Ada | 48 GB | $1.57/hr | Innecesario |
| H100 | 80 GB | ~$3.39/hr | Muy innecesario |

Tu carga (CLIP ViT-B/32 + YOLOv8 + InsightFace, todos juntos en batch) usa **menos de 8 GB de VRAM**. La RTX 4000 Ada te sobra, viene con 8 vCPU, 32 GB de RAM y 500 GB de NVMe local.

**Conclusión: no pierdas tiempo con solicitudes.** Creá la RTX 4000 Ada cuando tengas los datos arriba y listo. Si por algo no hubiera stock en tu región, la RTX 6000 Ada es el reemplazo directo.

### Sobre la BD vieja (`E:\X pero importante\GIT\photos.db`)

Tenés una BD con 26,413 fotos ya procesadas, pero **conviene descartarla y reprocesar de cero en la nube**:

- Los embeddings son el **fallback HSV** (histograma de color, 192 dims), no CLIP → agrupa por color dominante, no por contenido real.
- **0 tags de YOLO** (venía deshabilitado en la config) → los nombres de eventos salen genéricos.
- Solo 15,421 de 26,413 tienen fecha (58%) → el resto no puede agruparse temporalmente.

En la nube con GPU vas a tener CLIP + YOLO reales sobre las 49,874 fotos, y el `read_exif_info` reconstruido ahora cae al mtime del archivo cuando no hay EXIF, así que la cobertura de fechas sube a ~100%. Es exactamente el salto de calidad que buscás.

> La BD vieja no estorba: guardala como backup por si querés comparar.

---

## Paso 1 — Droplet de subida + Volume

1. **Crear Volume**: Panel → Volumes → **1.5 TB** (1,084 GB de fotos + margen para BD y hardlinks).
   Región: **elegí una que tenga GPU Droplets disponibles** (NYC2, TOR1, ATL1 suelen tener).
   ⚠️ Los volumes NO se mueven entre regiones. Si te equivocás de región, hay que resubir todo.

2. **Crear Droplet** básico ($6/mo, Ubuntu 22.04) en **la misma región**, y adjuntar el volume.

3. **Montar el volume** (SSH al droplet):
```bash
# DO te da el comando exacto en el panel del volume; típicamente:
mkfs.ext4 /dev/disk/by-id/scsi-0DO_Volume_<nombre>   # SOLO la primera vez
mkdir -p /mnt/fotos
mount -o discard,defaults /dev/disk/by-id/scsi-0DO_Volume_<nombre> /mnt/fotos
echo '/dev/disk/by-id/scsi-0DO_Volume_<nombre> /mnt/fotos ext4 defaults,nofail,discard 0 0' >> /etc/fstab
```

4. **Verificar que los hardlinks funcionan** (los usa la Fase 4):
```bash
cd /mnt/fotos && touch a && ln a b && ls -li a b && rm a b
# Los dos deben mostrar el MISMO número de inodo (primera columna)
```

---

## Paso 2 — Consolidar todo en Google Drive (desde tu PC)

Ya tenés rclone v1.74.4. Configurá el remote `gdrive` con tu propio client_id (los pasos que ya tenías son correctos: Cloud Console → Drive API → OAuth Desktop app → `rclone config`).

Usar tu propio client_id importa: el compartido de rclone tiene cuota global y con 1 TB te vas a topar con `rateLimitExceeded`.

```powershell
# Cada origen a su propia carpeta: así el detector de duplicados sabe de dónde vino cada copia
rclone copy "E:\" gdrive:Fototeca/HDD-E --progress --transfers 8 --checkers 16 --drive-chunk-size 128M `
  --include "*.{jpg,jpeg,png,gif,webp,tif,tiff}"
rclone copy "F:\" gdrive:Fototeca/HDD-F --progress --transfers 8 --checkers 16 --drive-chunk-size 128M `
  --include "*.{jpg,jpeg,png,gif,webp,tif,tiff}"
rclone copy "C:\Users\renzo\Pictures" gdrive:Fototeca/Notebook --progress --transfers 8

# Después los RAW (829 GB, el 80% del peso)
rclone copy "E:\" gdrive:Fototeca/HDD-E --progress --transfers 8 --drive-chunk-size 128M `
  --include "*.{cr2,arw,nef,dng,rw2,raf}"
rclone copy "F:\" gdrive:Fototeca/HDD-F --progress --transfers 8 --drive-chunk-size 128M `
  --include "*.{cr2,arw,nef,dng,rw2,raf}"
```

**OneDrive → Drive sin pasar por tu PC**: configurá también un remote `onedrive` y rclone hace la transferencia nube-a-nube.
```powershell
rclone copy onedrive:Imágenes gdrive:Fototeca/OneDrive --progress --transfers 8
```

No te preocupes por subir la misma foto dos veces desde orígenes distintos: **para eso está la Fase 5**, que las detecta y te dice cuál conservar.

## Paso 2c — Rescatar las fotos de Lightroom (⚠️ tiempo limitado)

**Tus fotos probablemente siguen ahí.** Adobe conserva los originales de Lightroom Cloud **un año completo** después de que caduca la suscripción. Pasado ese año, te manda un aviso por email y te da 30 días más antes de borrarlos definitivamente.

**Verificá hoy hace cuánto cancelaste.** Si estás cerca del año, esto es más urgente que todo lo demás de este documento — el crédito de DO se puede reponer, esas fotos no.

**No existe transferencia directa Adobe → Google Drive.** rclone no tiene backend de Adobe Creative Cloud (lo pidieron en 2019 y nunca se implementó), y Adobe no expone una API de storage para esto. Hay que bajar y volver a subir.

El camino oficial es **Adobe Lightroom Downloader**, una app gratuita que funciona *sin suscripción activa* y baja todos tus originales de una:

1. Descargala de https://helpx.adobe.com/lightroom-cc/using/lightroom-cloud-deletion.html
2. Entrá con tu Adobe ID
3. **Elegí como destino una carpeta del HDD de 1 TB**, no el disco del notebook (te vas a quedar sin espacio)
4. Cuando termine, sumala al flujo normal:

```powershell
rclone copy "F:\Lightroom_rescatado" gdrive:Fototeca/Lightroom --progress --transfers 8
```

Ojo con la distinción: el año de retención aplica a **Lightroom Cloud**. Los archivos sueltos en Creative Cloud (no-Lightroom) tienen una regla más dura — el espacio baja a 5 GB y tenés 30 días para bajar lo que exceda.

Las fotos de Lightroom van a solapar mucho con lo que ya tenés en los HDD (son los mismos disparos, quizás editados). **No las filtres a mano**: la Fase 5 las cruza contra todo lo demás y te dice cuál copia conservar.

## Paso 2d — Sobre la exportación de Google Photos (Takeout)

Pediste una exportación de Takeout (aviso: "puede tardar mucho", llega por email, 14 días para descargar). Dos cosas a saber:

**Las fotos de Takeout vienen SIN la fecha adentro.** Google guarda la fecha real y el GPS en un archivo `.json` al lado de cada foto (un "sidecar"). Si esos JSON se pierden, las fotos quedan con fecha de descarga (hoy) y el clustering las agruparía todas juntas en un "evento" falso de julio 2026.

**El pipeline ya lo maneja** — agregué soporte para leer esos sidecars (verificado en `test_takeout.py`). Pero para que funcione:

1. Al descomprimir los ZIP de Takeout, **mantené los `.json` junto a las fotos**. No muevas solo las imágenes.
2. Subí a Drive la carpeta completa de Takeout, con los JSON incluidos:
   ```powershell
   rclone copy "F:\Takeout" gdrive:Fototeca/GooglePhotos --progress --transfers 8
   ```
3. El indexado usa la fecha del sidecar automáticamente cuando la foto no trae EXIF.

> Si ya tenés esas mismas fotos en los HDD con su EXIF original, la Fase 5 va a preferir esas y descartar las de Takeout (que son versiones recomprimidas). El Takeout te sirve sobre todo para las fotos que **solo** existían en Google Photos.

## Paso 2b — Google Drive → DigitalOcean (rápido, en el droplet)

```bash
# En el droplet: instalar y configurar rclone con el MISMO client_id
curl https://rclone.org/install.sh | sudo bash
rclone config    # remote 'gdrive', usá "y" en headless y pegá el token de tu PC

rclone copy gdrive:Fototeca /mnt/fotos --transfers 16 --checkers 32 --progress --stats 30s
```

Este tramo va por la red de DigitalOcean: **horas, no días**, y no consume tu internet.

**Por qué rclone y no scp**: es resumible, paralelo, y verifica checksums. Con 1TB, un corte de conexión a mitad no te hace empezar de cero.

Si tenés varias partes / varios discos, corré un `rclone copy` por cada uno hacia el mismo destino.

> **Tip**: dejá la PC prendida sin suspender. `powercfg /change standby-timeout-ac 0`

---

## Paso 3 — Subir el código

```powershell
# Desde C:\Renzo\Proyectos\IMGorgni
rclone copy . do:/root/IMGorgni --include "*.py" --include "*.txt" --include "*.json" --include "*.png"
```

En el droplet, activar la config de nube:
```bash
cd /root/IMGorgni && cp config_cloud.json config.json
```

`config_cloud.json` ya viene con rutas Linux (`/mnt/fotos`), `preferred_provider: CUDA`, y workers/batches subidos para máquina grande.

---

## Paso 4 — GPU Droplet y procesamiento

**Recién cuando la subida terminó.** Cada hora de GPU encendida sin procesar es crédito tirado.

1. Crear **GPU Droplet** (RTX 4000 Ada es la mejor relación precio/velocidad) con la imagen **"AI/ML Ready"** (trae drivers NVIDIA y CUDA ya instalados).
2. **Desatachar el volume del droplet chico y atacharlo al GPU droplet** (panel de DO), montarlo igual que en el paso 1.3.
   > ⚠️ Si DO no permite adjuntar volumes a GPU droplets en tu región, alternativa: los GPU droplets traen disco NVMe local grande — copiá ahí con `rclone copy` droplet-a-droplet (red interna de DO, muy rápida, gratis).

3. Instalar y verificar:
```bash
cd /root/IMGorgni
apt update && apt install -y python3-pip tmux
pip install -r requirements_cloud.txt

# CRÍTICO: verificar que CUDA aparece, si no la GPU no se usa
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Debe listar 'CUDAExecutionProvider'

nvidia-smi   # debe mostrar la GPU

python3 setup_models.py                    # YOLO + CLIP visual
python3 precompute_scene_embeddings.py     # escenas CLIP (una sola vez, ~1 min)
```

4. **Procesar dentro de `tmux`** (si se cae el SSH, el proceso sigue):
```bash
tmux new -s fotos

python3 main.py -ph 1    # Indexado: hashes, EXIF, GPS, duplicados exactos  (~1-2 h)
python3 main.py -ph 2    # Análisis IA ← usa la GPU        (~6-8 h; los 34k RAW son lo lento)
python3 5_duplicados.py  # Detector de duplicados: REPORTE, no borra        (~5 min)
python3 3b_personas.py   # Agrupa caras en personas                         (~10 min)
python3 main.py -ph 3    # Clustering en eventos                            (~30-60 min)

# Ctrl+B luego D para salir dejándolo corriendo
# tmux attach -t fotos   para volver
```

Podés correr Fase 1 + 2 varias veces mientras sigue subiendo: solo procesan lo nuevo.

Monitorear desde otra sesión SSH:
```bash
python3 db_stats.py        # fotos indexadas / analizadas / pendientes
python3 progress_phase3.py # eventos creados
watch -n 5 nvidia-smi      # uso de GPU
```

---

## Paso 4b — Duplicados (importante al mezclar 5 orígenes)

Con fotos venidas de HDD, SSD, notebook, Drive y OneDrive, la misma imagen va a aparecer varias veces: idéntica, recomprimida por WhatsApp, redimensionada por Drive, o como par RAW+JPG.

```bash
python3 5_duplicados.py                    # genera duplicados.csv, NO toca archivos
python3 5_duplicados.py --umbral 3         # más estricto (menos falsos positivos)
python3 5_duplicados.py --incluir-raw-jpg  # tratar RAW+JPG del mismo disparo como par
```

Detecta cuatro clases y para cada grupo elige **cuál conservar** por este orden: RAW sobre JPG → más resolución → conserva EXIF de cámara → no está en carpeta de WhatsApp/descargas → archivo más grande.

El CSV trae una fila por descarte con la razón en texto (`"está en una carpeta de copias"`, `"el otro es RAW"`, …). **Revisalo en Excel antes de hacer nada.**

Cuando estés conforme:
```bash
python3 5_duplicados.py --mover    # a _duplicados_detectados/, NO borra
```

Los manda a una carpeta de cuarentena separada por tipo. Borrarlos es decisión tuya, a mano. El script nunca elimina un archivo.

> Corré la Fase 5 **antes** de la 3 (clustering): así los eventos no se llenan de la misma foto repetida.

## Paso 5 — Revisar y nombrar eventos (opcional pero recomendado)

La Fase 3 genera nombres automáticos a partir de los tags (`person, beach, sunset`). Para ponerles nombres reales:

```bash
# En el droplet
streamlit run app.py --server.port 8501 --server.address 127.0.0.1
```
```powershell
# En tu PC: túnel SSH, luego abrir http://localhost:8501
ssh -L 8501:localhost:8501 root@<IP-del-droplet>
```

Los eventos que renombres a mano quedan protegidos: la Fase 3 **nunca los borra** ni los recalcula (`3_agrupacion_mejorado.py` los preserva por diseño).

---

## Paso 6 — Exportar a carpetas

```bash
python3 4_exportar.py --out /mnt/fotos/_organizadas --por-persona
```

Genera la jerarquía de 2 niveles, al estilo de tu `E:\Fotos_Organizadas`:
```
/mnt/fotos/_organizadas/
├── Fotos eventos/
│   ├── 2023-09-12 Obra teatro/
│   └── 2024-12-25 christmas celebration/
├── Fotos personas/
│   ├── Persona 3/            ← renombrable a "Fer", "Simo", etc.
│   └── 2023-05-10 group photo of people/
├── Fotos paisajes/
│   └── 2024-01-05 Viña del Mar - beach, sunset/
├── Fotos animales/
├── Fotos deportes/
├── Fotos Astro/
└── Fotos descartes/          ← borrosas y oscuras, para revisar y borrar
```

Usa **hardlinks**: las carpetas no ocupan espacio extra. Una foto puede estar en su evento Y en la carpeta de la persona sin duplicarse.

Para nombrar personas antes de exportar:
```bash
python3 3b_personas.py --listar
python3 3b_personas.py --nombrar 3 "Fer"
```

---

## Paso 7 — Bajar los resultados ANTES del 31

**Orden de prioridad** (por si no llega el tiempo):

```powershell
# 1. La base de datos primero: es chica (~cientos de MB) y contiene TODO el trabajo
rclone copy do:/mnt/fotos/photos.db "E:\Fotos_organizadas\"

# 2. Las carpetas organizadas
rclone copy do:/mnt/fotos/_eventos "E:\Fotos_organizadas\eventos" --transfers 8 --progress
```

> **Plan B si la bajada de 1TB no llega antes del 31**: con solo `photos.db` en tu PC podés reorganizar las fotos locales sin volver a procesar nada. Editá `config.json` apuntando `root_dir` a tu disco externo y corré `python 4_exportar.py`. Los originales nunca se movieron: la BD solo guarda rutas.

---

## Paso 8 — Destruir todo

Cuando bajaste lo que querías: **destruir droplets y volume** en el panel. Si el crédito venció y quedan recursos activos, te los cobran a tu tarjeta.

---

## Ajustes si algo va lento

| Síntoma | Ajuste en `config.json` |
|---|---|
| GPU al 100%, poca velocidad | Subir `image_batch_size` (64 → 128) |
| GPU ociosa, CPU al 100% | Subir `parallel_loading_workers` (carga de imágenes es el cuello) |
| Se queda sin RAM | Bajar `image_batch_size` y `sqlite_cache_mb` |
| Fase 3 tarda muchísimo | Subir `dbscan_min_samples`; el clustering es O(n²) en el peor caso |
| Eventos demasiado fragmentados | Bajar `cluster_cosine_threshold` (0.75 → 0.65) o subir `cluster_time_window_hours` |
| Eventos demasiado grandes | Subir `cluster_cosine_threshold` (0.75 → 0.85) |

Los parámetros de clustering se pueden reajustar y correr `main.py -ph 3` de nuevo cuantas veces quieras: **no hay que repetir la Fase 2** (que es la cara). Los eventos que renombraste a mano se conservan.

Lo mismo con las personas: `python3 3b_personas.py --eps 0.5` re-agrupa las caras sin volver a detectarlas. Bajá `eps` si mezcla dos personas distintas; subilo si parte la misma persona en varias.

---

## Plan B: si la subida no llega al 31

Priorizá por valor, no por orden de disco:

1. **Solo JPG** (105,234 fotos, 255 GB — el 75% de tus fotos en el 24% del peso). A 20 Mbps son ~28 h. Procesás eso completo y tenés la organización de la mayoría de tu fototeca.
2. **RAW después**, si sobra tiempo. Los RAW suelen tener un JPG hermano ya subido; el evento ya existe y solo hay que sumarles las fotos.
3. **Lo que no llegue queda para otra corrida**: la BD es incremental, no se pierde nada.

Y guardá siempre `photos.db` primero al bajar: con esa BD podés reorganizar los originales de tus discos localmente sin reprocesar nada.

---

## Costo real del crédito si la subida se demora

El crédito vence el 31, pero **el gasto se concentra en las horas que el droplet está encendido**, no en el calendario. Con Drive de puente:

| Escenario | Gasto de crédito |
|---|---|
| Drive listo el 26 → procesás el 27 | ~$15 (volume 2 días + GPU 8 h) |
| Drive listo el 30 → procesás el 30 | ~$10 (volume 1 día + GPU 8 h) |
| Drive nunca llega a estar completo | Procesás lo que haya; el resto queda para después |

**No perdés nada por esperar a que Drive esté listo.** Lo que sí perdés es encender el droplet antes de tiempo y pagar días de volume mirando una barra de progreso.

Si al 30 solo subiste los JPG: procesá esos. Son 105k de tus 140k fotos y el resultado ya vale la pena. Los RAW se suman en otra corrida — la BD es incremental y no se pierde nada de lo hecho.
