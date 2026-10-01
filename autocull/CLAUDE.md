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
- El viejo "σ medido infla las borrosas" **ya está arreglado** (σ sale del ISO; el self-test de
  `sharpness.py` pasa: 3366 nítida vs 703 borrosa).

## Selección editorial (1/10/2026)
Medido contra `data/muestra_final_2026-07-11.json` (20 fotos de la final del 11/7 juzgadas a ciegas por 5
jueces con el criterio de Instagram). Se mide con:
```powershell
& $AC -m autocull.evaluar_muestra <carpeta_con_las_20_fotos> [--ranker ranker.json]
```
| Código | Spearman(score, votos) | 0 votos en top 5 |
|---|---|---|
| antes | +0.002 | 3 |
| + nitidez del sujeto sobre el cuerpo | +0.369 | 1 |
| + pelota en el sujeto / tamaño del sujeto | +0.367 | 1 |

- **Nitidez del sujeto sobre el CUERPO, no la caja** (`sharpness.body_mask`). A 2 MP la caja de un jugador
  es mayormente fondo: el lienzo a cuadros "EX SCUOLA", la malla del arco. Eso es nítido y de alto
  contraste, así que el P95 de la caja medía el FONDO. El top 1 de la final (`DSC09526`, jugador movido
  delante del lienzo) daba 303 de "sujeto"; medido sobre el cuerpo da 181 y cae en `subject_out_of_focus`.
  La máscara = polígono del torso + segmentos gruesos de extremidades + disco de la cabeza, desde los
  keypoints de YOLO-pose. Con <4 keypoints visibles vuelve a la caja (comportamiento viejo).
- **`composition_ball` paga por la pelota EN el protagonista** (`ball_on_subject`, la misma cercanía en
  alturas de cuerpo que usa `pick_protagonist`), no por una pelota en cualquier lado (18 de 20 fotos tenían
  una: no ordenaba nada). **`subject_fill`** (alto del protagonista vs medio cuadro) premia lo que aguanta un
  recorte 4:5. `crop_potential` quedaba en 1.0 para todas (planos abiertos) y bajó a 0.05;
  `detection_confidence` (nada editorial) a 0.05. En 20 fotos estas dos señales NO se pueden validar: el
  número no se movió. Quedan por razón fotográfica; sus pesos reales deberían salir del ranker.
- `pose.face_visible` tenía `min(1, a+b)/2`: tope 0.5, medio bono inalcanzable. Arreglado.
- Pendiente visto: en el cabezazo (`DSC08997`) el protagonista elegido es el #10 de espalda, no el que salta.
  `pick_protagonist` no sabe de "en el aire".

## Ranker aprendido de selecciones pasadas (`autocull/aprender.py`)
```powershell
& $AC -m autocull aprender --par <carrete> <seleccion> [--par ...] -o ranker.json
& $AC -m autocull <carpeta> --dry-run --ranker ranker.json     # o run_pipeline(..., ranker="ranker.json")
```
- Positivos = fotos del carrete que están en la selección (cruce por nombre sin extensión ni " (2)";
  si el nombre se repite en el carrete, desempata la fecha EXIF). Negativos = el resto que pasó los filtros
  duros. `<seleccion>` puede ser una carpeta o un CSV `archivo,tomada,elegida` (lo que eligió un cliente en
  la galería de la Pi, p. ej. `data/selecciones_renato/`). Es por carrete: los DSC se repiten entre fechas.
- Del carrete se excluyen las subcarpetas derivadas (`Seleccion*`, `Export*`, `Edicion*`, `Compras`,
  `Marca de agua`, `Entrega*`): son copias de las elegidas, no el carrete crudo.
- Regresión logística en numpy (sin dependencias nuevas), features en z-score DENTRO de cada carrete, peso
  por clase. JSON legible con un peso por feature. Con ≥2 `--par` reporta precision@10/@30 dejando un
  carrete afuera, contra el `final_score` actual: **ese número es el que dice si sirve**.
- Con `--ranker`, el ranker reemplaza el compuesto como ORDEN de las sobrevivientes; filtros duros, dedup
  de ráfagas y corte percentil siguen igual. El compuesto queda en `composite_score`.
- Features cacheadas por carrete en `_autocull_aprender\` (gitignored). Si cambia el análisis, bórralo.
