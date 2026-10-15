# Spec — completar la galeria de clientes (galeria.py)

## 0. Restricciones — leelas antes de escribir una linea

- **Editas UN SOLO archivo: `galeria.py`** del directorio actual. No crees archivos
  nuevos, no toques ningun otro `.py`, `.md`, `.json` ni `.db` del proyecto.
- **Prohibido tocar `photos_v2.db` y sus backups** (`*.db-backup*`, `respaldos/`).
  Es 3,5 GB y es la fuente de verdad de eventos, caras y tags. Ni leerla hace falta.
- **Prohibido conectarte por SSH, red o a la Raspberry Pi.** Trabajas solo sobre la
  copia local. El despliegue lo hace otro, despues.
- **Prohibido tocar `/mnt/fotos`, `K:` o cualquier ruta de fotos reales.** El
  self-check corre entero sobre directorios temporales.
- **Prohibido agregar dependencias.** Solo stdlib + Flask + Pillow, que es lo que el
  archivo ya importa. Nada de `pip install`. `zipfile`, `csv`, `hashlib` y `secrets`
  son stdlib y ya los tenes.
- **Prohibido romper galerias existentes.** Hay una `galeria.db` en produccion con
  datos reales. Toda columna nueva se agrega con el patron idempotente que ya usa
  `conectar()` (mirar como agrego `raw`), acepta NULL, y una galeria vieja sin ese
  valor tiene que seguir funcionando igual que hoy.
- Si algo te obliga a violar una de estas restricciones, **no lo hagas y reportalo**
  en la salida.

## 1. Contexto

`galeria.py` es un reemplazo casero de Pixieset que corre en una Raspberry Pi 4 de
**2 GB de RAM**, detras de Caddy, servido por el server de desarrollo de Flask en
`127.0.0.1:8000`. Ya funciona y sirve clientes reales.

Lo que ya hace y **no hay que rehacer**: galerias con slug secreto, gate por email,
previews de 1000px con marca de agua cacheadas, favoritos por cliente (`megusta`),
descarga de original y de RAW, panel admin en `/panel*` (protegido por basic_auth de
Caddy, no por codigo), paginado, y un `test()` con asserts que ya cubre fugas entre
galerias.

Los 2 GB de RAM son una restriccion de diseño real, no un detalle: nada de cargar
archivos enteros en memoria.

## 2. Que hacer — seis features

### 2.1 Contraseña opcional por galeria

Hoy `entrar()` acepta cualquier email y deja pasar. El link secreto es la unica
defensa.

1. Agregar columna `clave_hash TEXT` a `galeria` (idempotente, acepta NULL).
2. Si `clave_hash` es NULL, el comportamiento es **exactamente el de hoy** (solo email).
3. Si tiene valor, el formulario del GATE pide ademas contraseña, y `entrar()` rechaza
   si no coincide: vuelve al gate con un mensaje de error, sin crear el cliente ni la
   sesion.
4. Hash con `hashlib.scrypt` (stdlib) y sal aleatoria de `secrets.token_bytes(16)`.
   Guardar como `scrypt$<sal_hex>$<hash_hex>`. Comparar con
   `secrets.compare_digest`, nunca con `==`.
5. En el panel de la galeria: un formulario POST `/panel/<slug>/clave` que fija la
   contraseña, y la deja en NULL si el campo llega vacio.

### 2.2 Lightbox

En la grilla, click en una foto abre la foto grande sin recargar la pagina.

1. Usar el elemento `<dialog>` nativo del navegador y JavaScript a mano. **Nada de
   librerias ni frameworks JS.**
2. Navegacion: flechas izquierda/derecha del teclado y dos zonas clickeables a los
   costados. `Escape` cierra (lo hace `<dialog>` solo).
3. Dentro del lightbox va el mismo boton de me-gusta que en la grilla, y tiene que
   quedar sincronizado con el corazon de la celda de atras al marcar/desmarcar.
4. Solo navega entre las fotos de la pagina actual. No inventes precarga de la pagina
   siguiente: no la necesitamos.
5. Sirve la misma URL `/p/<id>` que ya usa la grilla — no agregues un tamaño nuevo.

### 2.3 Descarga en ZIP

Dos rutas nuevas: `/g/<slug>/zip` (todas) y `/g/<slug>/zip/elegidas` (solo los
me-gusta del cliente de la sesion).

1. Las dos exigen que el cliente haya entrado a ESA galeria, con el mismo criterio
   que usa `foto_autorizada()`. Un cliente de otra galeria tiene que recibir 404.
2. **Streaming obligatorio.** Con 2 GB de RAM no se arma el zip en memoria ni en un
   archivo temporal. Usar `zipfile.ZipFile` sobre un objeto tipo-archivo propio cuyo
   `seekable()` devuelva `False` — asi zipfile escribe en modo streaming — e ir
   sacando los bytes desde un generador que Flask consume con
   `Response(gen, mimetype="application/zip")`.
3. **`ZIP_STORED`, sin compresion.** Son JPEG: comprimir gasta CPU de la Pi para
   ahorrar ~0%.
4. Copiar cada foto en trozos de 1 MB (`shutil.copyfileobj` con `length=1024*1024`).
   Nunca `read()` de un archivo entero.
5. Se empaqueta el **original limpio**, no el preview marcado — es la entrega.
6. Nombre del archivo: `<slug>.zip` y `<slug>-elegidas.zip`.
7. Si una foto no existe en disco, se saltea y el zip sigue. Una foto rota no puede
   tumbar la descarga entera.
8. En la grilla: un boton "Descargar todo" y, cuando el cliente tiene al menos una
   marcada, "Descargar mis elegidas (N)".

### 2.4 Portada

1. Agregar columna `portada_foto_id INTEGER` a `galeria` (idempotente, NULL).
2. El GATE (la pantalla previa a entrar) muestra esa foto de fondo, oscurecida con un
   degradado para que el texto se lea, con el nombre de la galeria encima. Si es NULL,
   se ve igual que hoy.
3. La portada se sirve **sin exigir sesion** — es la tapa, se ve antes de entrar. Es
   el preview marcado al agua de `/p/<id>`, nunca el original. Para eso agregar una
   ruta aparte `/portada/<slug>` que no pase por `foto_autorizada()`; **no relajes
   `/p/<id>`**, que sigue exigiendo sesion.
4. En el panel: elegir la portada entre las fotos de la galeria.

### 2.5 Expiracion

1. Agregar columna `expira TEXT` a `galeria` (idempotente, NULL = no expira nunca).
   Fecha ISO `YYYY-MM-DD`.
2. Vencida la fecha, `/g/<slug>`, `/p/<id>`, `/d/<id>`, `/portada/<slug>` y las dos
   rutas de zip dejan de servir. `/g/<slug>` muestra una pagina que dice que la
   galeria expiro; el resto responde 404.
3. Comparar en UTC, con el mismo criterio que ya usa el archivo
   (`datetime.now(timezone.utc)`). La galeria vence al **terminar** el dia indicado.
4. En el panel: campo para fijar o borrar la fecha, y que la lista de galerias muestre
   cuales estan vencidas.

### 2.6 Exportar la seleccion

1. Ruta `GET /panel/<slug>/seleccion.csv` que devuelve un CSV descargable.
2. Columnas exactas, en este orden y con este encabezado:
   `ruta,nombre,votos,emails`
   - `ruta`: la ruta completa como esta guardada en `foto.ruta`
   - `nombre`: solo el nombre del archivo
   - `votos`: cuantos clientes la marcaron
   - `emails`: los emails que la marcaron, separados por `;`
3. Ordenado por `votos` descendente, y a igual cantidad por `foto.orden`.
4. Generarlo con el modulo `csv` de stdlib, no concatenando strings a mano (los
   emails y las rutas pueden traer comas).
5. Link visible en el panel de la galeria.

## 3. Como hacerlo — estilo del archivo

- Los templates son strings a nivel de modulo (`GRILLA`, `GATE`, `PANEL_GAL`, ...).
  Seguí ese patron, no crees una carpeta `templates/`.
- Comentarios **en español y sin tildes**, como todo el archivo.
- Donde tomes un atajo a proposito, marcalo con un comentario `# ponytail:` que diga
  cual es el techo del atajo. El archivo ya usa esa convencion.
- CSS: sumalo al bloque `<style>` que ya existe en `BASE_HTML`. No agregues hojas
  aparte.
- No reformatees ni reordenes lo que ya esta. El diff tiene que ser lo mas chico
  posible: solo lo que estas agregando.

## 4. Entregable exacto

1. **`galeria.py` modificado en el directorio actual.** Nada mas.
2. **`python galeria.py test` tiene que pasar e imprimir `ok`.** Es la condicion de
   aceptacion: si no pasa, la tarea no esta lista. Corrélo vos mismo hasta que pase.
3. Ampliar `test()` con asserts nuevos para lo que agregaste. Como minimo estos seis:
   - galeria con clave: entrar con la contraseña equivocada **no** crea sesion, y con
     la correcta si
   - galeria sin clave: sigue entrando solo con email, igual que hoy
   - galeria expirada: `/g/<slug>` no muestra fotos, y `/p/`, `/d/` y `/zip` dan 404
   - `/g/<slug>/zip` de una galeria en la que no entraste da 404
   - el zip generado se abre con `zipfile.ZipFile` y trae la cantidad de fotos
     esperada (armalo con JPEG de verdad hechos con Pillow en un directorio temporal)
   - el CSV de seleccion sale con el encabezado exacto y escapa bien una ruta con coma
4. Al terminar, imprimí un resumen de una linea por feature diciendo si quedo hecha o
   no, y cualquier restriccion de la seccion 0 que hayas tenido que esquivar.
