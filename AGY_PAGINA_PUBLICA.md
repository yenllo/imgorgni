# Modificar la página pública de rbfotos.studio

Tarea: cambiar **solo la portada pública** de `https://rbfotos.studio/`.
Todo lo demás del sistema está funcionando con gente usándolo ahora mismo.

---

## 1. Prohibido (leé esto antes que nada)

- **No edites `galeria.py` por SSH ni con `sed` remoto.** Ese archivo es el
  sitio en vivo. Se toca con el ciclo del punto 4 y de ninguna otra forma.
- **No toques `photos_v2.db` ni sus respaldos.** Son 3,5 GB y son la fuente de
  verdad de eventos, caras y etiquetas. No está en la Pi, pero si lo ves, no
  lo abras ni para leer.
- **No toques nada fuera del bloque `PORTADA` y su CSS** (ver punto 3). En
  particular, dejá exactamente como están:
  `dentro_de_raiz`, `traducir_ruta`, `foto_autorizada`, `_cliente_de`,
  `clave_ok`, `expirada`, y todas las rutas `/g/`, `/p/`, `/d/`, `/api/` y
  `/panel/`. Son el control de acceso: un cambio ahí filtra fotos privadas
  de clientes que pagaron.
- **No agregues dependencias.** La Pi tiene Flask, Pillow y rawpy y nada más.
  No instales nada, no importes nada nuevo.
- **No uses recursos externos**: ni CDN, ni Google Fonts, ni `<script src=…>`
  a otro dominio, ni imágenes remotas. La página se sirve entera desde la Pi
  y tiene que seguir funcionando sin internet de salida.
- **No borres los archivos `galeria.py.bak-*`** de la Pi. Son la vuelta atrás.
- **No corras `git`.** Este proyecto no está bajo control de versiones.
- Si para hacer el cambio necesitás tocar algo de esta lista: **no lo hagas,
  pará y reportá qué necesitabas y por qué.**

---

## 2. Qué es este sistema, en cuatro líneas

Renzo es fotógrafo deportivo. `rbfotos.studio` es su sitio, y corre en una
Raspberry Pi que él tiene en su casa, publicada al mundo por un túnel de
Cloudflare (no hay puertos abiertos en el módem).

El sitio tiene dos mitades:

| Parte | Qué es | Quién entra |
|---|---|---|
| `/` | La **portada pública**: su vidriera. Es lo único que vas a tocar. | cualquiera |
| `/g/<slug>` | Galerías privadas por evento. El cliente entra con su correo, marca favoritas y las descarga. | quien tenga el link |
| `/panel*` | Su administración: crear galerías, ver quién marcó y quién descargó. | solo él, con contraseña |

Todo eso es **un solo archivo**: `galeria.py`, unas 2.650 líneas, que vive en
la Pi en `/home/yenllo/galeria/galeria.py`. Las plantillas HTML son strings
de Python dentro de ese archivo. No hay carpeta `templates/`.

---

## 3. Dónde está la portada dentro de `galeria.py`

Buscá por nombre, no por número de línea (se mueven).

| Qué | Cómo encontrarlo |
|---|---|
| **La plantilla de la portada** | `PORTADA = """` … hasta la línea anterior a `GATE = ` — son ~78 líneas |
| **Su CSS** | dentro del `<style>` de `BASE_HTML`: los selectores `.viz`, `.hero`, `.marca`, `.rotulo`, `.tira`, `.planes`, `.precio`, `.fino`, `.mi`, `.como`, `.paso`, `.pie`, `.wsp` |
| **La ruta que la sirve** | `@app.get("/")` → función `home()` |
| **Los precios** | constantes `PRECIO_FOTO` (500) y `COMPRA_MINIMA` (5000) |
| **El WhatsApp** | constante `WHATSAPP` |
| **Las fotos de muestra** | carpeta `~/galeria/portada/` en la Pi; la función `muestras()` las lista y la ruta `/muestra/<nombre>` las sirve |

Secciones que ya tiene la portada, en orden:

1. `section.hero` — marca, título, bajada y botón de WhatsApp
2. `div.tira` — la tira de trabajos (solo aparece si hay fotos en `portada/`)
3. `section.planes` — las dos formas de trabajar (venta por foto / evento a convenir)
4. `section.mi` — «Sobre mí»
5. `section.como` — «Cómo recibís tus fotos», tres pasos
6. `footer.pie`

Variables que `home()` le pasa a la plantilla, y que podés usar:
`fotos` (nombres de las muestras), `wsp`, `precio`, `minimo`, `minimo_fotos`.

---

## 4. El ciclo de trabajo — este y no otro

Se corre desde `C:\Renzo\Proyectos\IMGorgni`:

```bash
python galeria_pi.py traer
```

Baja la copia viva a `pi_galeria/galeria.py`. **Empezá siempre por acá**, aunque
ya exista el archivo: puede haber cambiado en la Pi desde la última vez.

Después editás **`pi_galeria/galeria.py`** (la copia local), y:

```bash
python galeria_pi.py probar
```

Sube una copia a `/tmp` de la Pi y corre ahí el self-check que el propio archivo
trae. Tiene que decir `ok`. No corre en Windows a propósito: el self-check
compara rutas con `/` y acá `Path` las arma con `\`, así que fallaría por el
separador y no por tu cambio.

Cuando pasa:

```bash
python galeria_pi.py subir
```

Ese comando, en orden: corre el self-check y **si falla no sube nada**;
respalda el archivo vivo en la Pi; lo reemplaza; reinicia el servicio; y espera
a que conteste 200. **Si no contesta, restaura el respaldo solo.**

Está verificado que la primera compuerta funciona: con el archivo roto a
propósito, `subir` se negó y el archivo vivo quedó intacto.

---

## 5. Cómo llegar a la Pi si necesitás mirar algo

Hay acceso SSH sin contraseña ya configurado:

```bash
ssh fotosnas
```

Es `yenllo@192.168.1.62`. Si `fotosnas` no resuelve, va directo por IP, o por
Tailscale a `100.78.164.81`.

Cosas útiles de solo lectura:

```bash
ssh fotosnas 'ls ~/galeria/portada/'
```

```bash
ssh fotosnas 'systemctl status galeria --no-pager | head -5'
```

```bash
ssh fotosnas 'journalctl -u galeria -n 40 --no-pager'
```

**`sudo` pide contraseña y vos no la tenés.** Si algo parece necesitar `sudo`,
no lo intentes: reportalo.

---

## 6. Cómo verificar lo que hiciste

Además del self-check, mirá el HTML que sale de verdad:

```bash
ssh fotosnas 'curl -s http://127.0.0.1:8000/ | head -60'
```

Y que no se te haya colado un recurso externo:

```bash
ssh fotosnas 'curl -s http://127.0.0.1:8000/ | grep -o "https\?://[^\" ]*" | sort -u'
```

Ahí solo pueden aparecer enlaces a `wa.me` (el WhatsApp). Cualquier otro
dominio significa que metiste un recurso externo, que está prohibido.

---

## 7. Entregable

1. `pi_galeria/galeria.py` modificado, con el cambio pedido y **nada más**.
2. El cambio publicado con `python galeria_pi.py subir`, terminando en
   `la Pi responde 200. Publicado.`
3. Un resumen en `reporte_portada.md` con estas claves exactas:

```json
{
  "que_cambie": "una frase",
  "secciones_tocadas": ["hero", "planes"],
  "lineas_agregadas": 0,
  "lineas_borradas": 0,
  "self_check": "ok",
  "http_final": 200,
  "dominios_externos": [],
  "respaldo_en_la_pi": "galeria.py.bak-agy-AAAAMMDD-HHMMSS"
}
```

Si algo no lo pudiste hacer, ponelo en `"que_no_pude"` en vez de inventar un
resultado. Un reporte que dice «no pude» es útil; uno que miente cuesta horas.
