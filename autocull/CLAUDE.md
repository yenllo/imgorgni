# CLAUDE.md — Autocull

Culler automático de fotos **deportivas (fútbol 7 nocturno)** en Python. Selecciona las mejores
tomas de carretes de 1600–2800 fotos. Meta: ser "su propio Aftershoot", **especializado en deporte**
(no generalizar a bodas/retratos). Sony A7R III a ISO alto es el caso base.

## Cómo correr
Desde el 30/9/2026 el código vive en **IMGorgni** (`autocull/`) y se corre desde la **raíz de IMGorgni**.
El venv sigue en `C:\Renzo\Proyectos\Autocull\venv` (un venv no se puede mover) y los modelos `.onnx`
están en `IMGorgni\models\`. En IMGorgni lo usan `autocull_runner.py` (curador) y `seleccion_ig.py`.
La carpeta vieja `C:\Renzo\Proyectos\Autocull` queda solo por el venv, los `.pt` y `_serve/`.
```powershell
$AC = "C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"
& $AC -m autocull.web           # app web -> http://127.0.0.1:8000
& $AC -m autocull <carpeta>      # CLI: analiza, mueve a _selected/, escribe reportes
& $AC -m autocull <carpeta> --dry-run   # solo analiza + CSV, no mueve nada
& $AC -m autocull <carpeta> --undo      # revierte el último move (usa _autocull_undo.json)
& $AC -m autocull <carpeta> --from-csv autocull_report.csv  # recalcula umbral sin re-analizar
```
Flags CLI útiles: `--threshold` (percentil de corte final, def 30), `--sharpness-percentile` (def 15),
`--min-persons` (def 1), `--xmp` (sidecars Lightroom para rechazos), `--workers`, `--backend`.

Python 3.12. onnxruntime **CPU** (sin DirectML/NPU → siempre CPU).

### Setup de modelos
`setup.ps1` exporta los dos `.onnx` que `config.py` resuelve; **ambos** deben existir en `models/` (de la raíz
del proyecto) o los workers fallan al inicializar.
Los **dos** son `s`, no `nano` — medido sobre carrete real, nano degrada las dos señales que importan:
- `models/yolov8s.onnx` — la **pelota** (sus personas se descartan). La pelota ancla la elección del
  protagonista y nano solo la encuentra en 39% de las fotos nocturnas (yolov8s: 51%, yolov8m: 54% pero 5.6×
  más lento → no vale la pena). Ver `config.yolo_ball_confidence_threshold`.
- `models/yolov8s-pose.onnx` — **personas** + keypoints. Ojo: las personas de este modelo **son el pool de
  candidatos** de `pick_protagonist`, así que un jugador que no detecta jamás puede ser elegido como sujeto.
  s-pose encuentra 14% más jugadores que nano (mediana 6 vs 5 por foto) y cambia el protagonista en 9% de
  las fotos. Costo: 71ms vs 30ms.

`autocull/models/face_landmarker.task` (MediaPipe) ya viene versionado.

### Tests
No hay pytest. La convención es un **self-check bajo `if __name__ == "__main__"`** en cada módulo con lógica
no trivial (estilo ponytail). Para verificar un módulo, córrelo directo, p. ej.:
```powershell
& $AC -m autocull.scoring.scorer   # imprime scores de datos sintéticos
```

## Arquitectura — el flujo que cruza varios archivos
Pipeline de tiers en cascada. Cada foto pasa por filtros baratos primero; solo las sobrevivientes pagan
la inferencia cara. **El scoring es por percentiles de la sesión completa, no por umbrales fijos** — esto
es central y explica por qué las decisiones finales se calculan *después* de procesar todas las fotos.

- **`pipeline.py`** orquesta. `run_pipeline()` reparte fotos a un `ProcessPoolExecutor` (contexto **spawn**,
  4 workers). `init_worker()` carga YOLO + YOLO-pose + MediaPipe **una vez por proceso** (no por foto).
  `process_single_image()` corre Tier 1 y, si pasa, Tier 2, devolviendo un dict anidado `{tier1, tier2, exif}`.
  `files=` recibe una lista explícita (lo usa IMGorgni: las fotos de un evento no forman una carpeta).
- **Tier 1 (por foto, barato, CPU):** ruido (`analyzers/noise.py`) → nitidez SNR noise-aware
  (`analyzers/sharpness.py`) → blur FFT post-denoise (`analyzers/blur.py`) → exposición global
  (`analyzers/exposure.py`). `compute_tier1_result()` rechaza **inmediatamente** solo por exposición
  (sub/sobre-expuesto absoluto); nitidez y blur se difieren al corte por percentil.
- **Tier 2 (por foto, caro, solo si Tier 1 pasa):** `SubjectDetector` (yolov8s) da la pelota, `PoseDetector`
  (yolov8s-pose) da personas con keypoints. Por cada persona: SNR local, exposición local, expresión facial
  (`analyzers/face.py`).
  - **Elección del protagonista** (`scorer.pick_protagonist`) — el punto más sutil del sistema. El sujeto se
    elige por **composición** (cercanía a la pelota, tamaño, centralidad) y *recién después* se le mide la
    nitidez. Elegir "la persona más nítida" (como hacía antes) es circular: redefine al sujeto como quien
    haya salido nítido, así que **nunca puede detectar que el autofoco se fue al fondo** — justo la foto que
    hay que descartar. La cercanía a la pelota decae en *alturas de cuerpo*, no en píxeles de la diagonal
    (eso comprimía la señal a 0.7-1.0 y la volvía inerte).
  - **`protagonist_confident`** = hay pelota **o** hay una sola persona. Solo con eso se permite rechazar por
    `subject_out_of_focus` / `subject_too_dark`. Sin ancla (≈41% de las fotos) el sujeto es una conjetura de
    tamaño/centro, y el programa **no rechaza** por él: esas fotos caen a los filtros globales y al percentil
    final, que no dependen de saber quién es el sujeto.
- **Scoring final (`scoring/scorer.py::compute_final_scores`, una sola vez con toda la sesión):**
  1. Corte por percentil de nitidez y blur globales (Tier 1 diferido).
  2. Corte por percentil de SNR del sujeto (Tier 2) — **solo entre las fotos con `protagonist_confident`**;
     las demás ni se rechazan ni fijan el umbral (no se juzga a un sujeto que no se sabe cuál es).
  3. **Score compuesto** = suma ponderada (`config.score_weights`). Clave: las métricas de nitidez se
     convierten a **rank percentil dentro de la sesión** vía `_rank_map()`, no a multiplicadores fijos
     (que saturaban en el cap y dejaban de discriminar). Bonos por pelota/pose, penalización por ojos
     cerrados/mueca.
  4. **Tier 5 — dedup de ráfagas:** agrupa PASS por timestamp EXIF (≤1.5s) y deja solo el de mayor score.
  5. **Corte percentil final:** rechaza lo que quede bajo `final_reject_percentile`.
- **`config.py::AnalysisConfig`** es el dataclass central (~59 edges en el grafo). Todos los pesos,
  percentiles y rutas viven ahí; los flags CLI lo sobreescriben antes de `__post_init__()`.
- **Salida (`output/`):** `reporter.py` (CSV/JSON), `xmp.py` (sidecars Lightroom), `mover.py` (mueve a
  `_selected/` + log de undo).

### Web (`autocull/web/`) — capa separada, NO toca el CLI
FastAPI + UI single-page (vanilla JS en `static/`). Flujo **no destructivo**:
- Análisis en background (`jobs.py`) reusa `pipeline.run_pipeline`. Estado por carpeta en
  `<carpeta>/_autocull_session.json` (`state.py`), miniaturas en `_autocull_cache/` (`thumbs.py`).
- **Ver fotos** (`POST /api/browse`) abre cualquier carpeta *sin correr el análisis*, en este orden:
  sesión web guardada → `autocull_report.json` de una corrida del CLI (`state.import_report`, trae scores y
  decisiones reales gratis) → listado crudo de la carpeta (todo `NEW`, sin score, para marcar a mano).
  Solo escribe `_autocull_session.json`.
- Los `name` de los records son **rutas relativas a la carpeta** (`101MSDCF/DSC05837.ARW`), porque los
  carretes Sony vienen partidos en `100MSDCF/`, `101MSDCF/`… `thumbs.get_thumbnail` acepta subcarpetas
  (sigue rechazando `..` y todo lo que se salga de la carpeta) y aplana la ruta en el nombre de caché.
- Orden **por score** = ranking plano de toda la sesión (una sola sección); el orden **cronológico** es el
  que agrupa por ráfaga. Ordenar dentro de ráfagas de 2-3 fotos no ordenaba nada visible.
- El lightbox navega foto a foto: `←→` (o clic en los tercios laterales; el centro cierra) sobre `S.visible`,
  así que respeta el filtro y el orden activos, y `P`/`X`/`A` también funcionan con la foto ampliada.
- **Salida a Lightroom** = botón *XMP para Lightroom* (`POST /api/export` con `mode=xmp`): escribe sidecars
  con las aprobadas en `5★/Green` y las rechazadas en `1★/Red`. No copia ni mueve un solo ARW.
  El sidecar va como `DSC00023.xmp` (**convención Adobe**, reemplaza la extensión): Lightroom **no** lee
  `DSC00023.ARW.xmp`, que es el estilo darktable/RawTherapee y era lo que se escribía antes. Ojo: los
  sidecars solo valen para RAW — en JPEG Lightroom lee los metadatos del propio archivo.
- Decisiones keep/reject son **overrides** encima de la decisión automática: `effective_decision()` hace que
  el override del usuario gane. Nada mueve ni borra hasta `/api/export` (opt-in: `copy` a `_selected/` o
  escribir XMP). Bindea a `127.0.0.1` (single-user local).

## Notas / convenciones
- Los trabajos de fútbol viven en `K:\Fotos\Galeria\Trabajo\<liga>\...\<fecha>\` (JPEG arriba, RAW en `RAW\`).
- El EXIF **importa mucho** y hay que preservarlo al exportar: el ISO alimenta `expected_sigma()` (el
  divisor de toda la nitidez) y el timestamp alimenta el dedup de ráfaga. Sin EXIF, el ISO cae al default
  3200 y el dedup degrada a mtime/filename. Verificado en el carrete de 2026-07-11: los JPEG exportados
  **sí traen** EXIF (ISO 8000, timestamp, ILCE-7RM3), así que el export actual está bien configurado.
- Threading: los workers **deben** correr con 1 thread por librería (`OMP/OPENBLAS/MKL/OPENCV_NUM_THREADS=1`),
  seteado tanto en `cli.py` (top-level) como en `init_worker()` — sin esto `np.fft.fft2` cuelga por
  thrashing (16 workers × 16 threads). No quitar esos setenv.
- Pendiente conocido: la métrica global `snr_sharpness` puede **inflar fotos desenfocadas** (al desenfocar
  cae el σ de ruido más rápido que el gradiente P95, así que el cociente P95/σ sube). El self-test de
  `analyzers/sharpness.py` **falla a propósito** por esto: una checkerboard borrosa puntúa 1334 vs 580 la
  nítida. No es una regresión — es el bug esperando arreglo. El sujeto local (peso 30%) lo compensa en parte.
  Muy probablemente es lo que puso una foto movida (`DSC09526`) como top 1 de la final del 11/7: ver
  `data/muestra_final_2026-07-11.json` y `TAREA_FABLE.md` en la raíz.
