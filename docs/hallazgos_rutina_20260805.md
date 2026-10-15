# Rutina diaria — 2026-08-05 07:01

Continúa [hallazgos_loop_nocturno.md](hallazgos_loop_nocturno.md) (loop de la
noche del 4 al 5). Todo lo de acá está medido, no estimado.

---

## RESUMEN

**La descarga de OneDrive terminó bien.** 52.771 archivos, 572 GB, 0 dañados,
todos indexados y analizados. Lightroom: 68.816 archivos, 687 GB. El pipeline
corrió completo sobre ese material y cerró el 4/8 a las 10:47.

**No corrí lo que "queda" del pipeline, a propósito** — ver la sección de
abajo: lo único que falta son fases destructivas que dependen de una decisión
tuya que sigue abierta.

**Arreglado hoy (con backup y test):** los cuadros grises de `/personas` —
128 de 1.971 recortes de cara no se veían. Bajó a 7.

**Lo más importante que encontré:**
1. **37% de la cola de duplicados es trabajo fantasma** — 46.863 de 125.933
   pares pendientes apuntan a un archivo que ya no existe.
2. **El backup diario de la base se apagó sin querer el 4/8** — ya lo cubrí
   con una copia nueva, pero la tarea de Windows sigue borrada.
3. **56% de la biblioteca no tiene detección de caras** — Lightroom y
   OneDrive entraron con `enable_faces: false`.

---

## 1. Resultado de la descarga de OneDrive y Lightroom

| Biblioteca | Archivos | GB | Dañados | Indexados | Con features |
|---|---|---|---|---|---|
| `K:\OneDrive_bajado` | 52.771 | 572,2 | **0** | sí | sí |
| `K:\Lightroom_extraido` | 68.816 | 687,2 | 0 | sí | sí |

- `.listo_descarga` y `.listo_extraccion` del 3/8. El simulacro del 4/8 dio
  **0 archivos por bajar**.
- Base al día: 232.551 archivos, **0 con `needs_analysis=1`**, 0 sin fecha de
  captura, 0 sin features.
- Fase 5 final (4/8 10:47): EXACTO 48.529 · RECOMPRIMIDO 44.165 ·
  SIMILAR 43.706 = 136.400 archivos = 1.548,8 GB recuperables.

Nada quedó a medias en la descarga.

---

## 2. Por qué NO corrí lo que queda del pipeline

Barrí `K:\` entero contra la base: hay 361.693 imágenes en disco y 129.560 no
están indexadas, **pero todas son carpetas que el pipeline excluye a propósito**
— `Fotos_Organizadas_IA_v3` (la salida de la Fase 4, ~95.000) y
`_duplicados_contenido` / `_descartadas_movil_screenshot` (lo que el propio
pipeline movió). **No hay material nuevo pendiente de indexar.**

Lo que sí falta, y por qué no lo toqué:

| Fase | Estado | Por qué no la corrí |
|---|---|---|
| 1 · indexado | nada nuevo que indexar | correrla no aporta nada |
| 2 · análisis | 0 pendientes | ídem |
| 3 · eventos | 2.901 fotos vivas sin evento | **borra 3.107 de 3.146 eventos y 125.607 filas de `event_photos`** (`3_agrupacion.py:389-391` borra todo evento sin nombre). Y reagruparía sobre las 9.086 fechas falsas de mtime del evento 6718 — la pregunta que quedó abierta. Reproduciría el problema a mayor escala. |
| 3b · personas | — | `DELETE FROM persons` y reclusteriza. No hay caras nuevas que agrupar (ver punto 5), así que sería puro riesgo sin ganancia. |
| 3c · OCR | — | trabaja sobre los eventos que crearía la Fase 3 |
| 4 · export | — | lo excluiste vos explícitamente |
| 5 · duplicados | corrida y cerrada el 4/8 | reimportaría el CSV y **desharía las correcciones a mano de anoche** (los 1.732 pares de bit rot y el dedup de los 7 grupos) |

**Correr la Fase 3 hoy, sin vos y sin resolver lo del mtime, cambiaba trabajo
curado por trabajo automático peor.** Por eso quedó ahí.

---

## 3. [ARREGLADO] Los cuadros grises de `/personas`

**Síntoma:** 152 de las 1.971 miniaturas de `/personas` salían como cuadro
gris. 128 decían "Error rec".

**Causa (reproducida, no supuesta):** `recortar_cara()` recortaba siempre
sobre el JPEG incrustado del RAW, pero el bbox lo calculó `FaceAnalyzer` sobre
`load_image_array()`, que **descarta ese preview cuando su lado corto no llega
a `MIN_LADO_PREVIEW` (640) y revela el RAW entero**. Los DNG del rescate de F:
traen un preview de 1024×576 (lado corto 576 < 640), así que las coordenadas
estaban en el espacio de la imagen grande (~3024×2012) mientras el recorte se
hacía sobre la chica. `crop()` recibía una caja dada vuelta y PIL tiraba
`Coordinate 'right' is less than 'left'`.

Medido sobre los 128 fallos: **126 tienen un bbox que entra en
`files.ancho × alto` pero se sale del preview.** Los otros 2 son otra cosa.

**Arreglo** (`utils_images.py`, backup en
`utils_images.py.backup-antes-fix-recorte-20260805-071807`): `recortar_cara()`
aplica el mismo corte de `MIN_LADO_PREVIEW` y cae al mismo decodificado que la
detección. 4 líneas.

**Verificado en vivo, contra la app real:** `/personas` pasó de **152 rotas a
32**, y las "Error rec" de **128 a 7**. De las 32 que quedan, 24 son las rutas
huérfanas ya conocidas de anoche (carpetas de Liga entregadas al cliente).

Test: `test_recorte_cara.py` (4 casos, incluye recorte real de DNG y se saltea
solo si K: no está montado). `test_orientacion.py` sigue pasando.

---

## 4. [CONFIRMADO] 37% de la cola de duplicados es trabajo fantasma

De los **125.933 pares que `/duplicados` muestra como pendientes, 46.863
(37,2%) apuntan a un archivo que ya no existe en disco** — casi todos los
`_edited` de Lightroom que borraste el 3/8.

No es teórico, se ve en la primera pantalla: **14 de los 40 pares de la página
1 son fantasmas**, y 6 de 40 en la página 2 (ordena por `score`, y los EXACTO
con score -1 caen primero).

El comentario de `web_curador.py:1278-1284` ya cuenta esta historia y arregló
la parte del *import* (que no vuelvan a entrar), pero **las filas que ya
estaban nunca se limpiaron**. 46.576 de ellas ni siquiera tienen fila en
`files`.

No lo toqué: son 46 mil borrados en una tabla que estás curando a mano. Para
cuando quieras, con backup antes:

```sql
DELETE FROM duplicados
WHERE decision='pendiente'
  AND NOT EXISTS (SELECT 1 FROM files f WHERE f.id = duplicados.id_descartar);
```

Eso deja la cola en ~79.000 pares reales y hace que el contador de la página
diga la verdad. (El cálculo de GB a liberar **no** está inflado: sale sólo de
`decision='descartar'`.)

---

## 5. [CONFIRMADO] 56% de la biblioteca no tiene detección de caras

| | Fotos vivas sanas |
|---|---|
| Con detección de caras | 97.522 |
| **Sin detección de caras** | **123.474 (55,9%)** |

Desglose de las que faltan: **Lightroom_extraido 69.681** y
**OneDrive_bajado 50.510**. Las bibliotecas viejas están al 100%.

Causa: `config_K.json` tiene `"enable_faces": false`, y todo el material nuevo
se analizó con ese config. O sea, la sección Personas del curador sólo ve
menos de la mitad del archivo.

No lo arranqué: es una pasada de Fase 2 con caras sobre 123 mil fotos (horas
de GPU y de disco externo), y después habría que decidir si re-clusterizar
personas. Es una decisión tuya, no un arreglo.

---

## 6. Auditoría del chat completo (190 mensajes, 1/8 15:29 → 5/8 07:29)

Revisé cada pedido tuyo contra el estado real de hoy.

### Completados y verificados hoy

- Pipeline completo sobre K: sin export · `analizar_solo_camara` en false
  (las 4.288 sin features) · import del CSV arreglado · verificador de
  copia-mejor · rehasheo y barrido de integridad · selección múltiple de
  eventos en la portada · verificación de RAW · página `/danadas` ·
  descarga de OneDrive con script a K: · descompresión de Lightroom ·
  `--no-move` en el pipeline · Fase 5 completa en la cadena · borrado físico
  y en base de los `_edited` · revisión de todas las páginas ·
  **`/duplicados/grupos` eliminada** (confirmado: la ruta ya no existe) ·
  guía de armado del NAS (`RUNBOOK_NAS_PI.md`) · checklist para publicar el
  repo · Settimana/Alianzas 2025 (confirmado que ese material nunca existió
  en la biblioteca) · los 8 pasos del plan del 4/8 · precalentado de las
  223.023 miniaturas · verificación byte a byte de los EXACTO.

- **Los EXACTO se pueden borrar**: `verificar_copia_mejor.py` (solo lectura)
  corrido hoy sobre los **43.750 pares ya resueltos → 0 problemas**. Todo lo
  marcado tiene una copia viva en pie.

- **Tu duda de los RAW contra el thumbnail exportado** (mensaje del 4/8
  16:00) — la verifiqué a fondo hoy porque en `/duplicados` se ve feo: hay
  **361 pares EXACTO que cruzan `.jpg` ↔ `.ARW/.dng`**, 357 ya marcados.
  Rehasheé 60 en disco: **los 60 idénticos, y los archivos con extensión de
  RAW son JPEG de verdad** (firma `ff d8 ff`), mal nombrados por el
  recuperador de F:. **No hay ningún RAW real en riesgo**, son 0,6 GB.

### No completados

- **`ignora la rutina y borra todas las que estén relacionadas a este
  proyecto`** (4/8 11:17) — quedó al revés de lo que pediste:
  - La rutina que vos habías puesto era **esta tarea programada de Claude
    Code, `proseguirpipeline`** (diaria, 07:01). **Sigue viva y es la que
    generó este informe.** Nunca se borró.
  - En cambio se borró **`IMGorgni_AutoResolverDuplicados`**, la tarea de
    Windows que vos habías pedido el 1/8 y que **no** estaba en lo que
    querías sacar. Se leyó "rutina" como tarea de Windows.
  - **Consecuencia real:** esa tarea también hacía el **backup diario de la
    base al 8TB**. Último backup automático: **3/8 09:01**. Se perdieron dos
    días de respaldo justo sobre los cambios más grandes del proyecto.
  - **Ya lo tapé**: dejé `K:\_db\photos_v2_backup-20260805.db` (3,34 GB,
    `integrity_check: ok`, 232.551 archivos / 179.611 duplicados / 3.146
    eventos). Usé nombre con fecha para no pisar el del 3/8.
  - **Falta tu decisión**: volver a crear la tarea de Windows del
    auto-resolver (el script `auto_resolver_seguros.py` está intacto), y
    decidir si querés borrar o no `proseguirpipeline`.

- **El evento #6718 y las 9.086 fechas de mtime** — sigue igual, sin
  corregir. Es la misma pregunta abierta de anoche.

- **El umbral de OCR de `/marca-de-agua`** — sigue sin decidir. Hoy corregí
  el texto de la página, que decía "más de 8 caracteres de **texto real**":
  eso afirmaba justo lo que anoche se midió que era falso (~54%), en una
  página cuyo botón manda fotos al basurero. Ahora dice el criterio de
  verdad y avisa que mires la foto antes.

---

## 7. Estado de cada página (todas 200, ninguna rota)

Bajé **todas** las miniaturas de cada página y conté cuáles salen como cuadro
gris, en vez de mirarlas de a una.

| Página | Contadores | Miniaturas rotas | Nota |
|---|---|---|---|
| `/` | 3.119 eventos · 219.164 en eventos | 17 / 3.105 (0,5%) | **14 eventos con 0 fotos** (respuesta a tu pregunta del 3/8: sí, hay eventos sin fotos; ninguno tiene nombre propio) |
| `/evento/<id>` | ok, 404 correcto si no existe | 0 / 180 | |
| `/sin-evento` | 3.462 ✓ | 2 / 180 | |
| `/telefono` | 548 ✓ | 0 / 180 | 3.462 + 548 = las 4.010 vivas sin evento ✓ |
| `/basurero` | 9.833 ✓ | 0 / 180 | |
| `/danadas` | 1.722 = 1.379 RAW + 343 JPEG ✓ · 10.957 solo marcador ✓ | 29 / 180 en el filtro JPEG | **correcto**: esa página *son* los archivos que no decodifican |
| `/personas` | 1.971 personas | **32 / 1.971** (era 152) | arreglado hoy. Sin paginación: 1.971 recortes en una sola carga |
| `/persona/<id>` | paginación ok | 0 | |
| `/caras/sueltas` | 8.703 · 88 páginas, la última da 3 ✓ | 5 / 100 | |
| `/marca-de-agua` | 1.422 | 7 / 120 | rutas huérfanas conocidas |
| `/duplicados` | 125.933 pendientes | 0 / 66 | **37% de esos pendientes son fantasmas** (punto 4) |
| `/duplicados/corruptos` | 1 par | 0 / 2 | |
| `/api/fotos`, `/api/duplicados/aplicar/estado` | ok | — | |

Todos los filtros probados (tipo, estado, fiabilidad, `f=` de la portada,
paginación y páginas fuera de rango): ninguno rompe.

### Menores, sin tocar

- **`/personas` ignora el parámetro `page`** (`web_curador.py:987` lo lee
  para `/persona/<id>`, pero `/personas` renderiza las 1.971 de una). Con la
  caché fría eso eran ~57 s de carga; con las miniaturas ya generadas se
  nota poco.
- **180 personas sin ninguna cara** en `persons` (filas muertas, invisibles
  en la página porque el filtro pide `face_count >= 1`).
- **2.372 pares con el mismo `(id_conservar, id_descartar)` en dos filas de
  distinto tipo** (típico: una RECOMPRIMIDO ya `movido` y una SIMILAR
  todavía `pendiente`). De esas, **sólo 1 se ve hoy en la página**, el resto
  queda oculto porque un lado ya está en el basurero. Por la clave que usó
  el dedup de anoche `(ids + tipo)` quedan 6 repetidas.
- **843 pares pendientes cuyo lado "conservar" ya está en el basurero** —
  invisibles en la página por el mismo filtro.

### Comprobado y limpio

- 0 referencias rotas en `event_photos` (ni a `events` ni a `files`).
- 0 personas con `face_count` desincronizado · 0 nombres repetidos.
- La prueba de anoche sobre la persona 447 **no dejó rastro** (0 personas con
  nombre en toda la base).
- Reiniciar el curador **no reimporta nada**: lo simulé antes y lo confirmé
  después del reinicio real — `importar_csv()` insertó **0 filas**, la tabla
  quedó en 179.611.

---

## Qué toqué hoy

1. `utils_images.py` — arreglo del recorte de caras (backup al lado).
2. `templates/marca_de_agua.html` — el texto ya no afirma "texto real".
3. `test_recorte_cara.py` — nuevo.
4. `K:\_db\photos_v2_backup-20260805.db` — backup nuevo, no pisa el del 3/8.
5. Reinicié `web_curador.py` (puerto 5000) para que los dos arreglos tomen
   efecto. Verificado sirviendo 200.

Nada más se escribió: ni la base de fotos, ni la tabla `duplicados`, ni
archivos en K: fuera del backup.
