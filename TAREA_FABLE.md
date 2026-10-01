# Tarea: selección más precisa + aprendizaje por selecciones pasadas

Lee `CLAUDE.md` y `autocull/CLAUDE.md` primero. El código de Autocull está en `autocull/`;
el resto del repo es IMGorgni (el archivo de fotos y el curador) y **no es parte de esta tarea**: no lo toques salvo
`autocull_runner.py` si cambia la interfaz. Escribe en español de Chile (tuteo, nunca voseo).

## Por qué

`final_score` es un percentil técnico dentro de la sesión, y medido el 30/9/2026 **no sirve para elegir
qué publicar**. En `data/muestra_final_2026-07-11.json`, 5 jueces vieron a ciegas 20 fotos de la final
del 11/7 (2.905 fotos, 291 PASS), con el criterio de `data/criterio_editorial_ig.md`:

- El **top 1** (`DSC09526`, 90,99) está **movido y fuera de foco**, y pasó todos los filtros de nitidez.
  Ningún juez lo eligió.
- El **#4** (`DSC08922`, 89,33) es la arquera caminando con la pelota, sin acción. 0 votos.
- La **arenga del equipo** (`DSC08451`) quedó al fondo de las PASS (55,84).
- Las que sí eligieron (`DSC00281` 5 votos, `DSC08997` 4, `DSC00678` 4) tienen acción en su punto
  máximo: pelota en el aire en el área, cabezazo en salto, penal en el instante del remate.

## Tarea 1: precisión (primero)

1. Averigua en el código **por qué una foto movida saca el puntaje máximo**. Primer sospechoso, ya
   documentado en `autocull/CLAUDE.md`: `snr_sharpness` global **sube** al desenfocar (el σ de ruido cae
   más rápido que el gradiente P95), y el self-test de `analyzers/sharpness.py` falla a propósito por eso.
   Arréglalo hasta que ese self-test pase. Además, verifica (no supongas): ¿la nitidez se mide sobre el protagonista o sobre la foto completa? ¿`pick_protagonist`
   elige al sujeto correcto? ¿Hay algo que premie la ACCIÓN (pelota cerca del protagonista, pose en
   movimiento: pie arriba, salto, brazos) por sobre un jugador quieto, caminando o de espalda?
2. Corrige el scoring. Cada cambio tiene que tener una razón fotográfica o física, no "mover el número":
   la muestra son 20 fotos y es fácil sobreajustar.
3. Agrega `autocull/evaluar_muestra.py <carpeta_con_las_fotos>`. Corre el pipeline sobre esas fotos y
   reporta el Spearman entre `final_score` y `votos_si`, y cuántas fotos con 0 votos quedan en el top 5.
   Las fotos **no están en el repo**: este script lo corre Renzo en local, antes y después.

## Tarea 2: aprender de selecciones pasadas

1. Agrega el comando `python -m autocull aprender --par <carrete> <carpeta_seleccion> [--par ...]
   [--etiquetas archivo.csv] -o ranker.json`.
   - Positivos: las fotos del carrete que aparecen en la carpeta de selección. Renzo exporta sus
     elegidas a carpetas como `seleccion/edicion_basica` y `edicion_completa`. Se emparejan por nombre
     sin extensión, ignorando sufijos tipo ` (2)`/`(2)`, y desempatando por fecha EXIF cuando el nombre
     se repite (las tarjetas reinician la numeración DSC).
   - Negativos: el resto del carrete.
   - `--etiquetas`: CSV `archivo,elegida` para otras fuentes, como lo que eligen los clientes en la
     galería.
2. Modelo: lo más simple que funcione sobre las features que Autocull ya calcula, por ejemplo regresión
   logística en numpy. Guárdalo como **JSON legible**, con pesos por feature, para poder auditarlo.
   Aprovecha `--from-csv`, que ya recalcula sin volver a analizar las fotos.
3. Agrega `--ranker ranker.json` a la CLI y un parámetro equivalente en `run_pipeline`. Con eso, el
   ranker reemplaza al percentil para ordenar. Los filtros duros de rechazo (movida, ojos cerrados,
   etc.) se mantienen.
4. Valida dejando un carrete afuera: precision@10 y @30 del ranker contra el `final_score` actual.

## Restricciones

- No hay fotos en el repo ni acceso al disco de fotos. Prueba con datos sintéticos, con un self-check
  bajo `if __name__ == "__main__"` (la convención del repo; no hay pytest). El entrenamiento real lo
  corre Renzo en local.
- **No rompas la interfaz que usa IMGorgni**: `run_pipeline("", cfg, progress_callback=..., files=[...])`
  y las claves `filepath`, `final_score`, `decision`, `reject_reason`, `error` de cada resultado.
- Python 3.12, Windows, onnxruntime solo CPU. Nada de dependencias nuevas si no son imprescindibles,
  y si lo son, justifícalo en el PR.
- Actualiza `autocull/CLAUDE.md` con lo que cambie.

## Entregable

Un PR contra `main` que incluya qué cambió y por qué, los comandos exactos para medir la muestra y
para entrenar con los carretes de Renzo, y lo que no se pudo verificar sin fotos.
