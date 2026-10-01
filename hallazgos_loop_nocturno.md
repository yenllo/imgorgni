# Hallazgos del loop nocturno — iniciado 2026-08-05

Formato: **[estado]** descripción — evidencia — acción

Estados: `CONFIRMADO` (bug real, verificado con datos) · `ARREGLADO` (aplicado, con backup) · `PENDIENTE` (necesita tu decisión) · `DESCARTADO` (parecía bug, no lo era)

---

## ESTADO AL CIERRE DEL 2026-08-05 (tarde) — LEER ESTO PRIMERO

Se cerraron **todos** los pendientes que dependian de codigo o datos. Detalle
completo abajo en "CIERRE DE PENDIENTES". Resumen de una linea cada uno:

| Pendiente | Estado |
|-----------|--------|
| Los 3.292 pares EXACTO bloqueados por cadena | **RESUELTO** — redirigidos y borrados, 40 GB liberados |
| Cola de duplicados llena de fantasmas | **RESUELTO** — 130.844 → 69.647 pendientes reales (65.394 visibles) |
| 372 rutas huerfanas | **RESUELTO** — al basurero, fuera de los eventos |
| Filtro "Viajes" muerto | **YA NO EXISTIA** — pero aparecio un desajuste real de tipos, arreglado |
| Tabla de duplicados en mobile | **YA ESTABA ARREGLADO** — verificado |
| `.raw` que no era foto, archivo de 0 bytes | **RESUELTO** — al basurero |
| OCR de marca de agua | **NO ES TEMA DE CODIGO** — lo resolves a mano desde el curador |
| Material de Settimana 2025 | **RESUELTO** — la Settimana existio (4,7,8,9 jul 2025) pero su material nunca se bajo |
| Settimana 2023 y 2024 | **ARMADAS** — fechas verificadas contra el chat de la generacion, eventos agrupados |

Chequeo de salud final: 0 grupos repetidos, 0 pendientes sin copia buena,
0 pendientes con archivo faltante, 0 face_count desincronizado, 0 nombres
repetidos, 0 event_photos rotos. Las 11 paginas del curador responden HTTP 200.

---

## RESUMEN PARA LEER PRIMERO

**Arreglado esta noche, con backup y verificación (no hace falta que hagas nada):**
1. `_plausible()` rechazaba fotos JPEG sanas por un umbral (0.02 bytes/píxel) nunca medido → corregido a 0.0005 → **2,799 fotos recuperaron su dimensión real**.
2. `duplicados` volvió a tener filas repetidas (7 grupos, 13 filas) → arreglado, y encontré la causa de fondo: `importar_csv()` dedupea por texto de ruta en vez de por id, y 2,150 archivos de `Fotos_F_rescatadas\2024` tienen una carpeta de fecha duplicada real en disco.
3. `/api/persona/nombrar` no fusionaba personas con el mismo nombre (a diferencia de `/api/cara/asignar`, que sí) → arreglado, con test, y arreglé también el frontend para que no te deje en una página fantasma tras fusionar.
4. Texto desactualizado en `/marca-de-agua` (decía "por nombre de carpeta", en realidad es 100% OCR) → corregido.

**Actualización de la mañana:** pediste arreglar el #6718 → resuelto, ver
"CIERRE DEL EVENTO 6718" al final del documento.

**Necesita tu decisión — no la toqué:**
1. **El OCR de `/marca-de-agua`** (>8 caracteres = "tiene marca de agua") no distingue texto real de ruido: sólo ~54% de una muestra de 200 fotos marcadas parecen tener texto de verdad. Podrían ser ~690 falsos positivos de las 1,502.

**Menor, sólo anotado:** 372 de 222,718 rutas (0.17%) no existen en disco, mayoría de carpetas de Liga ya entregadas al cliente.

**Verificado limpio (revisé pero no encontré nada):** integridad estructural completa del árbol de eventos/`files`/`faces`/`persons`, los dos verificadores de integridad (RAW y JPEG), los umbrales de nitidez/brillo para "foto borrosa/oscura", fechas EXIF vs ruta en 66,383 fotos, `/api/persona/quitar`, paginación de Caras sueltas, y confirmación visual en pantalla de duplicados/dañadas/eventos/marca-de-agua/persona-486.

---

## Contexto de arranque
- Verificador `verificar_exactos.py --marcar` corriendo en background (bef441vck), ~53% al iniciar el loop. No se toca la tabla `duplicados` hasta que termine.
- Ya cubierto hoy con rigor: filas duplicadas en `duplicados`, `face_count`, rotación RAW/JPEG, layout mobile, integridad de cadena, EXACTO bit-rot, fechas falsas por mtime (evento 6718 y afines).

## Iteración 1 — integridad estructural del árbol de eventos

Chequeos read-only sobre `events`, `event_photos`, `files`, `faces`, `persons`:
ciclos en el árbol, huérfanos, hijos fuera del rango de fecha del padre,
padres con fotos directas, referencias rotas entre tablas.

**[DESCARTADO]** Todo limpio: 0 ciclos, 0 huérfanos, 0 hijos fuera de rango,
0 padres con fotos directas, 0 referencias rotas en faces/persons.

**[CONFIRMADO, menor]** 1 archivo `.raw` que no es foto (`bgm.pcm.raw` de un
backup de Nintendo 3DS) se coló en `files` por la extensión. Aislado — quedó
correctamente con `ancho=-1`, sin evento, sin daño. No hace falta tocar nada.

**[CONFIRMADO, menor]** 2 archivos de `Lightroom_extraido` con 0 bytes reales
en disco (escritura truncada durante la extracción). Ninguno está en un
evento, así que no generan miniatura rota visible. Bajo impacto.

**[CONFIRMADO — investigando alcance]** `_plausible()` en `web_curador.py`
(el filtro que descarta medidas "imposibles" de RAW dañados del rescate de
F: — declaran resoluciones absurdas en pocos MB) usa un umbral fijo de
`bytes/(ancho*alto) < 0.02`. Sobre una muestra de 20 JPEG marcados
`ancho=-1` ("no reintentar"), **19 abren y decodifican perfectamente** con
PIL — son fotos sanas de 24MP bien comprimidas (~0.015 bytes/píxel), por
debajo del umbral. El umbral está rechazando fotos reales, no solo RAW
corruptos. Afecta a 2,233 fotos que están en algún evento (sin ancho/alto
visible en la UI, y sin poder competir bien en el criterio "más píxeles" de
duplicados). Corriendo el chequeo completo sobre los 3,892 marcados
`ancho=-1` para medir el umbral correcto antes de tocar código — en curso.

**[ARREGLADO]** Confirmado a escala: **2,800 de 3,892 (72%) decodifican
perfecto** y quedaron sin medir por el umbral. Los 1,092 restantes sí están
dañados de verdad (no decodifican ni sin el filtro). El bpp mínimo real
medido: 0.0021 — 10x por debajo del umbral viejo (0.02). El caso que el
filtro debía frenar (DNG del rescate declarando 2 Gpx en 23 MB) da 0.0000119
— y ya lo frena `MAX_MPX` solo (300 Mpx), independiente del bpp. Cambié el
umbral a **0.0005** (con 4x de margen abajo del bpp real más bajo, 40x arriba
del caso dañado) en `_plausible()` (`web_curador.py:205`) **y** en la
limpieza duplicada de `rellenar_metadatos()` (`web_curador.py:265`, mismo
número hardcodeado por separado — si sólo arreglaba uno, el otro deshacía el
fix en el próximo arranque). Backup tomado, `test_plausible.py` con 5 casos
(incluye el DNG dañado real y una panorámica de 200MP real), todos pasan.
**Cerrado y verificado:** `remedir_plausible.py` (tarea bmqyx1gkc) terminó en
183s una vez que se destrabó el disco — **2,799 de 3,892 recuperados** con
dimensión real (casi exacto a la estimación de 2,800 sobre la muestra
completa). Quedan **1,093 realmente dañados** (no decodifican ni sin el
filtro — esos sí son daño de verdad, no falso rechazo). Verificado en la
base: 231,452 archivos vivos con dimensión válida ahora (antes 3,892 menos).
App sigue respondiendo bien (`/evento/2309` → HTTP 200).

**[DESCARTADO]** `verificar_raw.py` y `verificar_integridad.py` — revisados
por el mismo patrón (umbral sin medir). Están bien: los dos verifican por
señal real (decodificado forzado del sensor / marcador de fin `ffd9`), no
por heurística numérica. La cifra de 1,723 dañados de la sesión de hoy no
está en riesgo.

**[DESCARTADO]** `3_agrupacion_mejorado.py` parecía código sospechoso (no
lo había visto en toda la sesión) — es una versión vieja de Fase 3 (DBSCAN
global O(n²)) que `3_agrupacion.py` reemplazó explícitamente. Solo la
importa `test_smoke.py`. No es un bug, es código legado sin usar en
producción — candidato a archivar algún día, no urgente.

**[DESCARTADO]** `is_low_quality()` en `utils_analisis.py:550-559` — otro
umbral candidato a estar sin medir (`sharpness < 25.0` no tenía
justificación en el comentario, a diferencia de `brightness < 20` que sí la
tiene). Medido contra 20,000 fotos reales: el corte de sharpness cae en el
**percentil 1** (marca 0.9% de las fotos), y los de brightness en 0.46%
(oscuro) y 0.01% (sobreexpuesto). Los tres son conservadores de verdad, tal
como dice el comentario — no hay miscalibración. 97 eventos / 2,212 fotos
están nombrados "blurry unusable photo" en total, consistente con esa tasa.

## CIERRE: la pregunta original de esta noche ("¿los EXACTO se pueden
## borrar de verdad?") queda respondida y aplicada

**[ARREGLADO]** `verificar_exactos.py --marcar` (bef441vck) terminó tras
5,078s leyendo 976 GB. Resultado: **37,836 pares siguen byte a byte
idénticos** (seguros), **1,732 ya no lo son** (bit rot post-indexado) y
quedaron sacados a `REVISAR`/pendiente automáticamente. Verificado en vivo
en `/duplicados`: EXACTO bajó de ~30,782 a **29,057** marcados a descartar
(el número real y seguro), REVISAR muestra 1,822 pendientes + 6 ya
decididos aparte. Nada se borró todavía — sólo se corrigió la clasificación.

**[DESCARTADO]** Fotos CON EXIF de cámara (201,597) — ¿alguna tiene el reloj
de la cámara mal puesto, dando una fecha "confiable" pero falsa? Cruzado
contra ruta/nombre en las 66,383 que tienen alguna pista: **sólo 0.05%
contradicen**, y los 15 ejemplos revisados son todos ruido de frontera de
medianoche (WhatsApp nombra el archivo con la hora de *envío*, no de
captura — cruza medianoche y difiere un día del EXIF). No es reloj mal
puesto, es esperable. El EXIF es confiable donde existe.

**[ARREGLADO]** `/marca-de-agua` — el texto de la página decía "detectadas
por nombre de carpeta... no por análisis visual", pero el filtro por nombre
de carpeta ahora da **0 resultados** (`web_curador.py:1111-1116`): toda la
página (1,502 fotos) sale en realidad de OCR (Tesseract) sobre las carpetas
de trabajo. El texto quedó desactualizado desde que se agregó la parte de
OCR. Corregido en `templates/marca_de_agua.html` para describir lo que
hace de verdad. Sin cambio de datos, sólo copy — verificado en vivo.

**[PENDIENTE — necesita tu revisión, no lo toqué]** Mientras arreglaba el
texto de arriba, miré cómo decide `tiene_marca` (`buscar_marca_agua.py`):
más de 8 caracteres de texto detectado por OCR = "tiene marca de agua".
Ese umbral no distingue texto real de ruido de OCR sobre texturas/bordes
comunes en fotos de fútbol/eventos. Medido sobre una muestra de 200 de las
1,502 marcadas `tiene_marca=1`: **sólo 54% tienen texto que parece real**
(patrones tipo `.com`/© o 2+ palabras reconocibles). El resto es basura de
OCR como `'rc\n\nse se'`, `'Ww\n=\n-_\n<\nwa\nuw\nra'`, `'imam Qe\n\nsite'`.
Estimado: **~690 de las 1,502 fotos de esa página podrían estar mal
marcadas como "con marca de agua"** cuando en realidad no tienen nada.

No lo corregí porque no tengo verdad de referencia real (mi propio
heurístico de "parece texto real" es también una suposición, no algo
verificado) — cambiar `tiene_marca` a ciegas podría reemplazar un error por
otro, y esto afecta directamente qué mandás al basurero. El texto crudo de
OCR ya está guardado en `ocr_marca_agua.texto` para las 24,373 filas, así
que se puede revisar/recalcular sin volver a correr OCR (no hace falta
releer archivos). Dos caminos posibles para cuando decidas: (a) revisar a
mano una muestra de la página y confirmar la tasa real de falsos positivos,
o (b) mejorar el heurístico (exigir patrones de palabra real, no sólo
cantidad de caracteres) y aplicarlo sobre el texto ya guardado.

**[ARREGLADO + hallazgo de fondo importante]** Chequeo estructural de rutina
encontró que `duplicados` volvió a tener **7 grupos repetidos** (13 filas
sobrantes) — algo que debía estar en 0 desde el arreglo de ayer a la tarde.
Investigado a fondo, no es una regresión del fix de ayer:

- La causa es que `duplicados.csv` (26 MB, generado hoy 10:47am) tiene
  ~5.649 filas con id>375000 que se importaron recién esta noche (primera
  vez que algo pidió `/duplicados` después de que ese CSV quedara listo).
- De esas, 7 pares coinciden en `id_conservar`/`id_descartar` con filas
  viejas que ya existían — pero con el `ruta_conservar`/`ruta_descartar`
  en TEXTO ligeramente distinto (ej. `...\2024-07-03\_DSC0314...` vs
  `...\2024-07-03\2024-07-03\_DSC0314...`).
- `importar_csv()` (`web_curador.py:1230`) dedupea comparando el **texto**
  de la ruta, no el id numérico. Si `files.file_path` cambia para un
  archivo ya emparejado (una carpeta se renombra, se reorganiza, etc.), la
  próxima vez que se reimporte el CSV el dedup por texto no lo reconoce
  como el mismo par y lo vuelve a insertar — aunque `id_conservar`/
  `id_descartar` sean idénticos. **Este es probablemente el mecanismo real
  detrás del bug de 129.487 filas duplicadas de ayer**, no sólo "corrió
  varias veces antes de que existiera el guard" como se asumió entonces.
- Causa raíz de las rutas que cambian: encontré **2.150 archivos vivos**
  (todos bajo `Fotos_F_rescatadas\2024\...`) con una carpeta de fecha
  duplicada consecutiva en la ruta (`\2024-07-03\2024-07-03\`) — parece un
  artefacto real del reordenamiento del rescate de F:, no un error de la
  base (los archivos abren bien, la ruta doblada existe de verdad en
  disco). No lo toqué: reorganizar 2.150 archivos no es algo para decidir
  a las 2am, y no bloquea nada ahora mismo.

Arreglé sólo el síntoma inmediato: mismo método validado de ayer (backup,
dedup por `id_conservar+id_descartar+tipo` prefiriendo la fila ya decidida),
**0 grupos repetidos, 43.750 pares re-auditados con `verificar_copia_mejor`
→ 0 problemas**. La causa de fondo (dedup por texto en vez de por id) queda
para que decidas si vale la pena endurecerlo — no es urgente porque el CSV
ya no va a cambiar solo, pero podría repetirse si algún día se reorganizan
más carpetas de `Fotos_F_rescatadas`.

**[ARREGLADO]** `/api/persona/nombrar` (`web_curador.py:1040`) ponía el
nombre directo en `persons.name` sin chequear si otra persona ya lo tenía —
a diferencia de `/api/cara/asignar`, que sí fusiona por nombre. Resultado
posible (todavía no había pasado: 0 nombres repetidos en la base): nombrar
dos clusters de cara distintos con el mismo nombre real (ej. "María") deja
dos `persons` separadas con el mismo `name`, en vez de fusionarlas en una.
Corregido para fusionar igual que `asignar_cara()` — mueve las caras al id
existente, suma `face_count`, borra la persona vieja. Efecto secundario que
encontré al revisar el flujo completo: el JS de `persona_detalle.html` no
recargaba la página después de guardar, así que si la persona actual se
fusiona (y su id se borra), el usuario se quedaba viendo una página
fantasma y un refresh futuro daba 404 seco. Arreglado también: si el
`person_id` que vuelve la API es distinto del actual, redirige a la
fusionada. `test_nombrar_persona.py` con 4 casos (fusión real, sin
huérfanas, sin nombres repetidos, vaciar nombre no dispara fusión) — todos
pasan. Probado en vivo contra la app real (persona 447, nombre de prueba
puesto y revertido, sin dejar rastro).

**[CONFIRMADO, alcance chico]** Barrido completo de `files.file_path` vivos
contra el disco: **372 de 222,718 (0.17%) no existen** en la ruta guardada.
Mayoría en `Fotos_F_rescatadas` (284) y en carpetas de exportación de Liga
ya entregadas al cliente (`fotos\LigaExScuola\ExportacionFechas\...`, 86) —
probablemente reorganizadas después de la entrega. Lista completa en
`scripts_temp/rutas_huerfanas.csv`. Bajo impacto, no se tocó.

**[DESCARTADO]** `/api/persona/quitar` y la paginación de `/caras/sueltas`
revisadas a fondo — ambas sanas. `quitar` no tiene el problema que tenía
`nombrar` (no hay fusión posible al sacar, sólo decrementar). Paginación:
8.703 caras sueltas, 88 páginas, la última da exactamente 3 (8.703-8.700),
0 `photo_id` huérfanos, 0 `bbox_json` inválido sobre una muestra de 2.000.
Los 4 puntos donde se toca `persons.face_count` en todo el código (line
1060, 1078, 1159/1164/1166) quedan revisados y son simétricos entre sí —
cerrado el tipo de bug que causó el desincronizado de ayer.

**Checkpoint de salud general** (tras todos los arreglos de hoy y hoy a la
noche): 0 grupos repetidos en `duplicados`, 0 personas con `face_count`
desincronizado, 0 nombres de persona repetidos, 1.093 en `ancho=-1`
(exactamente los dañados reales, ni uno de más). App responde HTTP 200.

**Primera revisión VISUAL de la noche** (antes todo fue por datos/código):
- `/marca-de-agua`: el texto corregido se ve bien en pantalla. Noté 6/12
  miniaturas con "archivo no encontrado" en la primera pantalla — medido
  con precisión: son sólo **7 de 1.422 (0.5%)** en toda la página, es el
  mismo hallazgo ya anotado de rutas huérfanas, simplemente concentradas
  al principio del orden por id (ids más viejos = más probables de haber
  sido reorganizados). No es un hallazgo nuevo.
- `/persona/486` (las fotos de la boda que se veían de costado al empezar
  la noche): **las 8 se ven derechas ahora.** Confirmación visual del fix
  de rotación.

**[RECORDATORIO IMPORTANTE — no es hallazgo nuevo, es algo de ANTES del loop
que quedó sin resolver]** El evento **#6718** ("Santiago, CL - screenshot of
a computer screen", 9,017 fotos, 88 minutos, sin EXIF) que se diagnosticó
en detalle antes de arrancar el loop de esta noche — junto con el hallazgo
más amplio de **9,086 archivos con fecha de captura tomada del mtime en vez
de EXIF real** (recuperable: 2,049 con hora exacta del nombre de archivo,
8,728 con el día de la carpeta) — **sigue exactamente igual, sin corregir**.
Te había preguntado si arrancaba con la corrección de datos o el fix del
indexado, y en vez de responder pediste el loop nocturno — quedó una
pregunta abierta, no una que decidiste posponer a propósito. No lo toqué
esta noche porque es una reescritura grande (miles de archivos) que ya
estaba esperando tu aprobación explícita, y el criterio del loop es no
decidir solo en cosas así. Lo dejo bien visible acá para que no se pierda
entre todo lo demás.

**[DESCARTADO]** Revisión visual de `/duplicados`, `/danadas`, `/eventos` —
las tres se ven bien, números consistentes con todo lo arreglado hoy y
esta noche (EXACTO 37,856/29,057, dañadas 1,722 = 1,379+343, eventos 3,119
con el 6718 visible como recordatorio de arriba). Revisión de código de
`/api/eventos/agrupar` y sus helpers `_subir_fechas`/`_fechas_desde_hijos`
— bien implementado, protección contra ciclos, propagación de fechas a
ancestros correcta. Sin hallazgos.

## Cierre del loop nocturno

Tres chequeos seguidos sin nada nuevo (visual de duplicados/dañadas/eventos
+ revisión de código del merge de eventos), después de una noche larga con
~10 hallazgos reales (la mayoría arreglados con backup y test, dos dejados
para tu decisión por ser subjetivos/riesgosos). Cierro acá en vez de forzar
hallazgos artificiales — el archivo completo de esta noche queda en este
mismo documento para que lo repases con calma.

## Estado de las tareas en background (última revisión, ~03:35)
- Ambas tareas de esta noche: **terminadas**. Disco libre, sin nada
  compitiendo por I/O.

## CIERRE DEL EVENTO 6718 (a la mañana, a pedido explícito)

Backup: `photos_v2.db.backup-antes-fechas-mtime-20260805-083647`.

1. **Corregidas las fechas de 9,086 archivos** sin EXIF cuya `capture_date`
   venía del mtime (momento de copiado), no de la captura real — usando la
   hora exacta del nombre de archivo cuando estaba (467 casos) o el día de
   la carpeta conservando la hora vieja (8,619 casos). Verificado: el
   ejemplo `DSC04874.png` pasó de `2026-08-02` (fecha de extracción) a
   `2024-12-18` (fecha real de la carpeta).
2. Con las fechas ya reales, el evento 6718 quedó mostrando fotos de
   **193 días distintos** entre 2022 y 2026 — confirma que era un cluster
   falso armado sobre timestamps idénticos por casualidad, no un evento.
3. De las 9,060 fotos de los 5 eventos falsos (6718 + 4 más chicos:
   6530 "gym", 6524 "screenshot", 4909 "cafe", 6719 "parade" — ninguno
   nombrado ni confirmado por vos), sólo **5 caían dentro del rango de un
   evento hoja ya nombrado** (2 en "F6 - 18-10-2025", 2 en "Trabajo: sesión
   equipo ConSienteMente", 1 en "Viaje de estudio - Italia 2025") — esas se
   movieron ahí. Las 9,055 restantes son contenido disperso sin relación
   entre sí (mayoría fotos de teléfono de años distintos): volvieron al
   pool de "Sin evento"/"Teléfono", ahora con la fecha correcta.
4. Se borraron los 5 eventos falsos.

**Verificado en vivo:** `/evento/6718` da 404. El contador de eventos bajó
de 3,119 a 3,114 (exacto, los 5 borrados). "Fotos en eventos" bajó de
219,164 a 210,109 (los ~9,055 liberados). `files` sigue en 232,551 — ni una
foto se perdió, sólo cambió a qué evento pertenece cada una.



---

# CIERRE DE PENDIENTES — 2026-08-05 (tarde)

Backup: `photos_v2.db.backup-antes-cadenas-20260805-164147`.

Nota de infraestructura: el disco ya vive en la Pi (ver `RUNBOOK_NAS_PI.md`), y
la sesion de herramientas de Claude **no ve `K:`** (el `net use` vive en otra
sesion de logon; `EnableLinkedConnections` no cruza eso). Todo el trabajo contra
disco de hoy se hizo por SSH contra `/mnt/fotos` en la Pi, que ademas es mas
rapido que por SMB. Eso queda como el metodo para futuras verificaciones.

## 1. Los 3.292 pares EXACTO bloqueados por cadena — RESUELTO

**El problema:** ayer se borraron 25.765 duplicados EXACTO verificados byte a
byte, pero 3.292 quedaron afuera porque el archivo a descartar (X) era, a la
vez, la copia buena de otro par (B). Borrarlo dejaba a B sin nada que conservar.

**Lo que se investigo** (pedido: "revisa bien a que llevan por el otro lado, si
realmente son mejor, compara los 2 extremos y revisa si son copia"):

- La cadena es `P --EXACTO--> X --SIMILAR/RECOMPRIMIDO--> Q`.
- Los 5.046 pares B que dependian de X estaban **todos en 'pendiente'**: ninguno
  se habia aplicado, o sea que no habia daño hecho, solo riesgo futuro.
- **Los dos extremos (P vs Q):** P le gana a Q por lejos. En la muestra de 30,
  P es el RAW/original (24-25 MB) y Q una version chica (300-500 KB): P pesa
  entre 100% y 7.875% de Q. Ningun caso donde Q fuera mejor.
- **P y X son copia exacta:** sha256 identico en 30/30 de la muestra, y los
  6.584 archivos (P y X) existen en disco. Confirmado contra la Pi.

**El arreglo:** como P y X tienen exactamente los mismos bytes, P sirve igual de
bien para B. Se redirigieron los 5.046 pares B de X a P, y con eso X quedo libre
para borrar sin que nada perdiera su copia. Despues se borraron los 3.292 X:
**3.292 borrados, 0 errores, ~40 GB liberados**.

**Efecto secundario que el chequeo de salud detecto y se corrigio:** al
reapuntar B de X a P, 2.687 pares quedaron duplicados (ya existia un par (P,Q)
igual). Son el mismo par de verdad; se deduplico con el criterio de siempre
(conservar la fila ya decidida, si no la de menor id): 2.699 filas sobrantes
borradas, 0 grupos repetidos al final.

## 2. Hueco real en el verificador de seguridad — ARREGLADO

`verificar_copia_mejor.verificar()` chequeaba **una sola direccion**: que el
archivo que un par conserva siga vivo. No chequeaba la inversa: que el archivo
que un par quiere **borrar** no sea la copia buena de otro par. Ese es
exactamente el caso que se detecto a mano ayer, justo antes de borrar 392 GB.

Agregado a `verificar_copia_mejor.py` con su caso en
`test_verificar_copia_mejor.py`. Apenas se activo encontro **27 pares marcados
'descartar' que habrian borrado la copia buena de otro par** — todos con el
archivo todavia vivo, o sea que se llego a tiempo. Devueltos a 'pendiente'.

Los otros 582 que reporta son un caso benigno y ya protegido: pares 'movido'
cuyo descarte esta en `_duplicados_cuarentena` (recuperable) y del que depende
algun par sin resolver. `limpiar_cuarentena()` ya tiene la guarda
`SQL_NO_ES_CONSERVAR_PENDIENTE`, asi que no se los manda al basurero.

## 3. La cola de duplicados estaba llena de fantasmas — RESUELTO

De 130.844 pares "pendientes", **59.839 no tenian nada que decidir**: el archivo
a descartar ya no existia. La vista solo filtraba los que estaban en el basurero
(`borrado=1`), no los borrados de verdad.

- **46.579** eran los pares con `id_descartar` NULL del CSV viejo de los
  `_edited` borrados el 2026-08-03 (`importar_csv` ya no los deja entrar, pero
  estos habian quedado). Confirmado: 46.579/46.579 con `_edited` en el nombre y
  50/50 de la muestra sin existir en disco. Marcados `omitido`.
- **13.251** eran descartes ya borrados por otro par. Marcados `movido`.
- **838** no tenian copia buena viva (nada que conservar). Marcados `omitido`.

Ademas se agrego la guarda en la vista (`web_curador.py`, ruta `/duplicados`)
para que un par sin archivo no vuelva a aparecer como pendiente. Medido: la
consulta sigue en 0,19 s.

**Cola pendiente: 130.844 → 69.647 en la base, de los cuales 65.394 se
ven realmente en `/duplicados` (el resto lo tapa el guard: alguno de los dos
lados esta en el basurero).**

## 4. Filtro "Viajes" — ya no existia, pero aparecio otra cosa

El filtro muerto ya se habia sacado (no esta ni en `web_curador.py` ni en
`index.html`), y el campo "Viaje" separado tampoco esta en `evento.html`. La
columna `events.viaje` **sigue viva**: la escribe la Fase 3
(`3_agrupacion.py:450`) y la lee la Fase 4. **No se toco.**

Revisando eso aparecio un desajuste real: `4_exportar.py` esperaba los tipos
`viaje`/`vde`/`evento` pero el curador guarda `viaje_estudio`/
`evento_fotografico`/`familia`. Los que no coincidian caian a la carpeta
generica "Colecciones" en vez de la suya. Alineado, con
`test_tipos_coleccion.py` que compara el `<select>` del template contra el
diccionario de la exportacion para que no se vuelvan a separar.

## 5. Tabla de duplicados en mobile — ya estaba arreglado

`.tabla-scroll` esta definido en `base.html:105` y aplicado en
`duplicados.html:6`, con los divs balanceados. Verificado, sin cambios.

## 6. Los menores — cerrados

- **372 rutas huerfanas:** re-escaneadas contra el disco real (193.666 rutas
  chequeadas en la Pi) — siguen siendo exactamente 372, lo que confirma que los
  borrados de hoy fueron limpios (archivo y fila juntos). Sacadas de sus eventos
  y mandadas al basurero. Son restos de reorganizaciones: 249 de
  `Fotos_F_rescatadas/2024`, 86 de exportaciones de Liga ya entregadas al
  cliente, 35 de `Final ExScuola`, 2 sueltas.
- **El `.raw` que no era foto** (`bgm.pcm.raw`, de un backup de Nintendo 3DS) y
  **el archivo de 0 bytes** de Lightroom: al basurero.
- **4.713 caras apuntando a fotos del basurero:** la galeria de persona no
  filtraba por `borrado`, asi que se veian huecos. Arreglado con un JOIN en
  `persona_detalle`. `face_count` resincronizado (1.048 personas) y 393 personas
  que quedaron sin ninguna cara, borradas.
- **2.150 archivos con la carpeta de fecha duplicada** (`\2024-07-03\2024-07-03\`):
  **no se tocaron a proposito**. Los archivos abren bien y la ruta doblada existe
  de verdad en disco; renombrar 2.150 archivos es mas riesgo que beneficio, y la
  causa de fondo que importaba (el dedup por texto de `importar_csv`) ya no puede
  volver a meter filas repetidas.
- **2 filas `SIMILAR/conservar` con la copia buena inexistente:** se dejaron. Son
  falsos positivos de SIMILAR (un ARW de 2024 contra un jpg de Liga 2025), estan
  en un estado terminal que ningun codigo escribe ni lee, y el lado a descartar
  sigue vivo. Inertes.

## Verificacion final

`scripts_temp/salud_final.py` (nuevo, reusable):

```
grupos repetidos                 : 0
pendientes con archivo faltante  : 0
pendientes sin copia buena viva  : 0
face_count desincronizado        : 0
nombres de persona repetidos     : 0
event_photos apuntando a nada    : 0
event_photos de fotos borradas   : 0
files vivos                      : 193.292
```

Tests: `test_verificar_copia_mejor.py`, `test_import_duplicados.py`,
`test_tipos_coleccion.py`, `test_plausible.py`, `test_nombrar_persona.py`,
`test_orientacion.py` — todos pasan. (`test_import_duplicados.py` estaba roto
desde antes, sin relacion con esto: su fixture no creaba filas en `files` y la
guarda del 2026-08-03 descartaba todo. Arreglado el fixture.)

Smoke test en vivo: las 11 paginas principales responden HTTP 200, sin errores
en el log.

## Lo unico que queda

Ver "SETTIMANA" mas abajo: cerrado.

---

# SETTIMANA — cerrado 2026-08-05

Backup: `photos_v2.db.backup-antes-settimana-20260805-183456`.

Renzo aporto 5 eventos que reconocio visualmente y aclaro el formato: la
Settimana dura 3-5 dias de actividad deportiva todo el dia, **arranca un viernes
y sigue la semana siguiente**. Los habia encontrado filtrando por "grandes"
(800+ fotos), asi que podian faltar los chicos.

## Como se resolvio

Buscando por **ventana de fechas** en vez de por tamaño aparecieron los dias
completos. Y las fechas exactas se verificaron contra
`Desktop\Chat de WhatsApp con generación 25.txt` (el grupo se llamaba
literalmente "Generación 25 settimana"), que da prueba directa de que dia fue
que actividad.

## Settimana 2023 — evento **2063**, 4.564 fotos

| dia | evento | fotos | prueba en el chat |
|-----|--------|-------|-------------------|
| vie 11-ago | 4827 | 1.289 | "las q estaban en basket vengan a la cancha", "canchas de tenis" |
| mie 16-ago | 4837 | 1.779 | "fotos buenas de la presentacion y de la final de futbol femenino" |
| jue 17-ago | 4844 | 375 | "¿a que hora es baile a ciegas?" |
| vie 18-ago | 4848 + 4850 | 739 | "graben los bailes"; drive con "Scuola concert, Noche de reyes, Presentacion, Master chef, Final futbol fem" |

El concierto **si** es parte de la Settimana (aparece en el mismo drive que el
resto de las actividades, como "fotos de los ultimos dias"). El nombre viejo del
evento decia "9-13 ago 2023" — incorrecto, corregido.

## Settimana 2024 — evento **2122**, 13.662 fotos

| dia | evento | fotos | prueba en el chat |
|-----|--------|-------|-------------------|
| vie 28-jun | 5536 | 2.046 | "en 10 minutos parte futbol masculino en la cancha", "basket femenino en la multicancha" |
| lun 01-jul | 5547 | 2.889 | "a las 13.15 es la final de Volley y Catan", "traje creativo y la final de quemadas" |
| mar 02-jul | 5550 | 2.018 | "baile a ciegas", "tarde de reyes", "practicar el scuola concert" |
| mie 03-jul | 5556 | 2.649 | "Scuola concert ahora"; y a las 21:18 **"ha sido una de nuestras mejores Settimanas como generacion"** = cierre |
| (05-jul) | 5563 + 5564 | 319 | no es un dia de actividad: son fotos subidas despues ("ya se estan subiendo las fotos que faltaban de el martes y miercoles"). Sin EXIF, todas de `OneDrive_bajado\Importaciones`, fechadas por descarga |

La identificacion visual de Renzo (5556 = ultimo dia de una Settimana) quedo
confirmada por el mensaje de cierre de esa misma noche.

## Settimana 2025 — **existio, pero el material no esta en el archivo**

Fue **viernes 4, lunes 7, martes 8 y miercoles 9 de julio de 2025** — mismo
patron. Prueba en el chat: "POSTA VAYAN A LA CANCHA" (4-jul), "Ganamos
presentacion" (7-jul), aerobica y final de basket (8-jul), "Empatamos en primer
lugar en scuola concert" + baile a ciegas + tarde de reyes (9-jul). Era **"la
ultima settimana"** de la generacion.

En la base, esos dias tienen **~50 fotos, todas del telefono Xiaomi, ninguna de
camara**. Se barrio todo 2025 buscando dias con 150+ fotos de cualquier camara:
entre el 17-jun y el 27-ago no hay **ninguno** — julio 2025 esta vacio de camara.
Todo lo grande de ese año son fechas de Liga (sabados) y F1/F2/F3.

Conclusion: **no es un problema de indexado ni de fechas mal puestas — el
material nunca se bajo de la camara**. En el chat se ve que circulo por otras
vias ("aca estan las fotos de la camara de anoche", "manden todas las
fotos/videos que tengan de la settimanaaaa"), asi que si se quiere recuperar hay
que buscarlo fuera de este archivo: tarjeta SD, el drive del grupo, o pedirselo
a quien fotografio.

## Lo que se aplico

Los 11 eventos por dia quedaron colgando de su Settimana (`parent_id`), **sin
mover una sola foto** — se uso `/api/eventos/agrupar`, que ademas recalcula el
rango de fechas del padre. Los dos padres y los 11 hijos quedaron nombrados con
lo que paso ese dia y marcados como confirmados.

## Lo unico que queda por mirar

Los eventos **5563 y 5564** (319 fotos, 5-jul-2024): son de `OneDrive_bajado`
sin EXIF, asi que la fecha es la de descarga, no la de captura. Los colgue de la
Settimana 2024 porque el chat dice que ese dia se estaban subiendo las fotos del
martes y miercoles — pero conviene que Renzo los mire y confirme visualmente que
son de la Settimana y no de otra cosa.
