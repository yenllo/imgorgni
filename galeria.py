#!/usr/bin/env python3
"""Galeria privada de clientes — corre en la Pi, detras de Caddy.

Reemplazo casero de Pixieset. Dos niveles de archivo, a proposito:

  - lo que ve el navegador: preview de 1600px CON marca de agua, generada al
    vuelo y cacheada. Que se la bajen con F12 no importa.
  - lo que se entrega: el original limpio, solo por /d/<id> y solo si el cliente
    entro con su email a ESA galeria.

Uso:
    python3 galeria.py servir                      # el servicio
    python3 galeria.py publicar "Nombre" lista.txt # devuelve el link secreto
    python3 galeria.py calentar <slug>             # genera las previews antes
    python3 galeria.py test                        # self-check
"""
import os
import io
import re
import csv
import sys
import time
import secrets
import sqlite3
import hashlib
import zipfile
from datetime import datetime, timezone, date
from functools import lru_cache
from pathlib import Path

from flask import (Flask, g, session, request, redirect, url_for, abort,
                   send_file, render_template_string, jsonify, Response)
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("GALERIA_DB", BASE / "galeria.db"))
CACHE = Path(os.environ.get("GALERIA_CACHE", BASE / "cache"))
RAIZ_FOTOS = Path(os.environ.get("GALERIA_RAIZ", "/mnt/fotos"))
MARCA = os.environ.get("GALERIA_MARCA", "rb.fotos")

# Mas chico que lo que entregas, a proposito. Si el preview mide lo mismo que
# el archivo final, la marca de agua es lo unico que los separa; con 1000px de
# lado largo el preview sirve para elegir y no sustituye a la entrega.
ANCHO_PREVIEW = int(os.environ.get("GALERIA_ANCHO", 1000))

# Cuantas fotos por pagina. Una fecha deportiva son 2000-4000: en una sola
# pagina son ~16 mil nodos de DOM y el telefono del cliente se arrastra.
PAGINA = int(os.environ.get("GALERIA_PAGINA", 300))

# Portada publica. Las fotos de muestra son las que haya en esta carpeta, por
# orden de nombre: para reordenarlas alcanza con renombrarlas 01-, 02-, etc.
# Van SIN marca de agua — son tuyas, no de un cliente.
# Catalogo de eventos exportado de photos_v2.db por exportar_catalogo.py.
# Son 20 MB contra los 3,4 GB del original: aca no vienen embeddings ni scores,
# solo que eventos hay y que fotos tiene cada uno. Si no existe, la seccion de
# eventos simplemente no aparece.
CATALOGO = Path(os.environ.get("GALERIA_CATALOGO", BASE / "catalogo.db"))

MUESTRAS = Path(os.environ.get("GALERIA_MUESTRAS", BASE / "portada"))
ANCHO_MUESTRA = int(os.environ.get("GALERIA_ANCHO_MUESTRA", 1400))

# Numero de WhatsApp en formato internacional sin signos: 56912345678.
# Vacio = no se dibuja el boton, en vez de dejar un link roto.
WHATSAPP = os.environ.get("GALERIA_WHATSAPP", "56973756165")

# Venta por foto de las fechas deportivas. En pesos chilenos.
PRECIO_FOTO = int(os.environ.get("GALERIA_PRECIO_FOTO", 500))
COMPRA_MINIMA = int(os.environ.get("GALERIA_COMPRA_MINIMA", 5000))


def pesos(n):
    """5000 -> $5.000. Los miles en Chile van con punto."""
    return "$" + f"{n:,}".replace(",", ".")

# ponytail: la clave de sesion vive en un archivo al lado de la DB. Un secreto
# para un servicio no justifica traer un gestor de secretos.
_clave = BASE / ".secret"
if not _clave.exists():
    _clave.write_text(secrets.token_hex(32))
    _clave.chmod(0o600)

app = Flask(__name__)
app.secret_key = _clave.read_text().strip()

# Cloudflare corta las subidas de mas de 100 MB, asi que el limite de aca tiene
# que ser mas chico: mejor un error claro nuestro que un 413 del proxy.
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

ESQUEMA = """
CREATE TABLE IF NOT EXISTS galeria (
    id              INTEGER PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    nombre          TEXT NOT NULL,
    creada          TEXT NOT NULL,
    clave_hash      TEXT,
    portada_foto_id INTEGER,
    expira          TEXT
);
CREATE TABLE IF NOT EXISTS foto (
    id         INTEGER PRIMARY KEY,
    galeria_id INTEGER NOT NULL REFERENCES galeria(id) ON DELETE CASCADE,
    ruta       TEXT NOT NULL,
    raw        TEXT,
    orden      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_foto_galeria ON foto(galeria_id, orden);
CREATE TABLE IF NOT EXISTS cliente (
    id         INTEGER PRIMARY KEY,
    galeria_id INTEGER NOT NULL REFERENCES galeria(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    visto      TEXT NOT NULL,
    UNIQUE(galeria_id, email)
);
CREATE TABLE IF NOT EXISTS megusta (
    cliente_id INTEGER NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    foto_id    INTEGER NOT NULL REFERENCES foto(id) ON DELETE CASCADE,
    PRIMARY KEY (cliente_id, foto_id)
);
"""


def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


def conectar():
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    cx.executescript(ESQUEMA)
    # galerias creadas antes de que existiera la columna raw
    if "raw" not in {r["name"] for r in cx.execute("PRAGMA table_info(foto)")}:
        cx.execute("ALTER TABLE foto ADD COLUMN raw TEXT")
        cx.commit()
    # columnas agregadas despues: una galeria vieja las tiene en NULL y se
    # comporta exactamente igual que antes de que existieran
    tiene = {r["name"] for r in cx.execute("PRAGMA table_info(galeria)")}
    for col, tipo in (("clave_hash", "TEXT"), ("portada_foto_id", "INTEGER"),
                      ("expira", "TEXT")):
        if col not in tiene:
            cx.execute(f"ALTER TABLE galeria ADD COLUMN {col} {tipo}")
            cx.commit()
    return cx


@app.teardown_appcontext
def cerrar(_):
    for nombre in ("db", "cat"):
        cx = g.pop(nombre, None)
        if cx is not None:
            cx.close()


# ---------------------------------------------------------------- rutas fotos

def traducir_ruta(ruta):
    """K:\\Eventos\\x.jpg -> /mnt/fotos/Eventos/x.jpg

    La notebook ve el disco por Samba como K:, la Pi lo tiene montado. Es el
    mismo disco, cambia el prefijo.
    """
    r = str(ruta).strip().replace("\\", "/")
    bajo = r.lower()
    if bajo.startswith("k:/"):
        r = r[3:]
    elif bajo.startswith(str(RAIZ_FOTOS).lower() + "/"):
        r = r[len(str(RAIZ_FOTOS)) + 1:]
    return str(RAIZ_FOTOS / r.lstrip("/"))


def dentro_de_raiz(ruta):
    """Nadie sirve nada de afuera de /mnt/fotos, venga de donde venga."""
    try:
        Path(ruta).resolve().relative_to(RAIZ_FOTOS.resolve())
        return True
    except ValueError:
        return False


VISIBLES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
CRUDAS = (".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf")


def es_visible(p):
    return Path(p).suffix.lower() in VISIBLES


def abrible(ruta):
    """Pillow puede abrirlo de verdad, no solo tiene extension de foto.

    El archivo lo dice la papelera de Windows: /mnt/fotos/$RECYCLE.BIN/$I....JPG
    no es una imagen. Mejor descartarlo al publicar que romper la grilla despues.
    """
    try:
        with Image.open(ruta):
            return True
    except Exception:
        return False


def buscar_raw(jpg):
    """El RAW hermano, si esta al lado con el mismo nombre.

    ponytail: solo mira la misma carpeta. Si algun dia los RAW viven separados
    de los JPEG, aca va el mapeo de carpetas, no una busqueda global.
    """
    p = Path(jpg)
    for ext in CRUDAS:
        for cand in (p.with_suffix(ext), p.with_suffix(ext.upper())):
            if cand.exists():
                return str(cand)
    return None


# ------------------------------------------------------------- marca de agua

def _fuente(px):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, px)
    return ImageFont.load_default()


@lru_cache(maxsize=6)
def capa_marca(w, h):
    """La capa de marca depende solo del tamaño, no de la foto.

    Rotarla cuesta ~0.85s en la Pi 4 y dibujarla otros 0.20s — mas de la mitad
    del costo total. Como una galeria tiene dos o tres medidas distintas, se
    calcula una vez por medida y se reusa para todas las fotos.
    """
    lado = int((w ** 2 + h ** 2) ** 0.5) + 2
    capa = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    fuente = _fuente(max(14, int(w * 0.042)))
    izq, arr, der, aba = d.textbbox((0, 0), MARCA, font=fuente)
    paso_x, paso_y = (der - izq) + w * 0.14, (aba - arr) * 4.5

    y = 0
    fila = 0
    while y < lado:
        x = -paso_x if fila % 2 else 0
        while x < lado:
            d.text((x, y), MARCA, font=fuente, fill=(255, 255, 255, 62))
            x += paso_x
        y += paso_y
        fila += 1

    capa = capa.rotate(30, resample=Image.BICUBIC, center=(lado / 2, lado / 2))
    return capa.crop(((lado - w) // 2, (lado - h) // 2,
                      (lado - w) // 2 + w, (lado - h) // 2 + h))


def marcar(origen, destino):
    """Preview reducida con la marca en diagonal, compuesta sobre los pixeles.

    A proposito NO es un overlay de CSS: ahi abajo quedaria la foto limpia y
    cualquiera se la lleva desde el inspector.
    """
    im = Image.open(origen)
    # draft() antes de tocar los pixeles: el decodificador de JPEG baja la
    # resolucion durante la descompresion (1/2, 1/4, 1/8) en vez de decodificar
    # 6000px para despues tirar el 95%. Tiene que ir antes de exif_transpose,
    # que carga la imagen entera.
    im.draft("RGB", (ANCHO_PREVIEW, ANCHO_PREVIEW))
    im = ImageOps.exif_transpose(im)
    im.thumbnail((ANCHO_PREVIEW, ANCHO_PREVIEW), Image.LANCZOS)
    im = im.convert("RGBA")

    out = Image.alpha_composite(im, capa_marca(im.width, im.height)).convert("RGB")
    destino.parent.mkdir(parents=True, exist_ok=True)
    out.save(destino, "JPEG", quality=82, optimize=True)


def preview(foto_id, ruta):
    """Cachea por (ruta, mtime, marca): si cambia cualquiera, se regenera."""
    st = os.stat(ruta)
    clave = hashlib.sha1(
        f"{ruta}|{st.st_mtime_ns}|{MARCA}|{ANCHO_PREVIEW}".encode()
    ).hexdigest()
    destino = CACHE / clave[:2] / f"{clave}.jpg"
    if not destino.exists():
        marcar(ruta, destino)
    return destino


# --------------------------------------------------------------- autorizacion

def catalogo():
    """Conexion de solo lectura al catalogo, o None si todavia no se exporto."""
    if not CATALOGO.exists():
        return None
    if "cat" not in g:
        g.cat = sqlite3.connect(f"file:{CATALOGO}?mode=ro", uri=True)
        g.cat.row_factory = sqlite3.Row
    return g.cat


def subidas_dir():
    """Se calcula al llamar, no al importar: el self-check mueve RAIZ_FOTOS."""
    return RAIZ_FOTOS / "_subidas"


def carpeta_segura(nombre):
    """Nombre de carpeta a prueba de todo: solo letras, numeros, espacio, - y _."""
    return re.sub(r"[^A-Za-z0-9 _-]", "", nombre or "").strip()[:60]


def muestras():
    """Lo que haya en MUESTRAS, ordenado por nombre. Si no existe, lista vacia."""
    if not MUESTRAS.is_dir():
        return []
    return sorted(f.name for f in MUESTRAS.iterdir() if f.is_file() and es_visible(f))


def sin_marca(ruta):
    """Como preview() pero sin marca de agua y mas grande. Mismo cache."""
    st = os.stat(ruta)
    clave = hashlib.sha1(
        f"muestra|{ruta}|{st.st_mtime_ns}|{ANCHO_MUESTRA}".encode()).hexdigest()
    destino = CACHE / clave[:2] / f"{clave}.jpg"
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        im = ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")
        im.thumbnail((ANCHO_MUESTRA, ANCHO_MUESTRA), Image.LANCZOS)
        im.save(destino, "JPEG", quality=82, optimize=True, progressive=True)
    return destino


def entradas():
    return set(session.get("galerias", []))


def foto_autorizada(foto_id):
    """Devuelve la foto solo si el cliente entro a la galeria que la contiene.

    Sin este chequeo los id son enumerables y se cruzan galerias.
    """
    f = db().execute(
        "SELECT foto.*, galeria.slug, galeria.expira FROM foto "
        "JOIN galeria ON galeria.id = foto.galeria_id WHERE foto.id = ?",
        (foto_id,)).fetchone()
    if f is None or f["galeria_id"] not in entradas() or expirada(f):
        abort(404)
    return f


def cliente_actual(galeria_id):
    cid = (session.get("clientes") or {}).get(str(galeria_id))
    return int(cid) if cid else None


def hash_clave(texto):
    """scrypt de stdlib. No trae dependencias y no guarda el texto plano."""
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(texto.encode(), salt=sal, n=16384, r=8, p=1)
    return f"scrypt${sal.hex()}${h.hex()}"


def clave_ok(guardado, texto):
    try:
        _, sal, h = guardado.split("$")
        calc = hashlib.scrypt(texto.encode(), salt=bytes.fromhex(sal),
                              n=16384, r=8, p=1)
    except (ValueError, AttributeError, TypeError):
        return False
    return secrets.compare_digest(calc.hex(), h)


def expirada(gal):
    """Vence al TERMINAR el dia indicado. NULL = no vence nunca."""
    f = gal["expira"]
    if not f:
        return False
    try:
        return datetime.now(timezone.utc).date() > date.fromisoformat(f)
    except ValueError:
        # una fecha rota no puede dejar al cliente afuera de su propia galeria
        return False


# --------------------------------------------------------------------- vistas

@app.get("/")
def home():
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PORTADA, fotos=muestras(), wsp=WHATSAPP,
        precio=pesos(PRECIO_FOTO), minimo=pesos(COMPRA_MINIMA),
        minimo_fotos=COMPRA_MINIMA // PRECIO_FOTO))


@app.get("/muestra/<nombre>")
def muestra(nombre):
    """Foto de la portada. Es publica a proposito: es la vidriera."""
    f = MUESTRAS / nombre
    # nombre plano y nada mas: ni subcarpetas ni ..
    if ("/" in nombre or "\\" in nombre or not f.is_file()
            or f.resolve().parent != MUESTRAS.resolve()):
        abort(404)
    try:
        return send_file(sin_marca(f), mimetype="image/jpeg", max_age=604800)
    except Exception:
        app.logger.exception("no pude generar la muestra %s", f)
        abort(404)


@app.get("/g/<slug>")
def galeria(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    if expirada(gal):
        return render_template_string(BASE_HTML,
                                      cuerpo=render_template_string(EXPIRADA, gal=gal))
    if gal["id"] not in entradas():
        return render_template_string(BASE_HTML, cuerpo=render_template_string(
            GATE, gal=gal, error=request.args.get("error")))

    cid = cliente_actual(gal["id"])
    total = db().execute("SELECT COUNT(*) c FROM foto WHERE galeria_id = ?",
                         (gal["id"],)).fetchone()["c"]
    paginas = max(1, -(-total // PAGINA))
    try:
        p = min(max(1, int(request.args.get("p", 1))), paginas)
    except ValueError:
        p = 1

    fotos = db().execute(
        "SELECT foto.id, foto.raw IS NOT NULL AS tiene_raw, "
        "  EXISTS(SELECT 1 FROM megusta WHERE foto_id = foto.id AND cliente_id = ?)"
        "  AS mia "
        "FROM foto WHERE galeria_id = ? ORDER BY orden, id LIMIT ? OFFSET ?",
        (cid, gal["id"], PAGINA, (p - 1) * PAGINA)).fetchall()
    marcadas = db().execute(
        "SELECT COUNT(*) c FROM megusta JOIN foto ON foto.id = megusta.foto_id "
        "WHERE foto.galeria_id = ? AND megusta.cliente_id = ?",
        (gal["id"], cid)).fetchone()["c"]
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        GRILLA, gal=gal, fotos=fotos, total=total, marcadas=marcadas,
        p=p, paginas=paginas))


@app.post("/g/<slug>/entrar")
def entrar(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    email = (request.form.get("email") or "").strip().lower()
    if "@" not in email or len(email) < 5 or len(email) > 254:
        return redirect(url_for("galeria", slug=slug))

    # la galeria vencida no se chequea aca a proposito: galeria() muestra la
    # pantalla de vencida, y foto_autorizada() corta el acceso a los archivos
    if gal["clave_hash"] and not clave_ok(gal["clave_hash"],
                                          request.form.get("clave") or ""):
        return redirect(url_for("galeria", slug=slug, error="Contrasena incorrecta"))

    cx = db()
    cx.execute(
        "INSERT INTO cliente (galeria_id, email, visto) VALUES (?, ?, ?) "
        "ON CONFLICT(galeria_id, email) DO UPDATE SET visto = excluded.visto",
        (gal["id"], email, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    cx.commit()
    cid = cx.execute("SELECT id FROM cliente WHERE galeria_id = ? AND email = ?",
                     (gal["id"], email)).fetchone()["id"]

    session["galerias"] = sorted(entradas() | {gal["id"]})
    clientes = session.get("clientes") or {}
    clientes[str(gal["id"])] = cid
    session["clientes"] = clientes
    session.permanent = True
    return redirect(url_for("galeria", slug=slug))


@app.get("/p/<int:foto_id>")
def ver_preview(foto_id):
    f = foto_autorizada(foto_id)
    if not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    try:
        img = preview(foto_id, f["ruta"])
    except Exception:
        # una foto ilegible rompe su miniatura, no la pagina entera del cliente
        app.logger.exception("no pude generar preview de %s", f["ruta"])
        abort(404)
    return send_file(img, mimetype="image/jpeg", max_age=86400)


@app.get("/d/<int:foto_id>")
@app.get("/d/<int:foto_id>/<cual>")
def descargar(foto_id, cual="original"):
    f = foto_autorizada(foto_id)
    ruta = f["raw"] if cual == "raw" and f["raw"] else f["ruta"]
    if not dentro_de_raiz(ruta) or not os.path.exists(ruta):
        abort(404)
    return send_file(ruta, as_attachment=True,
                     download_name=os.path.basename(ruta))


@app.get("/portada/<slug>")
def portada(slug):
    """La tapa se ve ANTES de entrar, asi que no pasa por foto_autorizada().

    Es el preview marcado al agua, nunca el original, y solo si la foto es de
    esa galeria: sin ese AND, un portada_foto_id apuntaria a cualquier foto.
    """
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal) or not gal["portada_foto_id"]:
        abort(404)
    f = db().execute("SELECT * FROM foto WHERE id = ? AND galeria_id = ?",
                     (gal["portada_foto_id"], gal["id"])).fetchone()
    if f is None or not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    try:
        return send_file(preview(f["id"], f["ruta"]), mimetype="image/jpeg",
                         max_age=86400)
    except Exception:
        abort(404)


class _Tuberia:
    """Objeto tipo-archivo que junta lo que zipfile escribe y lo va soltando.

    seekable() en False obliga a zipfile a escribir en modo streaming (con data
    descriptors). Es lo unico que permite mandar un zip de varios GB desde una
    Pi de 2 GB de RAM sin armarlo antes en memoria ni en disco.
    """

    def __init__(self):
        self.trozos = []
        self.pos = 0

    def write(self, datos):
        self.trozos.append(bytes(datos))
        self.pos += len(datos)
        return len(datos)

    def tell(self):
        return self.pos

    def seekable(self):
        return False

    def flush(self):
        pass

    def sacar(self):
        d = b"".join(self.trozos)
        self.trozos.clear()
        return d


def _zip_stream(rutas):
    """Genera el zip de a 1 MB. Recibe las rutas ya resueltas a proposito: esto
    corre despues de que Flask cerro el contexto, aca no se puede tocar la DB."""
    t = _Tuberia()
    # ZIP_STORED: son JPEG, comprimir gasta CPU de la Pi para ahorrar ~0%
    with zipfile.ZipFile(t, "w", zipfile.ZIP_STORED) as z:
        usados = set()
        for ruta in rutas:
            if not dentro_de_raiz(ruta) or not os.path.exists(ruta):
                continue
            nombre = os.path.basename(ruta)
            if nombre in usados:  # dos carpetas pueden traer el mismo nombre
                raiz, ext = os.path.splitext(nombre)
                n = 2
                while f"{raiz}-{n}{ext}" in usados:
                    n += 1
                nombre = f"{raiz}-{n}{ext}"
            usados.add(nombre)
            try:
                with open(ruta, "rb") as src, z.open(nombre, "w") as dst:
                    while True:
                        trozo = src.read(1024 * 1024)
                        if not trozo:
                            break
                        dst.write(trozo)
                        yield t.sacar()
            except OSError:
                # una foto ilegible no puede tumbar la descarga entera
                continue
            yield t.sacar()
    yield t.sacar()


@app.get("/g/<slug>/zip")
@app.get("/g/<slug>/zip/<cuales>")
def zip_galeria(slug, cuales="todas"):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal) or gal["id"] not in entradas():
        abort(404)

    if cuales == "elegidas":
        filas = db().execute(
            "SELECT foto.ruta FROM foto JOIN megusta ON megusta.foto_id = foto.id "
            "WHERE foto.galeria_id = ? AND megusta.cliente_id = ? ORDER BY foto.orden",
            (gal["id"], cliente_actual(gal["id"]))).fetchall()
        nombre = f"{slug}-elegidas.zip"
    elif cuales == "todas":
        filas = db().execute(
            "SELECT ruta FROM foto WHERE galeria_id = ? ORDER BY orden, id",
            (gal["id"],)).fetchall()
        nombre = f"{slug}.zip"
    else:
        abort(404)

    # ponytail: se arma al vuelo en cada pedido, sin cachear. Son originales de
    # varios GB; guardarlos cuesta mas disco del que ahorra en tiempo.
    return Response(_zip_stream([f["ruta"] for f in filas]),
                    mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.post("/api/megusta")
def megusta():
    datos = request.get_json(silent=True) or {}
    f = foto_autorizada(int(datos.get("foto_id", 0)))
    cid = cliente_actual(f["galeria_id"])
    if cid is None:
        abort(403)
    cx = db()
    ya = cx.execute("SELECT 1 FROM megusta WHERE cliente_id = ? AND foto_id = ?",
                    (cid, f["id"])).fetchone()
    if ya:
        cx.execute("DELETE FROM megusta WHERE cliente_id = ? AND foto_id = ?",
                   (cid, f["id"]))
    else:
        cx.execute("INSERT INTO megusta (cliente_id, foto_id) VALUES (?, ?)",
                   (cid, f["id"]))
    cx.commit()
    return jsonify(mia=not ya)


OCULTAS = {"_galeria", "_thumbs_curador", "_vistas_curador", "_autocull_curador",
           "$RECYCLE.BIN", "System Volume Information"}


def _carpeta(rel):
    aqui = (RAIZ_FOTOS / rel.strip("/")).resolve() if rel.strip("/") else RAIZ_FOTOS.resolve()
    if not dentro_de_raiz(aqui) or not aqui.is_dir():
        abort(404)
    return aqui


@app.get("/panel/nueva")
def nueva():
    rel = (request.args.get("dir") or "").strip("/")
    aqui = _carpeta(rel)
    subdirs, fotos = [], []
    with os.scandir(aqui) as it:
        for e in it:
            if e.name.startswith(".") or e.name in OCULTAS:
                continue
            if e.is_dir():
                subdirs.append(e.name)
            elif es_visible(e.name):
                fotos.append(e.name)
    migas, acum = [], ""
    for parte in rel.split("/") if rel else []:
        acum = f"{acum}/{parte}".strip("/")
        migas.append((parte, acum))
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_NUEVA, rel=rel, migas=migas, subdirs=sorted(subdirs),
        cuantas=len(fotos)))


@app.post("/panel/nueva")
def crear():
    rel = (request.form.get("dir") or "").strip("/")
    nombre = (request.form.get("nombre") or "").strip()
    aqui = _carpeta(rel)
    rutas = sorted(str(p) for p in aqui.iterdir() if p.is_file() and es_visible(p))
    if not nombre or not rutas:
        abort(400)
    slug, _puestas, _faltan = crear_galeria(db(), nombre, rutas)
    return redirect(url_for("panel", slug=slug))


@app.post("/panel/<slug>/borrar")
def borrar(slug):
    """Borra la galeria. Las fotos del disco no se tocan — solo el listado,
    los emails y los me-gusta. El cache de previews queda y se reusa si
    volves a publicar las mismas fotos."""
    cx = db()
    if cx.execute("SELECT 1 FROM galeria WHERE slug = ?", (slug,)).fetchone() is None:
        abort(404)
    cx.execute("DELETE FROM galeria WHERE slug = ?", (slug,))
    cx.commit()
    return redirect(url_for("panel"))


@app.get("/panel/p/<int:foto_id>")
def panel_preview(foto_id):
    """Caddy protege /panel* con basic_auth. Sin esta ruta el panel no puede
    mostrar miniaturas: /p/<id> exige sesion de cliente."""
    f = db().execute("SELECT * FROM foto WHERE id = ?", (foto_id,)).fetchone()
    if f is None or not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    try:
        return send_file(preview(foto_id, f["ruta"]), mimetype="image/jpeg",
                         max_age=86400)
    except Exception:
        abort(404)


@app.get("/panel/eventos")
def eventos():
    cat = catalogo()
    if cat is None:
        return render_template_string(BASE_HTML, cuerpo=render_template_string(
            SIN_CATALOGO)), 404
    filas = cat.execute(
        "SELECT id, nombre, inicio, fotos, COALESCE(NULLIF(coleccion,''), ?) col "
        "FROM evento ORDER BY col = ?, col, inicio DESC", ("Sueltos", "Sueltos")).fetchall()
    grupos = {}
    for f in filas:
        grupos.setdefault(f["col"], []).append(f)
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_EVENTOS, grupos=grupos, total=len(filas)))


@app.get("/panel/eventos/<int:eid>")
def evento(eid):
    cat = catalogo()
    if cat is None:
        abort(404)
    ev = cat.execute("SELECT * FROM evento WHERE id = ?", (eid,)).fetchone()
    if ev is None:
        abort(404)
    paginas = max(1, -(-ev["fotos"] // PAGINA))
    try:
        pg = min(max(1, int(request.args.get("p", 1))), paginas)
    except ValueError:
        pg = 1
    fotos = cat.execute(
        "SELECT f.id, f.ruta FROM evento_foto ef JOIN foto f ON f.id = ef.foto_id "
        "WHERE ef.evento_id = ? ORDER BY f.fecha, f.id LIMIT ? OFFSET ?",
        (eid, PAGINA, (pg - 1) * PAGINA)).fetchall()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_EVENTO, ev=ev, fotos=fotos, p=pg, paginas=paginas))


@app.get("/panel/cat/<int:foto_id>")
def cat_preview(foto_id):
    """Miniatura de una foto del catalogo. Reusa el mismo cache que las
    galerias: si despues publicas el evento, ya estan hechas."""
    cat = catalogo()
    if cat is None:
        abort(404)
    f = cat.execute("SELECT ruta FROM foto WHERE id = ?", (foto_id,)).fetchone()
    if f is None:
        abort(404)
    ruta = traducir_ruta(f["ruta"])
    if not dentro_de_raiz(ruta) or not os.path.exists(ruta):
        abort(404)
    try:
        return send_file(preview(foto_id, ruta), mimetype="image/jpeg", max_age=86400)
    except Exception:
        abort(404)


@app.post("/panel/eventos/<int:eid>/publicar")
def publicar_evento(eid):
    cat = catalogo()
    if cat is None:
        abort(404)
    ev = cat.execute("SELECT * FROM evento WHERE id = ?", (eid,)).fetchone()
    if ev is None:
        abort(404)
    rutas = [r["ruta"] for r in cat.execute(
        "SELECT f.ruta FROM evento_foto ef JOIN foto f ON f.id = ef.foto_id "
        "WHERE ef.evento_id = ? ORDER BY f.fecha, f.id", (eid,))]
    nombre = (request.form.get("nombre") or "").strip() or ev["nombre"] or f"Evento {eid}"
    cx = db()
    slug, puestas, faltan = crear_galeria(cx, nombre[:120], rutas, verificar=False)
    cx.commit()
    return redirect(url_for("panel", slug=slug))


@app.get("/panel/subir")
def subir_form():
    return render_template_string(BASE_HTML,
                                  cuerpo=render_template_string(PANEL_SUBIR))


@app.post("/panel/subir")
def subir_archivo():
    """Un archivo por request. Cloudflare corta a los 100 MB, asi que subir una
    fecha entera en un solo POST no es una opcion."""
    carpeta = carpeta_segura(request.form.get("carpeta"))
    f = request.files.get("archivo")
    if not carpeta or f is None or not f.filename:
        return jsonify(error="falta la carpeta o el archivo"), 400

    destino = subidas_dir() / carpeta
    # el saneado ya lo garantiza, pero el guard vale igual: es un trust boundary
    if destino.resolve().parent != subidas_dir().resolve():
        abort(404)
    destino.mkdir(parents=True, exist_ok=True)

    nombre = secure_filename(f.filename)
    if not nombre or not es_visible(Path(nombre)):
        return jsonify(error=f"{f.filename}: formato no aceptado"), 400

    ruta = destino / nombre
    n = 2
    while ruta.exists():  # no pisar lo que ya subiste
        ruta = destino / f"{Path(nombre).stem}-{n}{Path(nombre).suffix}"
        n += 1
    f.save(ruta)

    # que se llame .jpg no lo hace un jpg: si no abre, no se queda en el disco
    if not abrible(ruta):
        ruta.unlink(missing_ok=True)
        return jsonify(error=f"{f.filename}: no es una imagen"), 400
    return jsonify(ok=True, nombre=ruta.name)


@app.post("/panel/<slug>/ajustes")
def ajustes(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    cx = db()

    clave = (request.form.get("clave") or "").strip()
    if request.form.get("sacar_clave"):
        cx.execute("UPDATE galeria SET clave_hash = NULL WHERE id = ?", (gal["id"],))
    elif clave:
        cx.execute("UPDATE galeria SET clave_hash = ? WHERE id = ?",
                   (hash_clave(clave), gal["id"]))

    expira = (request.form.get("expira") or "").strip()
    try:
        expira = date.fromisoformat(expira).isoformat() if expira else None
    except ValueError:
        expira = gal["expira"]
    cx.execute("UPDATE galeria SET expira = ? WHERE id = ?", (expira, gal["id"]))

    tapa = (request.form.get("portada") or "").strip()
    fid = None
    if tapa.isdigit():
        # solo una foto de ESTA galeria puede ser su tapa
        fila = cx.execute("SELECT id FROM foto WHERE id = ? AND galeria_id = ?",
                          (int(tapa), gal["id"])).fetchone()
        fid = fila["id"] if fila else None
    cx.execute("UPDATE galeria SET portada_foto_id = ? WHERE id = ?", (fid, gal["id"]))

    cx.commit()
    return redirect(url_for("panel", slug=slug))


@app.get("/panel/<slug>/seleccion.csv")
def seleccion_csv(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    filas = db().execute(
        "SELECT foto.ruta, foto.orden, COUNT(*) AS votos, "
        "  GROUP_CONCAT(cliente.email, ';') AS emails "
        "FROM megusta JOIN foto ON foto.id = megusta.foto_id "
        "JOIN cliente ON cliente.id = megusta.cliente_id "
        "WHERE foto.galeria_id = ? GROUP BY foto.id "
        "ORDER BY votos DESC, foto.orden", (gal["id"],)).fetchall()

    # el modulo csv y no un join a mano: las rutas y los emails traen comas
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["ruta", "nombre", "votos", "emails"])
    for f in filas:
        w.writerow([f["ruta"], os.path.basename(f["ruta"]), f["votos"], f["emails"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{slug}-seleccion.csv"'})


@app.get("/panel")
@app.get("/panel/<slug>")
def panel(slug=None):
    """Caddy protege /panel* con basic_auth — aca no hay chequeo propio."""
    cx = db()
    if slug is None:
        gals = cx.execute(
            "SELECT galeria.*, "
            "  (SELECT COUNT(*) FROM foto WHERE galeria_id = galeria.id) AS fotos, "
            "  (SELECT COUNT(*) FROM cliente WHERE galeria_id = galeria.id) AS clientes "
            "FROM galeria ORDER BY creada DESC").fetchall()
        return render_template_string(BASE_HTML, cuerpo=render_template_string(
            PANEL_INDICE, gals=gals,
            hoy=datetime.now(timezone.utc).date().isoformat()))

    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    clientes = cx.execute(
        "SELECT cliente.*, COUNT(megusta.foto_id) AS marcadas FROM cliente "
        "LEFT JOIN megusta ON megusta.cliente_id = cliente.id "
        "WHERE cliente.galeria_id = ? GROUP BY cliente.id ORDER BY visto DESC",
        (gal["id"],)).fetchall()
    elegidas = cx.execute(
        "SELECT foto.id, foto.ruta, COUNT(*) AS votos, "
        "  GROUP_CONCAT(cliente.email, ', ') AS quienes "
        "FROM megusta JOIN foto ON foto.id = megusta.foto_id "
        "JOIN cliente ON cliente.id = megusta.cliente_id "
        "WHERE foto.galeria_id = ? GROUP BY foto.id ORDER BY votos DESC, foto.orden",
        (gal["id"],)).fetchall()
    cuenta = cx.execute(
        "SELECT COUNT(*) AS total, COUNT(raw) AS con_raw FROM foto WHERE galeria_id = ?",
        (gal["id"],)).fetchone()
    # ponytail: solo las primeras 24 para elegir tapa. Una fecha son 3000 fotos
    # y nadie elige la portada en la numero 1800.
    primeras = cx.execute(
        "SELECT id FROM foto WHERE galeria_id = ? ORDER BY orden, id LIMIT 24",
        (gal["id"],)).fetchall()
    return render_template_string(
        BASE_HTML, cuerpo=render_template_string(
            PANEL_GAL, gal=gal, clientes=clientes, elegidas=elegidas,
            total=cuenta["total"], con_raw=cuenta["con_raw"],
            primeras=primeras, hoy=datetime.now(timezone.utc).date().isoformat(),
            nombre=os.path.basename))


# ---------------------------------------------------------------------- HTML

BASE_HTML = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>rb.fotos</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#111;color:#eee;font:16px/1.6 system-ui,sans-serif}
header{padding:1.2rem 1.5rem;border-bottom:1px solid #262626;display:flex;
  align-items:baseline;gap:1rem;flex-wrap:wrap}
header b{font-size:1.15rem;letter-spacing:-.02em}
header span{opacity:.55;font-size:.9rem}
main{padding:1.5rem;max-width:1500px;margin:0 auto}
a{color:#8ab4ff}
.grid{display:grid;gap:.55rem;
  grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
.celda{position:relative;background:#1c1c1c;border-radius:7px;overflow:hidden;
  aspect-ratio:3/2}
.celda img{width:100%;height:100%;object-fit:cover;display:block}
.acc{position:absolute;right:.4rem;bottom:.4rem;display:flex;gap:.35rem;
  opacity:0;transition:opacity .15s}
.celda:hover .acc,.celda:focus-within .acc{opacity:1}
.acc a,.acc button{background:rgba(0,0,0,.62);border:0;color:#fff;cursor:pointer;
  width:2.1rem;height:2.1rem;border-radius:50%;font-size:1rem;display:grid;
  place-items:center;text-decoration:none;padding:0}
.acc button.on{background:#e0245e}
.acc button.on::after{content:"\\2665"}
.acc button::after{content:"\\2661"}
form.gate{max-width:26rem;margin:12vh auto;text-align:center}
form.gate input{width:100%;padding:.8rem 1rem;font-size:1rem;border-radius:8px;
  border:1px solid #3a3a3a;background:#1a1a1a;color:#eee;margin:1rem 0 .8rem}
form.gate button{width:100%;padding:.8rem;font-size:1rem;border:0;border-radius:8px;
  background:#eee;color:#111;font-weight:600;cursor:pointer}
form.gate p{opacity:.55;font-size:.85rem}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{text-align:left;padding:.5rem .7rem;border-bottom:1px solid #262626;
  font-size:.92rem;vertical-align:top}
th{opacity:.55;font-weight:500}
code{background:#1c1c1c;padding:.15rem .4rem;border-radius:4px;font-size:.88em}
.acc a.raw{width:auto;padding:0 .5rem;font-size:.66rem;font-weight:700;letter-spacing:.04em}
.link{display:flex;gap:.6rem;align-items:center;background:#1c1c1c;border:1px solid #2f2f2f;
  border-radius:9px;padding:.7rem .9rem;margin:1rem 0;flex-wrap:wrap}
.link input{flex:1;min-width:15rem;background:transparent;border:0;color:#eee;font:inherit;
  font-size:.92rem}
.btn{display:inline-block;background:#eee;color:#111;border:0;border-radius:8px;
  padding:.55rem 1.05rem;font-weight:600;cursor:pointer;text-decoration:none;font-size:.93rem}
.dirs{list-style:none;padding:0;margin:1rem 0;border-top:1px solid #222}
.dirs a{display:block;padding:.65rem .8rem;border-bottom:1px solid #222;
  text-decoration:none;color:#eee}
.dirs a:hover{background:#1a1a1a}
.migas{opacity:.6;font-size:.9rem;margin:.4rem 0 1rem}
.col{margin:2.2rem 0 0}
.col h3{margin:0 0 .2rem;font-size:1.05rem;font-weight:600}
.col .cuantos{opacity:.35;font-size:.83rem;margin:0 0 .7rem}
.evs{list-style:none;padding:0;margin:0;border-top:1px solid #222}
.evs a{display:flex;gap:1rem;align-items:baseline;padding:.6rem .7rem;
  border-bottom:1px solid #222;text-decoration:none;color:#eee}
.evs a:hover{background:#1a1a1a}
.evs .nom{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.evs .fec{opacity:.4;font-size:.85rem;font-variant-numeric:tabular-nums}
.evs .n{opacity:.55;font-size:.85rem;font-variant-numeric:tabular-nums;
  min-width:5.5rem;text-align:right}
.soltar{border:2px dashed #333;border-radius:11px;padding:2.6rem 1.5rem;text-align:center;
  margin:1.2rem 0;transition:border-color .15s,background .15s}
.soltar.encima{border-color:#eee;background:#1a1a1a}
.soltar input{display:none}
.soltar label{cursor:pointer;text-decoration:underline}
#avance{margin:1rem 0;font-size:.93rem;min-height:1.5rem}
#fallos{margin:.5rem 0;font-size:.88rem;color:#ff8a9b}
#fallos div{margin:.2rem 0}
.publicar{background:#1c1c1c;border:1px solid #2f2f2f;border-radius:9px;padding:1rem;
  display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
.publicar input{flex:1;min-width:14rem;padding:.55rem .8rem;border-radius:7px;
  border:1px solid #3a3a3a;background:#141414;color:#eee;font:inherit}
.nav{display:flex;justify-content:space-between;align-items:center;gap:1rem;
  margin:.9rem 0;font-size:.92rem}
.nav a{text-decoration:none;background:#1c1c1c;border:1px solid #2f2f2f;
  border-radius:7px;padding:.45rem .9rem;color:#eee}
.nav span{opacity:.4}
.celda img{cursor:zoom-in}
#visor{position:fixed;inset:0;background:rgba(0,0,0,.94);z-index:50;
  display:grid;place-items:center;user-select:none}
#visor[hidden]{display:none}
#visor img{max-width:100vw;max-height:100vh;object-fit:contain;display:block}
#visor button,#visor a{background:rgba(0,0,0,.55);border:0;color:#fff;cursor:pointer;
  text-decoration:none;border-radius:9px;padding:.45rem .8rem;font-size:1rem}
#visor .cerrar{position:absolute;top:.7rem;right:.9rem;font-size:1.5rem;line-height:1;
  padding:.2rem .7rem}
#visor .ant,#visor .sig{position:absolute;top:50%;transform:translateY(-50%);
  font-size:2.2rem;line-height:1;padding:.2rem .75rem}
#visor .ant{left:.4rem}
#visor .sig{right:.4rem}
.vacc{position:absolute;bottom:1.1rem;left:50%;transform:translateX(-50%);
  display:flex;gap:.6rem;align-items:center}
#vlike::after{content:"\\2661"}
#vlike.on{background:#e0245e}
#vlike.on::after{content:"\\2665"}
#vraw{font-size:.72rem;font-weight:700;letter-spacing:.04em}
.vpos{position:absolute;top:.9rem;left:1.1rem;opacity:.55;font-size:.9rem}
.viz{max-width:1180px;margin:0 auto;padding:0 1.5rem}
.hero{padding:16vh 0 12vh;max-width:44rem}
.marca{font-size:clamp(2.6rem,9vw,4.6rem);font-weight:700;letter-spacing:-.045em;
  line-height:1;margin:0}
.marca span{opacity:.35}
.hero h1{font-size:clamp(1.15rem,3.2vw,1.7rem);font-weight:500;line-height:1.35;
  margin:1.9rem 0 0;letter-spacing:-.015em;max-width:20ch}
.hero p{opacity:.55;margin:1.1rem 0 0;max-width:44ch}
.wsp{display:inline-flex;align-items:center;gap:.55rem;margin-top:2.4rem;
  background:#eee;color:#111;text-decoration:none;font-weight:600;
  padding:.85rem 1.5rem;border-radius:999px;transition:transform .15s}
.wsp:hover{transform:translateY(-1px)}
.wsp:focus-visible{outline:2px solid #eee;outline-offset:3px}
.tira{display:grid;gap:.5rem;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  padding:0 .5rem 1rem}
.tira figure{margin:0;background:#1a1a1a;border-radius:9px;overflow:hidden;
  aspect-ratio:3/2}
.tira img{width:100%;height:100%;object-fit:cover;display:block}
.tira figure:first-child:nth-last-child(odd){grid-column:span 2}
.rotulo{font-size:.74rem;letter-spacing:.16em;text-transform:uppercase;opacity:.3;
  font-weight:600;margin:0 0 1.6rem}
.planes{padding:6rem 0 1rem;display:grid;gap:1rem;
  grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
.plan{background:#161616;border:1px solid #242424;border-radius:13px;padding:1.7rem 1.6rem;
  display:flex;flex-direction:column}
.plan.destaca{border-color:#3d3d3d;background:#1a1a1a}
.plan h3{margin:0;font-size:1.18rem;font-weight:600;letter-spacing:-.015em}
.plan .precio{margin:1.1rem 0 .2rem;font-size:2rem;font-weight:700;letter-spacing:-.03em}
.plan .precio small{font-size:.92rem;font-weight:400;opacity:.45;letter-spacing:0}
.plan .fino{opacity:.45;font-size:.86rem;margin:0}
.plan ul{list-style:none;padding:0;margin:1.4rem 0 0;display:flex;flex-direction:column;
  gap:.6rem;font-size:.94rem;opacity:.72}
.plan li{display:flex;gap:.6rem;align-items:flex-start}
.plan li::before{content:"+";opacity:.35;flex:none;font-weight:600}
.plan .btn{margin-top:auto;padding-top:1.7rem}
.plan .btn a{display:inline-block;background:#eee;color:#111;text-decoration:none;
  font-weight:600;padding:.7rem 1.3rem;border-radius:999px;font-size:.93rem}
.plan.simple .btn a{background:transparent;color:#eee;border:1px solid #3a3a3a}
.mi{padding:6.5rem 0 1rem;display:grid;gap:2.5rem;grid-template-columns:1fr 1fr;
  align-items:start;max-width:56rem}
.mi h2{font-size:1.6rem;font-weight:600;letter-spacing:-.02em;margin:0}
.mi p{opacity:.62;margin:0 0 1rem;font-size:.99rem}
.mi p:last-child{margin:0}
@media (max-width:720px){.mi{grid-template-columns:1fr;gap:1.4rem;padding:4.5rem 0 1rem}}
.como{padding:7rem 0 8rem;display:grid;gap:2.6rem;
  grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.como h2{grid-column:1/-1;font-size:1.6rem;font-weight:600;letter-spacing:-.02em;
  margin:0 0 -.8rem}
.paso b{display:block;font-size:.76rem;letter-spacing:.14em;text-transform:uppercase;
  opacity:.35;margin-bottom:.6rem;font-weight:600}
.paso p{margin:0;opacity:.62;font-size:.96rem}
.pie{border-top:1px solid #262626;padding:2.5rem 0 4rem;opacity:.4;font-size:.88rem;
  display:flex;gap:1.2rem;flex-wrap:wrap}
@media (max-width:640px){
  .hero{padding:9vh 0 7vh}
  .tira figure:first-child:nth-last-child(odd){grid-column:auto}
  .como{padding:4.5rem 0 5rem;gap:2rem}
}
.tapa{position:fixed;inset:0;z-index:-1;overflow:hidden}
.tapa img{width:100%;height:100%;object-fit:cover;opacity:.45}
.tapa::after{content:"";position:absolute;inset:0;
  background:linear-gradient(rgba(17,17,17,.3),rgba(17,17,17,.92))}
.bajar{display:flex;gap:.6rem;flex-wrap:wrap;margin:0 0 1.1rem}
.tapas{display:flex;gap:.4rem;flex-wrap:wrap;margin:.5rem 0 1rem}
.tapas label{cursor:pointer;line-height:0}
.tapas input{position:absolute;opacity:0;pointer-events:none}
.tapas img,.tapas .nada{width:98px;height:66px;object-fit:cover;border-radius:7px;
  border:3px solid transparent;display:grid;place-items:center;background:#1c1c1c}
.tapas .nada{font-size:.68rem;line-height:1.15;text-align:center;opacity:.55;color:#eee}
.tapas input:checked ~ img,.tapas input:checked ~ .nada{border-color:#eee}
.aviso{color:#ff8a9b;font-size:.9rem}
@media (hover:none){.acc{opacity:1}}
</style>{{ cuerpo|safe }}</html>"""

PORTADA = """<div class="viz"><section class="hero">
<p class="marca">rb<span>.</span>fotos</p>
<h1>Fotografia de deportes y eventos.</h1>
<p>Ligas, fechas deportivas, actos y presentaciones. Cubro el partido entero y
te lo entrego en una galeria privada, no en un pendrive con cuatro mil archivos.</p>
{% if wsp %}<a class="wsp" href="https://wa.me/{{ wsp }}"
  rel="noopener">Escribime por WhatsApp &rarr;</a>{% endif %}
</section></div>

{% if fotos %}<div class="viz"><p class="rotulo" id="trabajos">Trabajos</p></div>
<div class="tira">
{% for f in fotos %}<figure><img src="/muestra/{{ f|urlencode }}" alt=""
  loading="{{ 'eager' if loop.index <= 2 else 'lazy' }}" draggable="false"></figure>
{% endfor %}</div>{% endif %}

<div class="viz"><section class="planes" id="servicios">
<p class="rotulo" style="grid-column:1/-1;margin:0">Dos formas de trabajar conmigo</p>

<div class="plan destaca">
<h3>Ligas y fechas deportivas</h3>
<p class="precio">{{ precio }} <small>por foto</small></p>
<p class="fino">Compra minima {{ minimo }} &middot; {{ minimo_fotos }} fotos</p>
<ul>
<li>Voy a la fecha y disparo el partido entero</li>
<li>Subo todo, no una seleccion mia</li>
<li>Entras, miras y eliges solo las tuyas</li>
<li>Las pagas y te las llevas en calidad completa</li>
</ul>
<p class="btn">{% if wsp %}<a href="https://wa.me/{{ wsp }}?text={{
  'Hola Renzo, en que fecha vas a estar?'|urlencode }}" rel="noopener">Preguntar por una fecha</a>{% endif %}</p>
</div>

<div class="plan simple">
<h3>Eventos y encargos</h3>
<p class="precio">A convenir</p>
<p class="fino">Segun duracion y tipo de evento</p>
<ul>
<li>Actos, ceremonias y presentaciones</li>
<li>Cobertura completa de la jornada</li>
<li>Te entrego la galeria entera, sin elegir de a una</li>
<li>Con los RAW si los necesitas</li>
</ul>
<p class="btn">{% if wsp %}<a href="https://wa.me/{{ wsp }}?text={{
  'Hola Renzo, quiero cotizar un evento'|urlencode }}" rel="noopener">Pedir presupuesto</a>{% endif %}</p>
</div>
</section>

<section class="mi" id="sobre-mi">
<h2>Sobre mi</h2>
<div>
<p>Soy Renzo. Fotografio deporte amateur y eventos: ligas universitarias, fechas de futbol,
actos y presentaciones.</p>
<p>Trabajo con volumen. Una fecha son 300 fotos en veinte minutos, y de ahi salen las diez que
te importan a vos. Por eso arme mi propio sistema de entrega en vez de mandar carpetas de Drive
que nadie termina de mirar: cada evento queda en una galeria privada donde vos marcas las tuyas
y te las llevas.</p>
<p>Y las fotos no viven en un servicio de terceros que manana cambia de precio o cierra: estan
en un servidor mio.</p>
</div>
</section>

<section class="como">
<h2>Como recibis tus fotos</h2>
<div class="paso"><b>Un link privado</b>
<p>Termina la fecha y te paso un link con todas las fotos del dia. No se
indexa en Google y no lo ve nadie a quien no se lo pases.</p></div>
<div class="paso"><b>Marcas las tuyas</b>
<p>Entras con tu email y vas marcando con el corazon las que queres. Yo veo esa
seleccion del otro lado, asi no hay que ir y venir con numeros de archivo.</p></div>
<div class="paso"><b>Te las llevas</b>
<p>Descargas en calidad completa, de a una o todas juntas en un ZIP. Si hay RAW
al lado, tambien.</p></div>
</section>

<footer class="pie"><span>rb.fotos</span><span>Fotografia deportiva y de
eventos</span><span>Chile</span></footer></div>"""

GATE = """<header><b>rb.fotos</b> <span>{{ gal['nombre'] }}</span></header>
{% if gal['portada_foto_id'] %}<div class="tapa">
<img src="/portada/{{ gal['slug'] }}" alt=""></div>{% endif %}
<main><form class="gate" method="post" action="/g/{{ gal['slug'] }}/entrar">
<h2 style="margin:0">{{ gal['nombre'] }}</h2>
<p>Dejame tu email y entras a ver la galeria.</p>
{% if error %}<p class="aviso">{{ error }}</p>{% endif %}
<input type="email" name="email" placeholder="tu@email.com" required autofocus>
{% if gal['clave_hash'] %}<input type="password" name="clave" placeholder="Contrasena"
  required autocomplete="current-password">{% endif %}
<button>Ver las fotos</button>
<p>Lo uso para saber quien marco cada foto. Nada mas.</p>
</form></main>"""

EXPIRADA = """<header><b>rb.fotos</b> <span>{{ gal['nombre'] }}</span></header>
<main><div class="gate" style="text-align:center">
<h2 style="margin:0">{{ gal['nombre'] }}</h2>
<p style="opacity:.6">Esta galeria vencio y ya no esta disponible.<br>
Si la necesitas de nuevo, escribime y la vuelvo a abrir.</p>
</div></main>"""

NAV = """{% if paginas > 1 %}<nav class="nav">
{% if p > 1 %}<a href="?p={{ p - 1 }}">&larr; Anterior</a>{% else %}<span>&larr; Anterior</span>{% endif %}
<span>Pagina {{ p }} de {{ paginas }}</span>
{% if p < paginas %}<a href="?p={{ p + 1 }}">Siguiente &rarr;</a>{% else %}<span>Siguiente &rarr;</span>{% endif %}
</nav>{% endif %}"""

GRILLA = """<header><b>rb.fotos</b> <span>{{ gal['nombre'] }} &middot;
{{ total }} fotos &middot; marcaste <b id="cuenta">{{ marcadas }}</b></span></header>
<main><p class="bajar">
<a class="btn" href="/g/{{ gal['slug'] }}/zip">Descargar todo ({{ total }})</a>
<a class="btn" id="belegidas" href="/g/{{ gal['slug'] }}/zip/elegidas"
  {{ 'hidden' if not marcadas }}>Descargar mis elegidas ({{ marcadas }})</a>
</p>""" + NAV + """<div class="grid">
{% for f in fotos %}<figure class="celda" style="margin:0">
<img src="/p/{{ f['id'] }}" loading="lazy" alt="" draggable="false">
<div class="acc">
<button class="{{ 'on' if f['mia'] }}" data-id="{{ f['id'] }}" title="Me gusta"></button>
<a href="/d/{{ f['id'] }}" title="Descargar JPG original">&#8595;</a>
{% if f['tiene_raw'] %}<a class="raw" href="/d/{{ f['id'] }}/raw" title="Descargar RAW">RAW</a>{% endif %}
</div></figure>{% endfor %}
</div>""" + NAV + """</main>

<div id="visor" hidden>
  <span class="vpos" id="vpos"></span>
  <button class="cerrar" id="vcerrar" title="Cerrar (Esc)">&times;</button>
  <button class="ant" id="vant" title="Anterior">&lsaquo;</button>
  <img id="vimg" alt="" draggable="false">
  <button class="sig" id="vsig" title="Siguiente">&rsaquo;</button>
  <div class="vacc">
    <button id="vlike" title="Me gusta"></button>
    <a id="vdl" title="Descargar JPG">&#8595;</a>
    <a id="vraw" title="Descargar RAW">RAW</a>
  </div>
</div>

<script>
const cuenta = document.getElementById("cuenta");
const figs = [...document.querySelectorAll(".celda")];
const boton = f => f.querySelector(".acc button");

async function marcar(id) {
  const r = await fetch("/api/megusta", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({foto_id: +id})});
  if (!r.ok) return null;
  const mia = (await r.json()).mia;
  cuenta.textContent = +cuenta.textContent + (mia ? 1 : -1);
  const bel = document.getElementById("belegidas");
  if (bel) {
    const n = +cuenta.textContent;
    bel.hidden = !n;
    bel.textContent = "Descargar mis elegidas (" + n + ")";
  }
  return mia;
}

figs.forEach((fig, i) => {
  const b = boton(fig);
  b.onclick = async e => {
    e.stopPropagation();
    const m = await marcar(b.dataset.id);
    if (m !== null) b.classList.toggle("on", m);
  };
  fig.querySelector("img").onclick = () => abrir(i);
});

let idx = -1;
function abrir(i) {
  if (!figs.length) return;
  idx = (i + figs.length) % figs.length;
  const fig = figs[idx], id = boton(fig).dataset.id;
  vimg.src = "/p/" + id;
  vdl.href = "/d/" + id;
  const raw = fig.querySelector("a.raw");
  vraw.hidden = !raw;
  if (raw) vraw.href = raw.href;
  vlike.classList.toggle("on", boton(fig).classList.contains("on"));
  vpos.textContent = (idx + 1) + " / " + figs.length;
  visor.hidden = false;
  document.body.style.overflow = "hidden";
  // precarga los vecinos: pasar de foto no deberia esperar a la red
  [idx - 1, idx + 1].forEach(j => {
    const v = figs[(j + figs.length) % figs.length];
    if (v && v !== fig) new Image().src = "/p/" + boton(v).dataset.id;
  });
}
function cerrar() {
  visor.hidden = true;
  vimg.src = "";
  document.body.style.overflow = "";
}

vcerrar.onclick = cerrar;
vant.onclick = () => abrir(idx - 1);
vsig.onclick = () => abrir(idx + 1);
visor.onclick = e => { if (e.target === visor) cerrar(); };
vlike.onclick = async () => {
  const b = boton(figs[idx]);
  const m = await marcar(b.dataset.id);
  if (m === null) return;
  b.classList.toggle("on", m);
  vlike.classList.toggle("on", m);
};

document.addEventListener("keydown", e => {
  if (visor.hidden) return;
  if (e.key === "Escape") cerrar();
  else if (e.key === "ArrowLeft") abrir(idx - 1);
  else if (e.key === "ArrowRight") abrir(idx + 1);
  else if (e.key === " ") { e.preventDefault(); vlike.onclick(); }
});

let x0 = null;
visor.addEventListener("touchstart", e => x0 = e.touches[0].clientX, {passive: true});
visor.addEventListener("touchend", e => {
  if (x0 === null) return;
  const dx = e.changedTouches[0].clientX - x0;
  x0 = null;
  if (Math.abs(dx) > 50) abrir(idx + (dx < 0 ? 1 : -1));
}, {passive: true});
</script>"""

PANEL_INDICE = """<header><b>rb.fotos</b> <span>panel</span></header>
<main><p><a class="btn" href="/panel/nueva">+ Nueva galeria</a>
<a class="btn" style="background:transparent;color:#eee;border:1px solid #3a3a3a;
  margin-left:.5rem" href="/panel/subir">&uarr; Subir fotos</a>
<a class="btn" style="background:transparent;color:#eee;border:1px solid #3a3a3a;
  margin-left:.5rem" href="/panel/eventos">Eventos del curador</a></p>
<table><tr><th>Galeria</th><th>Fotos</th><th>Clientes</th><th>Vigencia</th></tr>
{% for g in gals %}<tr>
<td><a href="/panel/{{ g['slug'] }}">{{ g['nombre'] }}</a></td>
<td>{{ g['fotos'] }}</td><td>{{ g['clientes'] }}</td>
<td>{% if not g['expira'] %}<span style="opacity:.4">sin limite</span>
{% elif g['expira'] < hoy %}<span class="aviso">vencida</span>
{% else %}hasta {{ g['expira'] }}{% endif %}</td></tr>
{% else %}<tr><td colspan="4">Todavia no publicaste ninguna.</td></tr>{% endfor %}
</table></main>"""

PANEL_NUEVA = """<header><b>rb.fotos</b> <span>nueva galeria</span></header>
<main>
<p class="migas"><a href="/panel/nueva">/mnt/fotos</a>
{% for nom, ruta in migas %} / <a href="/panel/nueva?dir={{ ruta|urlencode }}">{{ nom }}</a>{% endfor %}</p>

{% if cuantas %}
<form class="publicar" method="post" action="/panel/nueva">
<input type="hidden" name="dir" value="{{ rel }}">
<input name="nombre" placeholder="Nombre de la galeria" required autofocus>
<button class="btn">Publicar {{ cuantas }} fotos</button>
</form>
{% else %}
<p style="opacity:.55">No hay fotos sueltas en esta carpeta. Entra a alguna de abajo.</p>
{% endif %}

<ul class="dirs">{% for d in subdirs %}
<li><a href="/panel/nueva?dir={{ (rel ~ '/' ~ d).lstrip('/')|urlencode }}">{{ d }}/</a></li>
{% else %}<li style="opacity:.55;padding:.65rem .8rem">Sin subcarpetas.</li>{% endfor %}</ul>
</main>"""

SIN_CATALOGO = """<header><b>rb.fotos</b> <span>eventos</span></header>
<main><p>Todavia no hay catalogo en la Pi.</p>
<p style="opacity:.55">Se genera en la notebook, desde photos_v2.db, y se manda por
Tailscale:</p>
<p><code>python exportar_catalogo.py --enviar</code></p>
<p><a href="/panel">Volver al panel</a></p></main>"""

PANEL_EVENTOS = """<header><b>rb.fotos</b> <span>eventos del curador</span></header>
<main>
<p class="migas">{{ total }} eventos con fotos, agrupados por coleccion.
Salen de <code>catalogo.db</code>, que es una foto fija de photos_v2.db —
para actualizarlo, corre <code>exportar_catalogo.py --enviar</code> en la notebook.</p>
{% for col, evs in grupos.items() %}
<div class="col">
<h3>{{ col }}</h3>
<p class="cuantos">{{ evs|length }} evento{{ 's' if evs|length != 1 }} &middot;
{{ evs|sum(attribute='fotos') }} fotos</p>
<ul class="evs">
{% for e in evs %}<li><a href="/panel/eventos/{{ e['id'] }}">
<span class="fec">{{ (e['inicio'] or '')[:10] }}</span>
<span class="nom">{{ e['nombre'] or '(sin nombre)' }}</span>
<span class="n">{{ e['fotos'] }} fotos</span></a></li>{% endfor %}
</ul></div>
{% endfor %}
</main>"""

PANEL_EVENTO = """<header><b>rb.fotos</b> <span>{{ ev['nombre'] }}</span></header>
<main>
<p class="migas"><a href="/panel/eventos">&larr; eventos</a>
{% if ev['coleccion'] %} / {{ ev['coleccion'] }}{% endif %}
&middot; {{ (ev['inicio'] or '')[:10] }} &middot; {{ ev['fotos'] }} fotos</p>

<form class="publicar" method="post" action="/panel/eventos/{{ ev['id'] }}/publicar">
<input name="nombre" value="{{ ev['nombre'] or '' }}" placeholder="Nombre de la galeria">
<button class="btn">Publicar las {{ ev['fotos'] }} como galeria</button>
</form>

{% if paginas > 1 %}<nav class="nav">
{% if p > 1 %}<a href="?p={{ p - 1 }}">&larr; Anterior</a>{% else %}<span>&larr; Anterior</span>{% endif %}
<span>Pagina {{ p }} de {{ paginas }}</span>
{% if p < paginas %}<a href="?p={{ p + 1 }}">Siguiente &rarr;</a>{% else %}<span>Siguiente &rarr;</span>{% endif %}
</nav>{% endif %}

<div class="grid" style="margin-top:1rem">
{% for f in fotos %}<figure class="celda" style="margin:0">
<img src="/panel/cat/{{ f['id'] }}" loading="lazy" alt="" draggable="false">
</figure>{% endfor %}
</div>
</main>"""

PANEL_SUBIR = """<header><b>rb.fotos</b> <span>subir fotos</span></header>
<main>
<p style="opacity:.55;font-size:.92rem;margin:0">Las fotos van a
<code>_subidas/&lt;carpeta&gt;</code> en el disco grande. Cuando terminen de subir te llevo
al publicador de siempre.</p>

<div class="publicar" style="margin-top:1.2rem">
<input id="carpeta" placeholder="Nombre de la carpeta (ej: liga-uandes-20-08)" autofocus>
</div>

<div class="soltar" id="zona">
<p style="margin:0 0 .5rem">Solta las fotos aca</p>
<label for="archivos">o eleg&iacute;las del disco</label>
<input type="file" id="archivos" multiple accept="image/jpeg,image/png,image/webp,image/tiff">
</div>

<div id="avance"></div>
<div id="fallos"></div>
<p><button class="btn" id="mandar" disabled>Subir</button>
<a class="btn" style="background:transparent;color:#eee;border:1px solid #3a3a3a"
  href="/panel">Volver</a></p>

<script>
const zona = document.getElementById("zona");
const input = document.getElementById("archivos");
const carpeta = document.getElementById("carpeta");
const avance = document.getElementById("avance");
const fallos = document.getElementById("fallos");
const mandar = document.getElementById("mandar");
let cola = [];

function listo() {
  mandar.disabled = !(cola.length && carpeta.value.trim());
  avance.textContent = cola.length ? cola.length + " fotos elegidas" : "";
}
carpeta.oninput = listo;
input.onchange = () => { cola = [...input.files]; listo(); };

["dragenter", "dragover"].forEach(e => zona.addEventListener(e, ev => {
  ev.preventDefault(); zona.classList.add("encima");
}));
["dragleave", "drop"].forEach(e => zona.addEventListener(e, ev => {
  ev.preventDefault(); zona.classList.remove("encima");
}));
zona.addEventListener("drop", ev => {
  cola = [...ev.dataTransfer.files]; listo();
});

mandar.onclick = async () => {
  mandar.disabled = true;
  carpeta.disabled = true;
  fallos.textContent = "";
  const dir = carpeta.value.trim();
  let ok = 0, mal = 0;
  // de a una: un POST con la fecha entera se pasa del limite del proxy
  for (let i = 0; i < cola.length; i++) {
    avance.textContent = `Subiendo ${i + 1} de ${cola.length}...  (${ok} listas)`;
    const d = new FormData();
    d.append("carpeta", dir);
    d.append("archivo", cola[i]);
    try {
      const r = await fetch("/panel/subir", {method: "POST", body: d});
      if (r.ok) { ok++; } else {
        mal++;
        const j = await r.json().catch(() => ({}));
        const l = document.createElement("div");
        l.textContent = j.error || (cola[i].name + ": fallo");
        fallos.appendChild(l);
      }
    } catch (e) {
      mal++;
      const l = document.createElement("div");
      l.textContent = cola[i].name + ": se corto la conexion";
      fallos.appendChild(l);
    }
  }
  avance.textContent = `Listo: ${ok} subidas` + (mal ? `, ${mal} fallaron` : "");
  if (ok) {
    avance.textContent += " — te llevo al publicador...";
    setTimeout(() => location.href = "/panel/nueva?dir=" +
      encodeURIComponent("_subidas/" + dir), 1200);
  } else {
    mandar.disabled = false;
    carpeta.disabled = false;
  }
};
</script>
</main>"""

PANEL_GAL = """<header><b>rb.fotos</b> <span>{{ gal['nombre'] }}</span></header>
<main>
<p style="opacity:.55;font-size:.9rem;margin:0">Link para mandarle al cliente</p>
<div class="link">
<input id="lnk" readonly value="{{ request.url_root }}g/{{ gal['slug'] }}">
<button class="btn" onclick="
  const i=document.getElementById('lnk'); i.select();
  navigator.clipboard.writeText(i.value).then(()=>this.textContent='Copiado');
">Copiar</button>
</div>
<p style="opacity:.55;font-size:.9rem">{{ con_raw }} de {{ total }} fotos tienen RAW al lado.
&middot; <a href="/g/{{ gal['slug'] }}">ver como cliente</a></p>
{% if gal['expira'] and gal['expira'] < hoy %}
<p class="aviso">Esta galeria vencio el {{ gal['expira'] }}: el cliente ya no la ve.</p>
{% endif %}

<h3>Ajustes</h3>
<form method="post" action="/panel/{{ gal['slug'] }}/ajustes">
<div class="publicar">
<input name="clave" type="text" autocomplete="off" placeholder="{{
  'Tiene contrasena — escribi otra para cambiarla' if gal['clave_hash']
  else 'Sin contrasena — escribi una para ponerla' }}">
{% if gal['clave_hash'] %}<label style="font-size:.85rem;opacity:.7">
<input type="checkbox" name="sacar_clave" value="1"> sacarla</label>{% endif %}
<label style="font-size:.85rem;opacity:.7">Vence el
<input type="date" name="expira" value="{{ gal['expira'] or '' }}"></label>
<button class="btn">Guardar</button>
</div>
<p style="opacity:.55;font-size:.85rem;margin:.9rem 0 0">Portada</p>
<div class="tapas">
<label><input type="radio" name="portada" value=""
  {{ 'checked' if not gal['portada_foto_id'] }}><span class="nada">sin<br>portada</span></label>
{% for f in primeras %}<label><input type="radio" name="portada" value="{{ f['id'] }}"
  {{ 'checked' if f['id'] == gal['portada_foto_id'] }}><img src="/panel/p/{{ f['id'] }}"
  loading="lazy" alt=""></label>{% endfor %}
</div>
</form>

<h3>Quien entro</h3>
<table><tr><th>Email</th><th>Ultima visita</th><th>Marcadas</th></tr>
{% for c in clientes %}<tr><td>{{ c['email'] }}</td><td>{{ c['visto'] }}</td>
<td>{{ c['marcadas'] }}</td></tr>{% else %}<tr><td colspan="3">Nadie todavia.</td></tr>
{% endfor %}</table>
<h3>Fotos elegidas
<a class="btn" style="font-size:.78rem;padding:.28rem .7rem;margin-left:.5rem"
  href="/panel/{{ gal['slug'] }}/seleccion.csv">Bajar CSV</a></h3>
<table><tr><th>Archivo</th><th>Votos</th><th>Quien</th></tr>
{% for f in elegidas %}<tr><td><code>{{ nombre(f['ruta']) }}</code></td>
<td>{{ f['votos'] }}</td><td>{{ f['quienes'] }}</td></tr>
{% else %}<tr><td colspan="3">Sin marcas todavia.</td></tr>{% endfor %}
</table>
<form method="post" action="/panel/{{ gal['slug'] }}/borrar" style="margin:2.5rem 0 1rem"
  onsubmit="return confirm('Borrar la galeria y los me-gusta de los clientes?\\nLas fotos del disco no se tocan.')">
<button class="btn" style="background:#2a1417;color:#ff8a9b;border:1px solid #4a1f26">
Borrar esta galeria</button>
</form></main>"""


# ------------------------------------------------------------------------ CLI

def crear_galeria(cx, nombre, rutas, verificar=True):
    """Alta de galeria. La usan igual la consola y el panel web.

    verificar=False lo usa la publicacion desde el catalogo: esas rutas ya
    pasaron por el pipeline y vienen filtradas por danado=0, y abrir 7 mil
    JPEG para revalidarlos son minutos de disco al pedo.
    """
    slug = secrets.token_urlsafe(12)
    cx.execute("INSERT INTO galeria (slug, nombre, creada) VALUES (?, ?, ?)",
               (slug, nombre, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    gid = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()["id"]

    puestas, faltan = 0, []
    for i, r in enumerate(rutas):
        ruta = traducir_ruta(r)
        if not dentro_de_raiz(ruta) or not os.path.exists(ruta):
            faltan.append(r)
            continue
        if verificar and not abrible(ruta):
            faltan.append(r)
            continue
        cx.execute("INSERT INTO foto (galeria_id, ruta, raw, orden) VALUES (?, ?, ?, ?)",
                   (gid, ruta, buscar_raw(ruta) if verificar else None, i))
        puestas += 1
    cx.commit()
    return slug, puestas, faltan


def publicar(nombre, lista):
    rutas = [l.strip() for l in Path(lista).read_text(encoding="utf-8").splitlines()
             if l.strip()]
    if not rutas:
        sys.exit("La lista esta vacia.")

    cx = conectar()
    slug, puestas, faltan = crear_galeria(cx, nombre, rutas)
    cx.close()

    print(f"Galeria '{nombre}': {puestas} fotos")
    if faltan:
        print(f"  {len(faltan)} no existen, salteadas. Primeras:")
        for r in faltan[:5]:
            print(f"    {r}")
    print(f"\n  https://rbfotos.studio/g/{slug}\n")


def calentar(slug):
    """Genera las previews de una galeria antes de mandar el link.

    ponytail: secuencial y a mano. Son ~0.5s por foto y se corre una vez por
    galeria; un pool de workers no vale el codigo hasta que moleste.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    cx = conectar()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        sys.exit(f"No existe la galeria {slug}")
    fotos = cx.execute("SELECT id, ruta FROM foto WHERE galeria_id = ? ORDER BY orden",
                       (gal["id"],)).fetchall()
    cx.close()

    t0 = time.perf_counter()
    fallaron = 0
    for i, f in enumerate(fotos, 1):
        try:
            preview(f["id"], f["ruta"])
        except Exception as e:
            fallaron += 1
            print(f"\n  falla en {f['ruta']}: {e}")
        print(f"\r  {i}/{len(fotos)}  {time.perf_counter() - t0:.0f}s", end="", flush=True)
    print(f"\nListo en {time.perf_counter() - t0:.0f}s"
          + (f", {fallaron} fallaron" if fallaron else ""))


def test():
    """ponytail: un solo check que falla si se rompe algo que importa."""
    import tempfile
    global DB, CACHE, RAIZ_FOTOS
    tmp = Path(tempfile.mkdtemp())
    DB, CACHE = tmp / "t.db", tmp / "cache"

    # traduccion de rutas: los tres prefijos que aparecen en la practica
    assert traducir_ruta(r"K:\Eventos\a.jpg") == "/mnt/fotos/Eventos/a.jpg"
    assert traducir_ruta("k:/Eventos/a.jpg") == "/mnt/fotos/Eventos/a.jpg"
    assert traducir_ruta("/mnt/fotos/Eventos/a.jpg") == "/mnt/fotos/Eventos/a.jpg"
    assert traducir_ruta("Eventos/a.jpg") == "/mnt/fotos/Eventos/a.jpg"

    # nadie sale de /mnt/fotos
    assert dentro_de_raiz("/mnt/fotos/x.jpg")
    assert not dentro_de_raiz("/etc/shadow")
    assert not dentro_de_raiz("/mnt/fotos/../etc/shadow")

    # marca de agua: sale un JPEG y los pixeles cambian de verdad
    orig = tmp / "orig.jpg"
    Image.new("RGB", (2400, 1600), (90, 110, 130)).save(orig)
    dest = tmp / "m.jpg"
    marcar(orig, dest)
    assert dest.exists()
    im = Image.open(dest)
    assert im.width == ANCHO_PREVIEW, im.size
    assert len(im.convert("RGB").getcolors(maxcolors=100000)) > 1, "no marco nada"

    # control de acceso: cliente de la galeria A no ve fotos de la B
    cx = conectar()
    for s, n in (("aaa", "A"), ("bbb", "B")):
        cx.execute("INSERT INTO galeria (slug, nombre, creada) VALUES (?,?,?)",
                   (s, n, "2026-01-01"))
    cx.execute("INSERT INTO foto (galeria_id, ruta, orden) VALUES (1,'/mnt/fotos/a.jpg',0)")
    cx.execute("INSERT INTO foto (galeria_id, ruta, orden) VALUES (2,'/mnt/fotos/b.jpg',0)")
    cx.commit()
    cx.close()

    # extension de foto no alcanza: tiene que abrirse de verdad
    basura = tmp / "basura.jpg"
    basura.write_bytes(b"no soy un jpeg")
    assert es_visible(basura) and not abrible(basura), "basura .jpg pasaria el filtro"
    assert abrible(orig)

    # RAW hermano: solo si esta al lado, mayusculas incluidas
    (tmp / "foto.jpg").write_bytes(b"x")
    assert buscar_raw(tmp / "foto.jpg") is None
    (tmp / "foto.ARW").write_bytes(b"x")
    assert buscar_raw(tmp / "foto.jpg") == str(tmp / "foto.ARW")

    app.config.update(TESTING=True)
    with app.test_client() as c:
        assert c.get("/p/1").status_code == 404, "sin entrar no se ve nada"
        c.post("/g/aaa/entrar", data={"email": "x@y.com"})
        assert c.get("/p/2").status_code == 404, "FUGA: vio foto de otra galeria"
        assert c.get("/d/2").status_code == 404, "FUGA: descargo de otra galeria"
        assert c.get("/d/2/raw").status_code == 404, "FUGA: RAW de otra galeria"
        assert c.get("/p/1").status_code == 404  # la ruta no existe en disco
        assert c.post("/g/aaa/entrar", data={"email": "no-es-mail"}).status_code == 302
        # paginado: un ?p= hostil no puede dar OFFSET negativo ni reventar
        for p in ("999", "abc", "-5", "0", ""):
            assert c.get(f"/g/aaa?p={p}").status_code == 200, f"?p={p} rompio"
        # el navegador de carpetas no sale de la raiz (Caddy protege /panel*,
        # pero el guard de rutas tiene que valer igual)
        assert c.get("/panel/nueva?dir=../etc").status_code == 404
        assert c.get("/panel/nueva?dir=/etc").status_code == 404

    # ------------------ clave, expiracion, portada, zip y csv de seleccion
    RAIZ_FOTOS = tmp  # de aca en adelante las fotos de prueba son reales

    h = hash_clave("secreta")
    assert "secreta" not in h, "guardo la clave en texto plano"
    assert clave_ok(h, "secreta") and not clave_ok(h, "Secreta")
    assert not clave_ok("basura", "secreta") and not clave_ok(None, "x")

    assert not expirada({"expira": None})
    assert expirada({"expira": "2020-01-01"})
    assert not expirada({"expira": "2099-12-31"})
    assert not expirada({"expira": "no-es-fecha"}), "fecha rota no puede cerrar la galeria"

    reales = []
    for n in ("uno.jpg", "dos.jpg", "con,coma.jpg"):
        f = tmp / n
        Image.new("RGB", (800, 600), (40, 80, 120)).save(f)
        reales.append(str(f))

    cx = conectar()
    cx.execute("INSERT INTO galeria (slug, nombre, creada, clave_hash) VALUES (?,?,?,?)",
               ("ccc", "C", "2026-01-01", h))
    gid = cx.execute("SELECT id FROM galeria WHERE slug='ccc'").fetchone()["id"]
    for i, r in enumerate(reales):
        cx.execute("INSERT INTO foto (galeria_id, ruta, orden) VALUES (?,?,?)", (gid, r, i))
    id_coma = cx.execute("SELECT id FROM foto WHERE galeria_id = ? AND ruta = ?",
                         (gid, reales[2])).fetchone()["id"]
    id_uno = cx.execute("SELECT id FROM foto WHERE galeria_id = ? AND ruta = ?",
                        (gid, reales[0])).fetchone()["id"]
    cx.execute("INSERT INTO galeria (slug, nombre, creada, expira) VALUES (?,?,?,?)",
               ("ddd", "D", "2026-01-01", "2020-01-01"))
    did = cx.execute("SELECT id FROM galeria WHERE slug='ddd'").fetchone()["id"]
    cx.execute("INSERT INTO foto (galeria_id, ruta, orden) VALUES (?,?,0)", (did, reales[0]))
    id_venc = cx.execute("SELECT id FROM foto WHERE galeria_id = ?", (did,)).fetchone()["id"]
    cx.commit()
    cx.close()

    with app.test_client() as c:
        # con clave puesta, la equivocada no abre nada
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "mala"})
        assert c.get("/g/ccc/zip").status_code == 404, "FUGA: entro con la clave equivocada"
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "secreta"})
        r = c.get("/g/ccc/zip")
        assert r.status_code == 200, "el zip no salio"
        z = zipfile.ZipFile(io.BytesIO(r.get_data()))
        assert z.testzip() is None, "zip corrupto"
        assert len(z.namelist()) == 3, z.namelist()
        assert z.read("uno.jpg")[:2] == b"\xff\xd8", "el zip no trae el JPEG entero"
        assert z.read("uno.jpg") == Path(reales[0]).read_bytes(), "el zip altero el original"
        # elegidas sigue los me-gusta de ESE cliente
        assert zipfile.ZipFile(io.BytesIO(
            c.get("/g/ccc/zip/elegidas").get_data())).namelist() == []
        c.post("/api/megusta", json={"foto_id": id_coma})
        assert zipfile.ZipFile(io.BytesIO(
            c.get("/g/ccc/zip/elegidas").get_data())).namelist() == ["con,coma.jpg"]
        # el csv sale con el encabezado exacto y escapa la coma de la ruta
        cs = c.get("/panel/ccc/seleccion.csv").get_data(as_text=True).splitlines()
        assert cs[0] == "ruta,nombre,votos,emails", cs[0]
        assert cs[1].startswith('"'), f"no escapo la ruta con coma: {cs[1]}"

    with app.test_client() as c:
        # galeria vencida: no se ve por ninguna puerta, ni habiendo entrado
        c.post("/g/ddd/entrar", data={"email": "x@y.com"})
        assert c.get(f"/p/{id_venc}").status_code == 404, "FUGA: preview de galeria vencida"
        assert c.get(f"/d/{id_venc}").status_code == 404, "FUGA: descarga de galeria vencida"
        assert c.get("/g/ddd/zip").status_code == 404, "FUGA: zip de galeria vencida"
        assert "vencio" in c.get("/g/ddd").get_data(as_text=True).lower()

    # la tapa se ve sin sesion, pero solo si es una foto de esa misma galeria
    cx = conectar()
    cx.execute("UPDATE galeria SET portada_foto_id = ? WHERE id = ?", (id_venc, gid))
    cx.commit()
    with app.test_client() as c:
        assert c.get("/portada/ccc").status_code == 404, "FUGA: tapa de otra galeria"
    cx.execute("UPDATE galeria SET portada_foto_id = ? WHERE id = ?", (id_uno, gid))
    cx.execute("UPDATE galeria SET portada_foto_id = ? WHERE id = ?", (id_venc, did))
    cx.commit()
    cx.close()
    with app.test_client() as c:
        assert c.get("/portada/ccc").status_code == 200, "la tapa tiene que verse sin entrar"
        assert c.get("/portada/ddd").status_code == 404, "FUGA: tapa de galeria vencida"

    # ------------------------------------------------- subida de fotos
    assert carpeta_segura("../../etc") == "etc"
    assert carpeta_segura("liga uandes 20-08") == "liga uandes 20-08"
    assert carpeta_segura("  ") == "" and carpeta_segura(None) == ""

    with app.test_client() as c:
        # sin carpeta no se sube nada
        assert c.post("/panel/subir", data={"carpeta": ""}).status_code == 400

        buena = io.BytesIO()
        Image.new("RGB", (60, 40), (10, 90, 60)).save(buena, "JPEG")
        buena.seek(0)
        r = c.post("/panel/subir", data={
            "carpeta": "prueba", "archivo": (buena, "foto real.jpg")})
        assert r.status_code == 200, r.get_data(as_text=True)
        guardada = subidas_dir() / "prueba" / "foto_real.jpg"
        assert guardada.exists(), list((subidas_dir() / "prueba").iterdir())

        # un .jpg que no es jpg no puede quedarse en el disco
        r = c.post("/panel/subir", data={
            "carpeta": "prueba", "archivo": (io.BytesIO(b"no soy jpeg"), "trucho.jpg")})
        assert r.status_code == 400
        assert not (subidas_dir() / "prueba" / "trucho.jpg").exists(), "quedo basura"

        # una extension que no es de imagen tampoco entra
        r = c.post("/panel/subir", data={
            "carpeta": "prueba", "archivo": (io.BytesIO(b"x"), "script.sh")})
        assert r.status_code == 400
        assert not (subidas_dir() / "prueba" / "script.sh").exists()

        # y no se puede escribir fuera de _subidas
        c.post("/panel/subir", data={
            "carpeta": "../fuera", "archivo": (io.BytesIO(b"x"), "a.jpg")})
        assert not (RAIZ_FOTOS / "fuera").exists(), "FUGA: escribio fuera de _subidas"

    # ------------------------------------------------- eventos del catalogo
    global CATALOGO
    CATALOGO = tmp / "catalogo.db"
    cat = sqlite3.connect(CATALOGO)
    cat.executescript("""
        CREATE TABLE evento (id INTEGER PRIMARY KEY, nombre TEXT, inicio TEXT,
            fin TEXT, coleccion TEXT, tipo TEXT, parent_id INTEGER,
            fotos INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE foto (id INTEGER PRIMARY KEY, ruta TEXT NOT NULL, fecha TEXT,
            camara TEXT, ancho INTEGER, alto INTEGER);
        CREATE TABLE evento_foto (evento_id INTEGER, foto_id INTEGER,
            PRIMARY KEY (evento_id, foto_id));
    """)
    cat.execute("INSERT INTO evento (id,nombre,inicio,coleccion,fotos) "
                "VALUES (7,'Partido de prueba','2026-08-20','LigaTest',2)")
    for i, r in enumerate(reales[:2], start=1):
        cat.execute("INSERT INTO foto (id,ruta) VALUES (?,?)", (900 + i, r))
        cat.execute("INSERT INTO evento_foto VALUES (7, ?)", (900 + i,))
    cat.commit()
    cat.close()

    with app.test_client() as c:
        assert "LigaTest" in c.get("/panel/eventos").get_data(as_text=True)
        d = c.get("/panel/eventos/7").get_data(as_text=True)
        assert "Partido de prueba" in d and "/panel/cat/901" in d
        assert c.get("/panel/eventos/99").status_code == 404
        assert c.get("/panel/cat/901").status_code == 200, "no genero la miniatura"
        assert c.get("/panel/cat/12345").status_code == 404

        # publicar el evento crea una galeria con sus fotos
        antes = conectar()
        n0 = antes.execute("SELECT COUNT(*) FROM galeria").fetchone()[0]
        antes.close()
        r = c.post("/panel/eventos/7/publicar", data={"nombre": "Desde el evento"})
        assert r.status_code == 302, r.status_code
        cx2 = conectar()
        g2 = cx2.execute("SELECT id FROM galeria WHERE nombre='Desde el evento'").fetchone()
        assert g2 is not None, "no creo la galeria"
        assert cx2.execute("SELECT COUNT(*) FROM galeria").fetchone()[0] == n0 + 1
        assert cx2.execute("SELECT COUNT(*) FROM foto WHERE galeria_id=?",
                           (g2["id"],)).fetchone()[0] == 2, "no puso las 2 fotos"
        cx2.close()

    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "servir"
    if cmd == "test":
        test()
    elif cmd == "publicar":
        if len(sys.argv) != 4:
            sys.exit('uso: galeria.py publicar "Nombre" lista.txt')
        publicar(sys.argv[2], sys.argv[3])
    elif cmd == "calentar":
        if len(sys.argv) != 3:
            sys.exit("uso: galeria.py calentar <slug>")
        calentar(sys.argv[2])
    elif cmd == "servir":
        CACHE.mkdir(parents=True, exist_ok=True)
        # ponytail: servidor de desarrollo de Flask (threaded), detras de Caddy.
        # Aguanta un puñado de clientes simultaneos, que es el caso real. Si
        # alguna vez hay decenas a la vez, pasar a gunicorn -w 4.
        app.run(host="127.0.0.1", port=8000)
    else:
        sys.exit(__doc__)
