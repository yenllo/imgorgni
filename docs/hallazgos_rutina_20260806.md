# Rutina del 6/8/2026

Revisión del chat anterior (`local_4390d6a5`, 267 mensajes tuyos entre el 1/8 y el 6/8
05:00), estado de la descarga de OneDrive, decisión sobre el pipeline y repaso de las
páginas del curador.

---

## 1. El disco no está montado — es lo que condiciona todo lo demás

`K:` no está mapeado. El `net use` pide la contraseña de Samba y vos no estabas para
escribirla, así que **no la monté**.

Lo que sí verifiqué por SSH (`ssh fotosnas-lan`, la LAN; el alias por Tailscale no
responde ahora):

| | |
|---|---|
| Pi | arriba, 20 h de uptime |
| Disco | `/dev/sda2` montado en `/mnt/fotos`, 7,3 T · 3,6 T usados (50%) |
| Samba | puerto 445 respondiendo |
| USB | sigue en **480 Mbps** — el cable nuevo no llegó todavía |

O sea: la Pi está sana y esperando. Falta el paso tuyo de mapear `K:`.

---

## 2. La descarga de OneDrive: terminada y sana

| | archivos vivos | espacio |
|---|---|---|
| OneDrive | 47.930 | 572,6 GB |
| Lightroom | 61.099 | 589,9 GB |
| **Total biblioteca** | **192.981** | **2.073,7 GB** |

Salud de la base, toda verificada hoy:

- `needs_analysis = 1` → **0**
- sin hash SHA256 → **0**
- sin fecha de captura → **0**
- sin features → **0**
- filas huérfanas en `photo_features` / `event_photos` → **0**

No quedó nada a medio procesar de la descarga.

---

## 3. No corrí el pipeline, y esta vez por dos motivos

**El primero es físico:** las fases 1, 2, 3b, 4 y 5 leen o escriben archivos en `K:`.
Sin el disco montado no hay nada que correr — no es una decisión, es que no se puede.

**El segundo sigue siendo el de siempre, y conviene que lo tengas presente para cuando
montes el disco:** la Fase 3 (`3_agrupacion.py:389-391`) arranca con

```sql
DELETE FROM event_photos WHERE event_id IN (SELECT id FROM events WHERE name IS NULL)
DELETE FROM events       WHERE name IS NULL
```

Con los números de hoy eso es **borrar 3.082 de 3.142 eventos y 70.644 filas de
`event_photos`**. Sobreviven sólo los 60 que tienen nombre escrito a mano. Ninguno de
los que borra está marcado como confirmado, pero igual te llevás por delante todo el
agrupamiento actual para rehacerlo automáticamente.

**No la corras sin querer.** Si algún día querés reagrupar de verdad, hacelo con backup
y sabiendo que perdés ese trabajo.

---

## 4. Las páginas del curador: todas bien

Levanté `web_curador.py` y las recorrí una por una. Todas responden 200 y los números
que muestran cuadran con la base:

| Página | Muestra | Cuadra |
|---|---|---|
| `/` | 3.104 eventos raíz · 180.798 fotos en eventos | ✔ |
| `/evento/<id>` | grilla + Autocull + guardar | ✔ |
| `/sin-evento` | 4.035 de cámara | ✔ (4.035 + 8.148 = 12.183 sin evento) |
| `/telefono` | 8.148 de teléfono | ✔ |
| `/basurero` | 10.513 | ✔ |
| `/danadas` | 1.011 con daño real | ✔ (los otros 4.055 son `danado=2`) |
| `/personas` | 1.758 personas · 32.232 caras | ✔ |
| `/caras/sueltas` | 5.931 sin agrupar | ✔ |
| `/marca-de-agua` | 1.415 (filtro >8 caracteres, de 24.373 con OCR) | ✔ |
| `/duplicados` | 69.647 pendientes | ✔ (18.812+40.947+1.813+8.075) |
| `/duplicados/corruptos` | 0 para revisar | ✔ — los 11 que quedan tienen un lado ya en el basurero |

Dos cosas que revisé porque parecían fallas y **no lo son**:

- `/duplicados/corruptos` dice 0 aunque hay 343 pares marcados: 330 están omitidos y los
  11 pendientes tienen un lado en el basurero, que el filtro descarta a propósito.
- `/?pagina=2` devuelve lo mismo que `/`: el índice no pagina, muestra todo de una.

**La cola de duplicados fantasma quedó limpia.** Los 46.863 que apuntaban a archivos
borrados ya no están pendientes: de 125.933 pasó a 69.647.

### Lo único que encontré para que decidas: 27 eventos vacíos

27 eventos raíz aparecen en el índice con «0 fotos». No están rotos, pero ensucian: cada
uno figura además en el desplegable «colgar de» de todas las páginas de evento. Varios
son sombras de eventos reales, creados al mismo minuto:

| Vacío | Al lado del real |
|---|---|
| 6708 «playing soccer» 20/06 19:03 | 2413 «F7 - 20-06-2026» · 2.679 fotos |
| 6707 «playing soccer» 13/06 19:02 | 2410 «F6 - 13-06-2026» · 2.754 fotos |
| 6706 «playing soccer» 06/06 19:04 | 2406 «F5 - 06-06-2026» · 2.509 fotos |
| 6704 «playing soccer» 30/05 19:14 | 2402 «F4 - 30-05-2026» · 2.844 fotos |
| 6702 «sports game» / 6701 «dancing» 22/05 16:12 | 1800 «Fotos Martina» · 231 fotos |

**No los borré**, porque no puedo distinguir a ojo cuáles son basura y cuáles creaste vos
a mano para llenar después (6085 se llama sólo «Evento», que es lo que pone el curador
cuando creás uno). Si querés limpiarlos, esto los saca sin tocar ninguna foto:

```sql
DELETE FROM events
 WHERE id NOT IN (SELECT DISTINCT event_id FROM event_photos)
   AND id NOT IN (SELECT parent_id FROM events WHERE parent_id IS NOT NULL);
```

---

## 5. Repaso de tus pedidos del chat anterior

Del 5/8 en adelante, que es lo que quedaba abierto:

| Pedido | Estado |
|---|---|
| Evento 6718 sin sentido → arreglarlo | ✔ ya no existe, se fusionó |
| Settimana: unificar 5550, 4827, 4837, 2122 | ✔ con nombres propios y colgados de sus padres (2122 para 2024, 2063 para 2023) |
| Borrar los duplicados exactos seguros | ✔ corrió `borrar_exacto_seguro.py`; 10.513 quedaron en el basurero |
| Recortes de cara grises en `/personas` | ✔ arreglado, `test_recorte_cara.py` pasa |
| Visor tipo galería | ✔ `/vista/<id>` a 1600 px, con flechas |
| Integrar Autocull por evento | ✔ botón, avance en vivo y filtros por estrellas (en F2: 20 de 5★, 62 de 4★, 123 de 3★) |
| Dejar en CLAUDE.md lo de pedir el dock | ✔ con la tabla de velocidades |
| SSH sin contraseña + IP de Tailscale en CLAUDE.md | ✔ |
| Actualizar el runbook del NAS | ✔ |
| **Botón de descarga (original/RAW + ZIP)** | ✘ **estaba sin hacer — lo hice hoy, ver abajo** |

### Sobre las rutinas programadas

El 4/8 pediste *«borra todas las que estén relacionadas a este proyecto»*. Eso quedó al
revés y sigue así:

- **`proseguirpipeline` — esta misma rutina — sigue viva.** Es la que querías sacar.
- **`IMGorgni_AutoResolverDuplicados` se borró de más**, y con ella se fue el **backup
  diario de la base**.

No toqué ninguna de las dos: borrar la rutina que me está ejecutando y recrear una tarea
de Windows son cambios de configuración que preferí dejarte a vos. El backup más nuevo
de `photos_v2.db` es del **5/8 18:35** y la base se modificó el 5/8 21:36, así que hay
un día sin respaldo.

---

## 6. Lo que hice: el botón de descarga

Era el único pedido tuyo sin cumplir. Quedó así:

**Una foto suelta** — en el visor, al lado de «ver original», ahora hay «⬇ descargar».
Baja el **RAW si la foto lo tiene** (busca el hermano por carpeta + nombre; hay 2.455
pares RAW/JPEG en la biblioteca) y si no, el archivo original tal cual.

**Varias** — con fotos seleccionadas aparece «⬇ Descargar» en la barra, en las seis
pantallas que tienen grilla (evento, sin-evento, teléfono, dañadas, basurero, marca de
agua). Arma un ZIP con el nombre pedido:

```
F2 - 09-05-2026 (4 fotos, 5 estrellas).zip
F2 - 09-05-2026 (7 fotos, 4 y 5 estrellas).zip
F2 - 09-05-2026 (9 fotos, 3 a 5 estrellas).zip
```

Nombre del evento, cantidad y las estrellas de Autocull. Los tres salieron de correr la
descarga de verdad contra la base, no son ejemplos inventados.

Detalles que vas a agradecer:

- Dos fotos con el mismo nombre en carpetas distintas no se pisan: la segunda entra como
  `DSC09851 (2).JPG`.
- Si un archivo no se puede leer, el ZIP se arma igual con el resto y adentro queda un
  `FALTANTES.txt` diciendo cuántos faltaron y por qué.
- El ZIP va sin comprimir (`ZIP_STORED`): JPEG y RAW ya vienen comprimidos, deflate sólo
  gastaría CPU.
- La descarga va por un `<form>`, no por `fetch`: el navegador lo guarda a disco
  directamente en vez de meter varios GB en memoria.

**Lo que no pude probar:** la lectura real de los archivos, porque `K:` no está montado.
Probé todo lo demás con archivos de mentira en `test_descargar.py` (6 casos, pasa), y
contra la base real confirmé el armado del nombre y que sin disco responde 404 y
`FALTANTES.txt` en vez de romperse. **Cuando montes `K:`, la primera descarga es la
prueba que falta.**

Un techo conocido, anotado en el código: el ZIP se arma entero antes de mandarlo y Flask
atiende de a uno, así que mientras se arma el curador no responde. Con 50-100 fotos por
LAN son segundos; para bajar un evento entero conviene el dock por USB.

Los 11 tests del proyecto pasan.

---

## 7. Si mañana querés seguir

1. **Montá `K:`** (`powershell -File montar_K.ps1`, te va a pedir la contraseña de
   Samba) y probá una descarga real desde el visor.
2. **Decidí las dos rutinas**: sacar `proseguirpipeline` y reponer el backup diario.
3. Los 27 eventos vacíos, si te molestan en el desplegable.
4. Cuando llegue el cable USB 3.0 A-B, ahí sí tiene sentido pensar en corridas grandes.
