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
    python3 galeria.py videos [dias]               # prepara los clips nuevos (cron)
    python3 galeria.py test                        # self-check
"""
import os
import io
import re
import csv
import sys
import time
import secrets
import shutil
import subprocess
import threading
import sqlite3
import contextlib
import unicodedata
import hashlib
import hmac
import zipfile
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from functools import lru_cache
from pathlib import Path

from flask import (Flask, g, session, request, redirect, url_for, abort,
                   send_file, render_template_string, jsonify, Response)
from markupsafe import Markup
from werkzeug.utils import secure_filename
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, features

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("GALERIA_DB", BASE / "galeria.db"))
CACHE = Path(os.environ.get("GALERIA_CACHE", BASE / "cache"))
RAIZ_FOTOS = Path(os.environ.get("GALERIA_RAIZ", "/mnt/fotos"))
# derivadas (JPEG sacados del embebido de un RAW): regenerables, van a la
# SSD. Asi el disco de fotos puede quedar montado solo lectura.
RAIZ_DERIVADAS = Path(os.environ.get("GALERIA_DERIVADAS", BASE / "derivadas"))
MARCA = os.environ.get("GALERIA_MARCA", "rb.fotos")
# lo que se puede elegir por galeria. Una galeria con marca_texto en NULL
# usa MARCA, asi que las que ya existian no cambian de aspecto.
MARCAS = ("RB.Fotos_", "RB.Deportes_")

# Mas chico que lo que entregas, a proposito. Si el preview mide lo mismo que
# el archivo final, la marca de agua es lo unico que los separa; con 1000px de
# lado largo el preview sirve para elegir y no sustituye a la entrega.
ANCHO_PREVIEW = int(os.environ.get("GALERIA_ANCHO", 1000))

# Lo que pide el VISOR al abrir una foto. Con 1000 px la grilla anda liviana
# pero al ver una en grande no se distingue si esta enfocada, y para elegir
# 140 entre 5.000 eso es justo lo que hay que ver. Sigue siendo menos de un
# tercio del original y va con la misma marca de agua.
ANCHO_VISOR = int(os.environ.get("GALERIA_ANCHO_VISOR", 2400))

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

# Logos para la marca de agua. Se suben desde el panel y se eligen por
# galeria: asi una fecha de una liga puede llevar su logo y el mio juntos.
LOGOS = Path(os.environ.get("GALERIA_LOGOS", BASE / "logos"))
MAX_LOGOS = 4

# La FIRMA es otra cosa que la marca de agua: va en la foto que se entrega,
# a color y opaca, una sola vez en una esquina. La marca de agua protege el
# preview; la firma acredita la foto vendida.
FILAS = ("arriba", "medio", "abajo")
COLUMNAS = ("izq", "cen", "der")
POSICIONES = tuple(f"{f}-{c}" for f in FILAS for c in COLUMNAS)
FIRMA_POS = "arriba-cen"
FIRMA_TAM = 16          # % del ancho de la foto
# las muestras del panel no se firman a tamaño completo: al 1000 de ancho se
# ven igual (la firma es un % del ancho, no un tamaño fijo) y cuestan la mitad
ANCHO_MUESTRA_FIRMA = 1000
CALIDAD_FIRMA = int(os.environ.get("GALERIA_CALIDAD_FIRMA", 95))

MUESTRAS = Path(os.environ.get("GALERIA_MUESTRAS", BASE / "portada"))
ANCHO_MUESTRA = int(os.environ.get("GALERIA_ANCHO_MUESTRA", 1400))
# la portada se muestra a sangre: si se genera del tamaño de la grilla, el
# navegador la estira y estira la marca de agua con ella
ANCHO_TAPA = int(os.environ.get("GALERIA_ANCHO_TAPA", 2200))
# anchos chicos que /muestra/ y /portada/ aceptan con ?w=, para el srcset de
# la portada. Lista cerrada a proposito: un ?w= libre dejaria a cualquiera
# llenar el cache de la Pi pidiendo un ancho distinto por request
ANCHOS = (320, 480, 600, 800, 1000)

# el dominio publico, para el canonical y el sitemap. Va fijo y no sale de
# request.url_root: detras del tunel ese puede llegar como http:// o 127.0.0.1
SITIO = os.environ.get("GALERIA_SITIO", "https://rbfotos.studio").rstrip("/")

# Numero de WhatsApp en formato internacional sin signos: 56912345678.
# Vacio = no se dibuja el boton, en vez de dejar un link roto.
WHATSAPP = os.environ.get("GALERIA_WHATSAPP", "56973756165")

# Precios por tramo, en pesos chilenos: una linea por tramo, "<fotos> <precio>".
# Cada galeria puede tener los suyos (columna `precios`); esto es el default.
PRECIOS = os.environ.get(
    "GALERIA_PRECIOS", "5 3000\n10 5500\n20 10000\n50 20000")

# Que recibe el cliente cuando paga. Una linea por fila, "Etiqueta: valor".
# Los packs de edicion que se pueden ofrecer. La descripcion de cada uno son
# textos editables (edicion_basica / edicion_completa): aca solo esta la clave
# y de que texto sale, para que el panel arme el selector solo.
EDICIONES = ("basica", "completa")

# Que es cada galeria. Es un rotulo para el panel y nada mas: no cambia como
# se comporta, para eso estan los interruptores de venta y descargas.
TIPOS = ("evento", "galeria", "seleccion", "amigos")
TIPO_NOMBRE = {"evento": "Evento", "galeria": "Galeria de entrega",
               "seleccion": "Galeria de seleccion", "amigos": "Amigos"}
# La galeria con este slug no muestra fotos: muestra las pasadas que grabo
# Frigate (la camara del comedero). Clave, vencimiento y gate son los de
# cualquier galeria; lo unico distinto es de donde salen los items.
VIDEOS_SLUG = os.environ.get("GALERIA_VIDEOS", "perros")
# el puerto interno de Frigate, sin login: publicado solo en 127.0.0.1
FRIGATE = os.environ.get("GALERIA_FRIGATE", "http://127.0.0.1:5000")
FRIGATE_CAMARA = os.environ.get("GALERIA_FRIGATE_CAMARA", "comedero")
ETIQUETAS = {"dog": "Perro", "cat": "Gato"}
# El clip que entrega Frigate es un MP4 fragmentado: no dice cuanto dura hasta
# el final, asi que el navegador no puede mostrarlo mientras baja ni adelantar.
# ffmpeg lo reordena (-movflags +faststart) sin recodificar, en menos de 1 s.
# En la Pi no hay ffmpeg, pero si adentro del contenedor de Frigate, y
# /mnt/fotos/_mascotas ya esta montado ahi como /media/frigate.
FF_DOCKER = os.environ.get("GALERIA_FFMPEG_DOCKER", "frigate")
FF_PUENTE = Path(os.environ.get("GALERIA_PUENTE", "/mnt/fotos/_mascotas/_tmp"))
EVENTO_ID = re.compile(r"^[0-9]{9,11}\.[0-9]+-[a-z0-9]{4,10}$")
VIDEOS_PAGINA = 240        # eventos que se piden; las visitas son muchas menos
# Frigate corta el evento cuando el animal se queda quieto y abre otro cuando
# se mueve: una sola visita al plato deja 3, 7 o 18 clips del mismo minuto.
# Clips separados por menos de esto son la misma visita.
HUECO_VISITA = 180
# Cuando el dispensador tira comida. Una visita que empieza entre 5 minutos
# antes y 30 despues de una de estas horas es "la comida de las 13:30".
COMIDAS = tuple(h.strip() for h in
                os.environ.get("GALERIA_COMIDAS", "08:30,13:30,18:30").split(",") if h.strip())
COMIDA_ANTES, COMIDA_DESPUES = 5 * 60, 30 * 60
# titulos de los grupos del indice del panel, en este orden
TIPO_GRUPO = (("evento", "Eventos"), ("galeria", "Galerias de entrega"),
              ("seleccion", "Galerias de seleccion"), ("amigos", "Amigos"),
              (None, "Sin tipo"))


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
CREATE TABLE IF NOT EXISTS lista (
    id         INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    nombre     TEXT NOT NULL,
    orden      INTEGER NOT NULL DEFAULT 0,
    activa     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(cliente_id, nombre)
);
CREATE TABLE IF NOT EXISTS lista_foto (
    lista_id INTEGER NOT NULL REFERENCES lista(id) ON DELETE CASCADE,
    foto_id  INTEGER NOT NULL REFERENCES foto(id) ON DELETE CASCADE,
    PRIMARY KEY (lista_id, foto_id)
);
CREATE INDEX IF NOT EXISTS ix_lista_cliente ON lista(cliente_id, orden);
-- ordenar por "mas elegidas" cuenta por foto; sin este indice cada foto
-- escanea lista_foto entera
CREATE INDEX IF NOT EXISTS ix_lista_foto_foto ON lista_foto(foto_id);
CREATE TABLE IF NOT EXISTS descarga (
    id         INTEGER PRIMARY KEY,
    cliente_id INTEGER REFERENCES cliente(id) ON DELETE SET NULL,
    foto_id    INTEGER NOT NULL REFERENCES foto(id) ON DELETE CASCADE,
    cual       TEXT NOT NULL DEFAULT 'original',
    cuando     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_descarga_foto ON descarga(foto_id, cuando);
CREATE TABLE IF NOT EXISTS vista (
    cliente_id INTEGER NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    foto_id    INTEGER NOT NULL REFERENCES foto(id) ON DELETE CASCADE,
    visor      INTEGER NOT NULL DEFAULT 0,   -- 0 = miniatura, 1 = abierta grande
    primera    TEXT NOT NULL,
    ultima     TEXT NOT NULL,
    veces      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (cliente_id, foto_id, visor)
);
CREATE INDEX IF NOT EXISTS ix_vista_foto ON vista(foto_id);
-- una carpeta junta galerias bajo un solo link (/c/<slug>)
CREATE TABLE IF NOT EXISTS carpeta (
    id     INTEGER PRIMARY KEY,
    slug   TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    creada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS carpeta_galeria (
    carpeta_id INTEGER NOT NULL REFERENCES carpeta(id) ON DELETE CASCADE,
    galeria_id INTEGER NOT NULL REFERENCES galeria(id) ON DELETE CASCADE,
    PRIMARY KEY (carpeta_id, galeria_id)
);
CREATE TABLE IF NOT EXISTS comentario (
    id         INTEGER PRIMARY KEY,
    foto_id    INTEGER NOT NULL REFERENCES foto(id) ON DELETE CASCADE,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    texto      TEXT NOT NULL,
    cuando     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_comentario_foto ON comentario(foto_id, id);
CREATE TABLE IF NOT EXISTS texto (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seleccion_lista (
    galeria_id INTEGER NOT NULL REFERENCES galeria(id) ON DELETE CASCADE,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    cuando     TEXT NOT NULL,
    PRIMARY KEY (galeria_id, cliente_id)
);
"""


# todo se guarda en UTC, pero "a que hora entro" solo se entiende en la hora
# de aca: 00:41 UTC es las 21:41 del dia anterior en Chile
try:
    TZ = ZoneInfo("America/Santiago")
except Exception:                       # sin tzdata: se muestra UTC y listo
    TZ = timezone.utc


def hora_local(s, fmt="%d/%m %H:%M"):
    try:
        return datetime.fromisoformat(s).astimezone(TZ).strftime(fmt)
    except (TypeError, ValueError):
        return s or "-"


def ahora_iso():
    """UTC al segundo: es como estan guardadas todas las fechas de la base."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


def conectar():
    cx = sqlite3.connect(DB, timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    # WAL: lectores y escritor no se bloquean entre si. Con el modo por
    # defecto, anotar una vista trababa la base entera y la grilla devolvia
    # 500 en las fotos que caian justo ahi (18/9/2026, CasaMati con 300).
    # Es persistente: se fija una vez y queda en el archivo.
    cx.execute("PRAGMA journal_mode = WAL")
    cx.execute("PRAGMA busy_timeout = 30000")
    cx.executescript(ESQUEMA)
    # galerias creadas antes de que existiera la columna raw
    tiene_foto = {r["name"] for r in cx.execute("PRAGMA table_info(foto)")}
    if "raw" not in tiene_foto:
        cx.execute("ALTER TABLE foto ADD COLUMN raw TEXT")
        cx.commit()
    # cuando se tomo, del EXIF. NULL en las galerias de antes: ahi el orden por
    # captura cae en `orden`, que para un evento del catalogo ES la fecha
    if "tomada" not in tiene_foto:
        cx.execute("ALTER TABLE foto ADD COLUMN tomada TEXT")
        cx.commit()
    # a que partido pertenece la foto, y los dos colores de polera que lo
    # identifican. NULL = sin agrupar, que es como estan todas las galerias
    for col in ("grupo", "grupo_col"):
        if col not in tiene_foto:
            cx.execute("ALTER TABLE foto ADD COLUMN %s TEXT" % col)
            cx.commit()
    # si se puede descargar. Arranca en 1: nada cambia para lo que ya existe,
    # y se apaga foto por foto desde /panel/<slug>/descargables
    if "descargable" not in tiene_foto:
        cx.execute("ALTER TABLE foto ADD COLUMN descargable INTEGER DEFAULT 1")
        cx.commit()
    # columnas agregadas despues: una galeria vieja las tiene en NULL y se
    # comporta exactamente igual que antes de que existieran
    tiene = {r["name"] for r in cx.execute("PRAGMA table_info(galeria)")}
    for col, tipo in (("clave_hash", "TEXT"), ("portada_foto_id", "INTEGER"),
                      ("expira", "TEXT"), ("marca", "INTEGER DEFAULT 1"),
                      ("descargas", "INTEGER DEFAULT 0"),
                      # el boton RAW; 1 = como siempre, se apaga por galeria
                      ("raw_descarga", "INTEGER DEFAULT 1"),
                      ("marca_texto", "TEXT"),
                      # publica: se lista en /eventos. venta: se puede
                      # comprar. Las dos en 0, asi ninguna galeria que ya
                      # existe se vuelve publica sola al migrar.
                      ("publica", "INTEGER DEFAULT 0"),
                      ("venta", "INTEGER DEFAULT 0"),
                      # fecha del evento; `creada` es cuando lo publicaste,
                      # que para un evento del ano pasado no es lo mismo
                      ("fecha", "TEXT"),
                      # tramos propios de esta galeria; NULL = los de PRECIOS
                      ("precios", "TEXT"),
                      # indicaciones de entrega propias; NULL = las de ENTREGA
                      ("entrega", "TEXT"),
                      # logos de la marca de agua, un nombre de archivo por
                      # linea; NULL o vacio = solo el texto, como siempre
                      ("logos", "TEXT"),
                      # logo estampado en la ENTREGA. NULL = se entrega el
                      # archivo original tal cual, como hasta ahora
                      ("firma", "TEXT"),
                      ("firma_pos", "TEXT"),
                      ("firma_tam", "INTEGER"),
                      # que recibe el cliente. Vacio = lo que diga el texto
                      # general, asi una galeria nueva ya sale con algo puesto
                      ("ent_resolucion", "TEXT"),
                      ("ent_metodo", "TEXT"),
                      ("ent_plazo", "TEXT"),
                      ("ent_detalles", "TEXT"),
                      ("ent_edicion", "TEXT"),
                      # rotulo para el panel; no cambia el comportamiento
                      ("tipo", "TEXT"),
                      # listas que se le crean solas a cada cliente, una por
                      # linea. NULL = solo Favoritos, como siempre
                      ("listas_auto", "TEXT"),
                      # comentarios de los clientes sobre las fotos, visibles
                      # entre ellos. Apagados: ninguna galeria que ya existe
                      # los estrena sola
                      ("comentarios", "INTEGER DEFAULT 0"),
                      # renglones del preset de Edicion que ESTA galeria no
                      # muestra, uno por linea y tal cual. NULL = la lista
                      # entera, como siempre
                      ("ent_quita", "TEXT")):
        if col not in tiene:
            cx.execute(f"ALTER TABLE galeria ADD COLUMN {col} {tipo}")
            cx.commit()
    # la creo la galeria sola (listas_auto) y no el cliente. Solo estas se
    # borran al apagar la casilla, y solo si quedaron vacias
    if "auto" not in {r["name"] for r in cx.execute("PRAGMA table_info(lista)")}:
        cx.execute("ALTER TABLE lista ADD COLUMN auto INTEGER DEFAULT 0")
        cx.commit()
    # como se llama, para firmar sus comentarios. El correo sigue siendo quien
    # es; el nombre es opcional y solo para mostrar
    if "nombre" not in {r["name"] for r in cx.execute("PRAGMA table_info(cliente)")}:
        cx.execute("ALTER TABLE cliente ADD COLUMN nombre TEXT")
        cx.commit()
    # cuando se marco cada foto. NULL = marca vieja, de antes de que se
    # midiera: se muestra como tal, no se inventa una fecha
    if "cuando" not in {r["name"] for r in cx.execute("PRAGMA table_info(lista_foto)")}:
        cx.execute("ALTER TABLE lista_foto ADD COLUMN cuando TEXT")
        cx.commit()
    migrar_megusta(cx)
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
    # una derivada de la SSD ya esta en su lugar: mandarla a /mnt/fotos la
    # convertia en una ruta inexistente y la foto se perdia en silencio
    if bajo.startswith(str(RAIZ_DERIVADAS).lower() + "/"):
        return r
    if bajo.startswith("k:/"):
        r = r[3:]
    elif bajo.startswith(str(RAIZ_FOTOS).lower() + "/"):
        r = r[len(str(RAIZ_FOTOS)) + 1:]
    return str(RAIZ_FOTOS / r.lstrip("/"))


def dentro_de_raiz(ruta):
    """Nadie sirve nada de afuera de las dos raices, venga de donde venga.

    resolve() ANTES de comparar: resuelve symlinks y `..`, asi que un
    /mnt/fotos/../etc/shadow no pasa. Agregar una raiz no afloja eso.
    """
    try:
        p = Path(ruta).resolve()
    except OSError:
        return False
    # las derivadas primero, y no por gusto: si el disco de fotos esta
    # desconectado su punto de automontaje CUELGA en el resolve(), asi que
    # preguntar por el ultimo evita colgar todo lo que vive en la SSD
    for raiz in (RAIZ_DERIVADAS, RAIZ_FOTOS):
        try:
            p.relative_to(raiz.resolve())
            return True
        except ValueError:
            continue
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


def fecha_exif(ruta):
    """Cuando se tomo la foto, del EXIF, o None si no lo dice.

    Lee la cabecera nada mas —no decodifica los pixeles—, asi que son
    milisegundos por archivo y se puede hacer al agregar sin que se note.
    Devuelve "2026-08-23 21:30:15", que ordena bien como texto.
    """
    try:
        ex = Image.open(ruta).getexif()
        v = ex.get(36867) or ex.get(306)
        if not v:
            v = ex.get_ifd(0x8769).get(36867) or ex.get_ifd(0x8769).get(36868)
        if v:
            return str(v).strip().replace(":", "-", 2)
    except Exception:
        pass
    return None


_CACHE_RAW = {}  # carpeta -> listado, SOLO mientras dura una tanda
_TANDA = [False]


@contextlib.contextmanager
def tanda_raw():
    """Cachea los listados de carpetas mientras dura una busqueda en tanda
    (alta de galeria, boton "Buscar RAW"). Fuera de una tanda no se cachea:
    el servidor corre dias y los RAW se copian despues de crear la galeria;
    un indice guardado de antes decia "no hay" para siempre."""
    _CACHE_RAW.clear()
    _TANDA[0] = True
    try:
        yield
    finally:
        _TANDA[0] = False
        _CACHE_RAW.clear()


def _indice_raw(carpeta):
    """Todos los RAW bajo esa carpeta (subcarpetas incluidas), por nombre.

    Se cachea por proceso: reparar_raw() llama a buscar_raw() una vez por
    foto del evento, y recorrer de nuevo miles de archivos por cada una
    seria lentisimo. Una tarjeta de camara copiada tal cual trae carpetas
    tipo 100MSDCF/101MSDCF adentro de RAW/, por eso rglob y no iterdir.
    """
    carpeta = str(carpeta)
    if carpeta not in _CACHE_RAW:
        idx = {}
        for f in Path(carpeta).rglob("*"):
            if f.is_file() and f.suffix.lower() in CRUDAS:
                idx.setdefault(f.name.lower(), []).append(f)
        if not _TANDA[0]:
            return idx
        _CACHE_RAW[carpeta] = idx
    return _CACHE_RAW[carpeta]


def _nombres_en(carpeta):
    """{nombre.lower(): Path} de una carpeta. En una tanda se lista una sola
    vez: una tarjeta trae 1500 fotos por carpeta, y listarla por cada foto
    eran millones de lecturas por evento."""
    clave = "dir:" + str(carpeta)
    if clave in _CACHE_RAW:
        return _CACHE_RAW[clave]
    try:
        listado = {h.name.lower(): h for h in Path(carpeta).iterdir()}
    except OSError:
        listado = {}
    if _TANDA[0]:
        _CACHE_RAW[clave] = listado
    return listado


def _fecha_captura_raw(ruta):
    """Fecha EXIF de un RAW. Pillow no lo abre, pero por dentro es TIFF y
    exifread si lee esa cabecera. Mismo formato que fecha_exif() para poder
    compararlas."""
    try:
        import exifread
        with open(ruta, "rb") as f:
            tags = exifread.process_file(f, details=False,
                                          stop_tag="EXIF DateTimeOriginal")
        for k in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
            if k in tags:
                return str(tags[k]).strip().replace(":", "-", 2)
    except Exception:
        pass
    return None


def buscar_raw(jpg, raw_dir=None):
    """El RAW hermano: al lado con el mismo nombre, o en cualquier parte de
    una carpeta "RAW" hermana (convencion por evento RAW / Jpeg / Export;
    busca en subcarpetas porque una tarjeta copiada tal cual trae 100MSDCF,
    101MSDCF, etc. adentro de RAW/).

    raw_dir fuerza la carpeta: una derivada de la SSD no tiene RAW hermano y
    la base no guarda de donde salio.

    El contador de la Sony vuelve a DSC00001 cada 10000 fotos: dos tarjetas
    del mismo evento pueden traer el mismo nombre. Si el nombre aparece mas
    de una vez adentro de RAW/, la fecha EXIF tiene que coincidir con la del
    JPEG. De lo que coincide se descarta el .dng (conversion de Lightroom,
    no el original), y lo que queda tiene que ser la misma foto copiada
    (SeleccionFinal/ repite el .ARW de la tarjeta): mismo tamaño exacto.
    Si no, None antes que entregar el archivo equivocado.
    """
    p = Path(jpg)
    nombres = {(p.stem + ext).lower() for ext in CRUDAS}
    if raw_dir is None:
        al_lado = _nombres_en(p.parent)
        for n in nombres:
            if n in al_lado:
                return str(al_lado[n])
        # la carpeta RAW del evento puede estar uno o dos niveles arriba:
        # Export/x.jpg -> ../RAW, y jpeg/100MSDCF/x.JPG -> ../../RAW (tarjeta
        # copiada tal cual). Dos y no mas: mas arriba ya es otro evento.
        arriba = [p.parent.parent, p.parent.parent.parent]
        if any(a.name.lower() == "raw" for a in [p.parent] + arriba):
            return None          # la foto ya esta adentro de RAW/
        raw_dir = next((h for a in arriba for h in _nombres_en(a).values()
                        if h.name.lower() == "raw" and h.is_dir()), None)
        if raw_dir is None:
            return None
    indice = _indice_raw(raw_dir)
    candidatos = [c for nombre in nombres for c in indice.get(nombre, ())]
    if len(candidatos) <= 1:
        return str(candidatos[0]) if candidatos else None
    fecha = fecha_exif(jpg)
    coinciden = [c for c in candidatos if fecha and _fecha_captura_raw(c) == fecha]
    coinciden = [c for c in coinciden if c.suffix.lower() != ".dng"] or coinciden
    if coinciden and len({c.stat().st_size for c in coinciden}) == 1:
        return str(sorted(coinciden)[0])
    return None


def reparar_raw(slug=None, raw_dir=None):
    """Vuelve a buscar el RAW de cada foto que todavia no tiene uno.

    Para las galerias publicadas antes de que buscar_raw() supiera mirar la
    carpeta RAW hermana. Sin slug, repasa todas las galerias. raw_dir es para
    las galerias hechas desde derivadas, que no tienen RAW al lado.
    """
    if raw_dir and not Path(raw_dir).is_dir():
        sys.exit("No existe la carpeta %s" % raw_dir)
    cx = conectar()
    where, args = "raw IS NULL", ()
    if slug:
        gal = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()
        if gal is None:
            sys.exit("No existe la galeria %s" % slug)
        where, args = where + " AND galeria_id = ?", (gal["id"],)
    encontrados, total = _buscar_raws(cx, where, args, raw_dir)
    print("%d de %d fotos sin RAW ahora lo tienen" % (encontrados, total))


def _buscar_raws(cx, where, args, raw_dir=None):
    """(encontrados, revisadas) para las fotos que cumplen `where`."""
    fotos = cx.execute("SELECT id, ruta FROM foto WHERE %s" % where, args).fetchall()
    encontrados = 0
    with tanda_raw():
        for f in fotos:
            raw = buscar_raw(f["ruta"], raw_dir)
            if raw:
                cx.execute("UPDATE foto SET raw = ? WHERE id = ?", (raw, f["id"]))
                encontrados += 1
    cx.commit()
    return encontrados, len(fotos)


# ------------------------------------------------------------- marca de agua

def _fuente(px):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, px)
    return ImageFont.load_default()


ALFA_MARCA = 62      # lo translucida que va la marca, sobre 255


def logos_de(gal):
    """Los logos elegidos para una galeria, como tupla (hashable: entra en el
    lru_cache de la capa). Solo los que siguen existiendo en la carpeta."""
    crudo = ""
    if gal is not None and "logos" in gal.keys() and gal["logos"]:
        crudo = gal["logos"]
    hay = set(logos_disponibles())
    return tuple(n for n in crudo.splitlines() if n.strip() in hay)[:MAX_LOGOS]


def logos_disponibles():
    if not LOGOS.is_dir():
        return []
    return sorted(f.name for f in LOGOS.iterdir() if f.is_file() and es_visible(f))


def combos_logos():
    """Las combinaciones de logos que ya usaste en otras galerias.

    No hace falta guardarlas aparte: son los valores distintos de
    galeria.logos. Asi una combinacion existe mientras alguna galeria la use
    y desaparece sola cuando ya no la usa ninguna — cero mantenimiento.
    """
    hay = set(logos_disponibles())
    vistos, out = set(), []
    for r in db().execute("SELECT DISTINCT logos FROM galeria "
                          "WHERE logos IS NOT NULL AND logos <> ''"):
        c = tuple(n for n in r["logos"].splitlines() if n in hay)[:MAX_LOGOS]
        if c and c not in vistos:
            vistos.add(c)
            out.append(c)
    return out


# Los logos se componen a esta altura y la estampa los achica UNA vez: asi
# no se escala dos veces (que emborrona) ni se agranda desde una miniatura.
ALTO_REF = 512


def _blanquear(im):
    """El logo pasa a blanco, con su propia luminancia como transparencia.

    Una marca de agua sobre foto tiene que ser clara: el logo de PrimeLeague
    es negro con dorado y al 24% sobre una cancha de noche no se ve. Pasarlo a
    silueta plana tampoco sirve —el escudo relleno taparia sus propias
    letras—, asi que lo que manda la opacidad de cada pixel es cuan claro es:
    lo blanco del logo queda, lo negro se va. Un logo que ya viene blanco no
    cambia, asi que se puede aplicar siempre sin preguntar.
    """
    return Image.merge("RGBA", (
        Image.new("L", im.size, 255), Image.new("L", im.size, 255),
        Image.new("L", im.size, 255),
        ImageChops.multiply(im.getchannel("A"), im.convert("L"))))


def _tira_logos(nombres, alto=ALTO_REF):
    """Los logos en fila, a la misma altura y ya translucidos.

    Cada uno se recorta a su contenido antes de escalar. Sin eso, un PNG
    exportado en un lienzo cuadrado —lo normal— entra casi todo transparente
    y el logo de verdad sale ridiculamente chico.

    Un archivo roto o borrado se saltea en vez de reventar: quedarse sin marca
    de agua por un PNG malo seria justo lo contrario de lo que la marca hace.
    """
    ims = []
    for n in nombres:
        f = LOGOS / n
        try:
            if not f.is_file() or f.resolve().parent != LOGOS.resolve():
                continue
            im = Image.open(f).convert("RGBA")
            caja = im.getbbox()          # None si es enteramente transparente
            if caja:
                im = im.crop(caja)
            im = _blanquear(im)
            ancho = max(1, round(im.width * alto / im.height))
            ims.append(im.resize((ancho, alto), Image.LANCZOS))
        except Exception:
            continue
    if not ims:
        return None
    hueco = max(1, round(alto * 0.5))
    total = sum(i.width for i in ims) + hueco * (len(ims) - 1)
    tira = Image.new("RGBA", (total, alto), (0, 0, 0, 0))
    x = 0
    for i in ims:
        tira.alpha_composite(i, (x, 0))
        x += i.width + hueco
    # la transparencia propia del logo se conserva; lo que baja es su opacidad
    tira.putalpha(tira.getchannel("A").point(lambda v: v * ALFA_MARCA // 255))
    return tira


def _estampa(texto, px, logos):
    """Lo que se repite en el mosaico: el texto y, debajo, la fila de logos.

    El logo se lleva el ANCHO del texto, no una altura fija: asi acompaña a
    "RB.Deportes_" en vez de quedar de adorno. Con texto vacio va solo el
    logo, para los que ya traen el nombre adentro.
    """
    texto = (texto or "").strip()
    if not texto and not logos:
        texto = MARCA          # una marca vacia no marca nada
    fuente = _fuente(px)
    izq = arr = tw = th = 0
    if texto:
        medir = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        izq, arr, der, aba = medir.textbbox((0, 0), texto, font=fuente)
        tw, th = der - izq, aba - arr

    tira = _tira_logos(logos)
    if tira is not None:
        # el piso importa: con una marca corta (o un espacio) el logo se
        # escalaba al ancho de esa nada y desaparecia
        objetivo = max(tw, px * 7)
        alto_max = px * (2.6 if tw else 4.0)
        esc = objetivo / tira.width
        if tira.height * esc > alto_max:
            esc = alto_max / tira.height
        tira = tira.resize((max(1, round(tira.width * esc)),
                            max(1, round(tira.height * esc))), Image.LANCZOS)

    ancho = max(tw, tira.width if tira is not None else 0)
    hueco = round(px * 0.3) if (tira is not None and th) else 0
    est = Image.new("RGBA",
                    (max(1, ancho),
                     max(1, th + hueco + (tira.height if tira is not None else 0))),
                    (0, 0, 0, 0))
    if texto:
        ImageDraw.Draw(est).text(((ancho - tw) // 2 - izq, -arr), texto,
                                 font=fuente, fill=(255, 255, 255, ALFA_MARCA))
    if tira is not None:
        est.alpha_composite(tira, ((ancho - tira.width) // 2, th + hueco))
    return est


def _pegar(capa, est, x, y):
    """alpha_composite de la estampa en (x, y), recortando lo que se sale.

    No es paste(): paste con mascara MULTIPLICA el alfa en vez de componer, y
    una marca al 24% terminaba al 6%, practicamente invisible. Y el
    alpha_composite de Pillow no recorta —tira ValueError— y las estampas del
    borde siempre se salen, asi que el recorte va a mano.
    """
    x, y = int(x), int(y)
    x0, y0 = max(0, -x), max(0, -y)
    x1, y1 = min(est.width, capa.width - x), min(est.height, capa.height - y)
    if x0 >= x1 or y0 >= y1:
        return
    capa.alpha_composite(est.crop((x0, y0, x1, y1)), (x + x0, y + y0))


@lru_cache(maxsize=12)
def capa_marca(w, h, texto, factor=0.042, logos=()):
    """La capa de marca depende del tamaño, del texto y de los logos, no de
    la foto.

    Rotarla cuesta ~0.85s en la Pi 4 y dibujarla otros 0.20s — mas de la mitad
    del costo total. Como una galeria tiene dos o tres medidas distintas, se
    calcula una vez por medida y se reusa para todas las fotos.
    """
    lado = int((w ** 2 + h ** 2) ** 0.5) + 2
    capa = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    # la tapa se ve a sangre: ahi la marca tiene que ser proporcionalmente
    # mas chica que en una miniatura, o tapa la foto
    est = _estampa(texto, max(12, int(w * factor)), logos)
    paso_x = est.width + w * 0.14
    paso_y = est.height * (2.2 if logos else 4.5)

    y = 0
    fila = 0
    while y < lado:
        x = -paso_x if fila % 2 else 0
        while x < lado:
            _pegar(capa, est, x, y)
            x += paso_x
        y += paso_y
        fila += 1

    capa = capa.rotate(30, resample=Image.BICUBIC, center=(lado / 2, lado / 2))
    return capa.crop(((lado - w) // 2, (lado - h) // 2,
                      (lado - w) // 2 + w, (lado - h) // 2 + h))


def marcar(origen, destino, texto=None, ancho=None, factor=0.042, logos=(),
           firma=None):
    ancho = ancho or ANCHO_PREVIEW
    """Preview reducida con la marca en diagonal, compuesta sobre los pixeles.

    A proposito NO es un overlay de CSS: ahi abajo quedaria la foto limpia y
    cualquiera se la lleva desde el inspector.
    """
    im = Image.open(origen)
    # draft() antes de tocar los pixeles: el decodificador de JPEG baja la
    # resolucion durante la descompresion (1/2, 1/4, 1/8) en vez de decodificar
    # 6000px para despues tirar el 95%. Tiene que ir antes de exif_transpose,
    # que carga la imagen entera.
    im.draft("RGB", (ancho, ancho))
    im = ImageOps.exif_transpose(im)
    im.thumbnail((ancho, ancho), Image.LANCZOS)
    im = im.convert("RGBA")

    out = Image.alpha_composite(
        im, capa_marca(im.width, im.height, MARCA if texto is None else texto,
                       factor, tuple(logos))).convert("RGB")
    # la firma va DESPUES y sin rotar: es la misma que se estampa en la
    # entrega, para que lo que se ve al elegir sea lo que llega
    if firma:
        pegar_firma(out, *firma)
    destino.parent.mkdir(parents=True, exist_ok=True)
    guardar(out, destino, quality=82, optimize=True)


# WebP a 80 desde el original: medido sobre fotos reales a 480/800/1400 px,
# pesa 20% menos que el JPEG a 82 y tiene MAS calidad (PSNR 35,9 vs 35,5 dB).
# Pasarlo desde el JPEG ya cacheado era mas simple pero perdia 1,7 dB.
CALIDAD_WEBP = int(os.environ.get("GALERIA_CALIDAD_WEBP", 80))
WEBP_OK = features.check("webp")      # Pillow sin libwebp: todo sigue en JPEG


def guardar(im, destino, **jpeg):
    """Guarda en el formato que dice la extension del destino: .webp o JPEG."""
    if Path(destino).suffix == ".webp":
        im.save(destino, "WEBP", quality=CALIDAD_WEBP, method=4)
    else:
        im.save(destino, "JPEG", **jpeg)


def ruta_cache(ruta, texto, logos, ancho, factor, firma=None):
    """Donde vive la preview de (foto, marca, firma, medida). Es una funcion
    aparte porque la limpieza necesita rearmar exactamente la misma clave: si
    la formula viviera solo dentro de preview(), borrar seria adivinar."""
    st = os.stat(ruta)
    clave = hashlib.sha1(
        f"{ruta}|{st.st_mtime_ns}|{texto}|{'|'.join(logos)}|{ancho}|{factor}|"
        f"{firma}".encode()).hexdigest()
    return CACHE / clave[:2] / f"{clave}.jpg"


# las medidas que se generan de verdad: la miniatura de la grilla, la del
# visor y la tapa a sangre. La limpieza tiene que buscar en las tres — sin la
# del visor quedaban copias huerfanas cada vez que se cambiaba la marca.
MEDIDAS = ((None, 0.042), (ANCHO_VISOR, 0.042), (ANCHO_TAPA, 0.017))


def firma_de(gal):
    """(logo, posicion, tamano) de la firma de entrega, o None si no tiene.

    Un logo borrado de la carpeta apaga la firma en vez de romper la descarga.
    """
    if gal is None or "firma" not in gal.keys() or not gal["firma"]:
        return None
    if gal["firma"] not in set(logos_disponibles()):
        return None
    pos = gal["firma_pos"] if gal["firma_pos"] in POSICIONES else FIRMA_POS
    try:
        tam = min(40, max(5, int(gal["firma_tam"] or FIRMA_TAM)))
    except (TypeError, ValueError):
        tam = FIRMA_TAM
    return (gal["firma"], pos, tam)


def pegar_firma(im, logo, pos=FIRMA_POS, tam=FIRMA_TAM):
    """Pega el logo, a color y opaco, en una de las 9 posiciones. Modifica im.

    Lo comparten la entrega y la preview: el comprador tiene que ver mientras
    elige exactamente la foto que le va a llegar, y si el logo se dibujara en
    dos lugares del codigo terminarian dibujandolo distinto.
    """
    lg = Image.open(LOGOS / logo).convert("RGBA")
    caja = lg.getbbox()
    if caja:
        lg = lg.crop(caja)      # el lienzo transparente no cuenta como tamano
    ancho = max(1, round(im.width * tam / 100))
    lg = lg.resize((ancho, max(1, round(lg.height * ancho / lg.width))),
                   Image.LANCZOS)
    m = round(im.width * 0.025)
    fila, col = pos.split("-")
    x = {"izq": m, "cen": (im.width - lg.width) // 2,
         "der": im.width - lg.width - m}[col]
    y = {"arriba": m, "medio": (im.height - lg.height) // 2,
         "abajo": im.height - lg.height - m}[fila]
    im.paste(lg, (x, y), lg)
    return im


def firmar(origen, destino, logo, pos=FIRMA_POS, tam=FIRMA_TAM, ancho=None):
    """La copia que se ENTREGA: el original con el logo estampado.

    exif_transpose ANTES de pegar: si no, en una foto vertical el logo se
    estampa contra el lado que el visor va a rotar y termina acostado. Pillow
    deja la orientacion en 1 en el EXIF que devuelve, asi que no se rota dos
    veces.
    """
    im = ImageOps.exif_transpose(Image.open(origen)).convert("RGB")
    if ancho and im.width > ancho:
        im.thumbnail((ancho, ancho * 20), Image.LANCZOS)
    pegar_firma(im, logo, pos, tam)
    destino.parent.mkdir(parents=True, exist_ok=True)
    ex = im.info.get("exif")
    im.save(destino, "JPEG", quality=CALIDAD_FIRMA, subsampling=0,
            optimize=True, **({"exif": ex} if ex else {}))


def ruta_firma(ruta, logo, pos, tam, ancho=None):
    st = os.stat(ruta)
    clave = hashlib.sha1(
        f"firma|{ruta}|{st.st_mtime_ns}|{logo}|{pos}|{tam}|{ancho}|"
        f"{CALIDAD_FIRMA}".encode()).hexdigest()
    return CACHE / clave[:2] / f"{clave}.jpg"


def firmada(ruta, logo, pos, tam, ancho=None):
    """La copia firmada, cacheada. ancho=None es la que se entrega."""
    destino = ruta_firma(ruta, logo, pos, tam, ancho)
    if not destino.exists():
        firmar(ruta, destino, logo, pos, tam, ancho)
    return str(destino)


def preview_de(gal, foto_id, ruta, ancho=None):
    """La imagen que /p/ sirve para esta foto.

    La comparten la vista y el calentado del cache: si cada uno eligiera por
    su cuenta, calentar generaria previews que nadie pide y la galeria
    quedaria igual de fria, ocupando el doble.
    """
    if gal["marca"] == 0:
        return sin_marca(ruta, ancho)
    return preview(foto_id, ruta, gal["marca_texto"], ancho,
                   logos=logos_de(gal), firma=firma_de(gal))


def clave_preview_de(gal, ruta, ancho=None, logos=None, firma=None):
    """Donde va a parar la preview de esta foto, SIN generarla.

    Tiene que tomar la misma decision que preview_de(): si las dos se separan,
    el contador de calentado miente. logos/firma se pueden pasar ya calculados
    para no releer la carpeta de logos una vez por foto.
    """
    if gal["marca"] == 0:
        return _clave_sin_marca(ruta, ancho)
    texto = gal["marca_texto"]
    return ruta_cache(ruta, MARCA if texto is None else texto,
                      tuple(logos_de(gal) if logos is None else logos),
                      ancho or ANCHO_PREVIEW, 0.042,
                      firma_de(gal) if firma is None else firma)


def entregable(ruta, firma):
    """La ruta del archivo que se entrega: el original, o su copia firmada."""
    if not firma:
        return ruta
    return firmada(ruta, *firma)


def preview(foto_id, ruta, texto=None, ancho=None, factor=0.042, logos=(),
            firma=None, webp=False):
    """Cachea por (ruta, mtime, texto de la marca, logos, ancho).

    El texto y los logos van en la clave a proposito: cambiar la marca de una
    galeria tiene que regenerar, no servir la preview vieja con la de antes.
    """
    # "" es una eleccion (marca de solo logo); None es "no me dijeron nada"
    texto = MARCA if texto is None else texto
    ancho = ancho or ANCHO_PREVIEW
    logos = tuple(logos)
    destino = ruta_cache(ruta, texto, logos, ancho, factor, firma)
    if webp:
        destino = destino.with_suffix(".webp")   # misma clave: la limpieza la encuentra
    if not destino.exists():
        marcar(ruta, destino, texto, ancho, factor, logos, firma)
    return destino


def limpiar_cache(gid, texto, logos, firma=None):
    """Borra las previews de una galeria hechas con ESA marca.

    Sin esto, cada vez que tocas un logo el cache se queda con una copia
    entera de la galeria marcada como antes, que ya no va a pedir nadie: el
    cache crece con cada prueba y no baja nunca. Como la clave es
    determinista, se sabe exactamente que borrar sin recorrer los 12 mil
    archivos ni tocar las previews de otras galerias.
    """
    n = 0
    # mas las de la tapa que piden la tarjeta (srcset) y la vista previa del
    # chat (og, 1200): sin ellas quedaban huerfanas al cambiar la marca
    medidas = MEDIDAS + tuple((w, 0.017) for w in ANCHOS + (1200,))
    for f in db().execute("SELECT ruta FROM foto WHERE galeria_id = ?", (gid,)):
        for ancho, factor in medidas:
            try:
                p = ruta_cache(f["ruta"], MARCA if texto is None else texto,
                               tuple(logos), ancho or ANCHO_PREVIEW, factor,
                               firma)
            except OSError:
                break        # la foto ya no esta en el disco: nada que limpiar
            for q in (p, p.with_suffix(".webp")):
                if q.exists():
                    q.unlink()
                    n += 1
    return n


def limpiar_firmas(gid, firma):
    """Borra las copias firmadas de una galeria hechas con ESA firma."""
    n = 0
    for f in db().execute("SELECT ruta FROM foto WHERE galeria_id = ?", (gid,)):
        for ancho in (None, ANCHO_MUESTRA_FIRMA):
            try:
                p = ruta_firma(f["ruta"], *firma, ancho=ancho)
            except OSError:
                break
            if p.exists():
                p.unlink()
                n += 1
    return n


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


def _clave_sin_marca(ruta, ancho=None):
    """La ruta de cache que le corresponde a esta foto sin marca — la calcula
    sin generarla, para poder preguntar "ya esta calentada" sin pagar el costo.
    """
    ancho = ancho or ANCHO_MUESTRA
    st = os.stat(ruta)
    clave = hashlib.sha1(
        f"muestra|{ruta}|{st.st_mtime_ns}|{ancho}".encode()).hexdigest()
    return CACHE / clave[:2] / f"{clave}.jpg"


def sin_marca(ruta, ancho=None, webp=False):
    """Como preview() pero sin marca de agua y mas grande. Mismo cache."""
    ancho = ancho or ANCHO_MUESTRA
    destino = _clave_sin_marca(ruta, ancho)
    if webp:
        destino = destino.with_suffix(".webp")
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(ruta)
        im.draft("RGB", (ancho, ancho))                   # igual que marcar()
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((ancho, ancho), Image.LANCZOS)
        guardar(im, destino, quality=82, optimize=True, progressive=True)
    return destino


def entradas():
    return set(session.get("galerias", []))


def firma_descarga(gid, cid):
    return hmac.new(app.secret_key.encode(), f"d|{gid}|{cid}".encode(),
                    "sha256").hexdigest()[:20]


def cliente_firmado(gid):
    """El cliente de ?c=&k= si la firma es de ESA galeria, si no None."""
    cid, k = request.args.get("c", type=int), request.args.get("k") or ""
    if cid and hmac.compare_digest(firma_descarga(gid, cid), k):
        return cid
    return None


@app.template_global()
def llave_d(gid):
    """?c=&k= para los links /d/. El navegador de Instagram y Facebook no
    guarda archivos: le pasa el link a Chrome, que llega SIN la cookie de la
    sesion y daba 404 (Cumpleanos Cata, 24/9/2026). Con la firma baja igual."""
    cid = cliente_actual(gid)
    return f"?c={cid}&k={firma_descarga(gid, cid)}" if cid else ""


def foto_autorizada(foto_id, firmada=False):
    """Devuelve la foto solo si el cliente entro a la galeria que la contiene.

    Sin este chequeo los id son enumerables y se cruzan galerias. firmada=True
    acepta ademas un link de llave_d() de la galeria de la foto.
    """
    f = db().execute(
        "SELECT foto.*, galeria.slug, galeria.expira, galeria.marca, "
        "galeria.descargas, galeria.raw_descarga, galeria.marca_texto, galeria.logos, "
        "galeria.firma, galeria.firma_pos, galeria.firma_tam FROM foto "
        "JOIN galeria ON galeria.id = foto.galeria_id WHERE foto.id = ?",
        (foto_id,)).fetchone()
    if f is None or expirada(f) or (
            f["galeria_id"] not in entradas()
            and not (firmada and cliente_firmado(f["galeria_id"]))):
        abort(404)
    return f


FAVORITOS = "Favoritos"
CARRITO = "Carrito"


def favoritos(cx, cid):
    """La lista que todo cliente tiene si o si. Se crea sola y activa."""
    cx.execute("INSERT OR IGNORE INTO lista (cliente_id, nombre, orden, activa) "
               "VALUES (?, ?, 0, 1)", (cid, FAVORITOS))
    return cx.execute("SELECT * FROM lista WHERE cliente_id = ? AND nombre = ?",
                      (cid, FAVORITOS)).fetchone()


# las que el panel ofrece con un tick; ademas se pueden escribir otras
LISTAS_SUGERIDAS = ("Edición completa", "Edición básica")


def _norm(t):
    """'EDICION COMPLETA' y 'Edición completa' son la misma lista."""
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).strip().lower()


def listas_auto_de(gal):
    return [l.strip() for l in (gal["listas_auto"] or "").splitlines() if l.strip()]


def asegurar_listas(cx, gal, cid):
    """Crea (apagadas) las listas que la galeria pide y el cliente no tiene.
    Apagadas: los me-gusta siguen cayendo en Favoritos hasta que el cliente
    elija otra cosa."""
    pedidas = listas_auto_de(gal)
    tiene = {_norm(r["nombre"]) for r in cx.execute(
        "SELECT nombre FROM lista WHERE cliente_id = ?", (cid,))}
    for nombre in pedidas:
        if _norm(nombre) in tiene:
            continue
        cx.execute("INSERT OR IGNORE INTO lista (cliente_id, nombre, orden, activa, auto) "
                   "SELECT ?, ?, COALESCE(MAX(orden), 0) + 1, 0, 1 FROM lista "
                   "WHERE cliente_id = ?", (cid, nombre[:40], cid))
        tiene.add(_norm(nombre))
    # apagar la casilla deshace lo que hizo, pero solo donde no hay nada que
    # perder: la lista automatica que quedo vacia se va, la que el cliente uso
    # se queda, y las suyas (auto = 0) no se tocan nunca
    for r in cx.execute("SELECT id, nombre FROM lista WHERE cliente_id = ? AND auto = 1",
                        (cid,)).fetchall():
        if _norm(r["nombre"]) in {_norm(n) for n in pedidas}:
            continue
        if cx.execute("SELECT COUNT(*) FROM lista_foto WHERE lista_id = ?",
                      (r["id"],)).fetchone()[0] == 0:
            cx.execute("DELETE FROM lista WHERE id = ?", (r["id"],))


# fotos que estan en dos o mas listas del cliente (el carrito no cuenta)
REPETIDAS = (
    "SELECT lista_foto.foto_id FROM lista_foto "
    "JOIN lista ON lista.id = lista_foto.lista_id "
    "WHERE lista.cliente_id = ? AND lista.nombre <> ? "
    "GROUP BY lista_foto.foto_id HAVING COUNT(DISTINCT lista.id) > 1")


def carrito(cx, cid):
    """Donde caen los "agregar al carrito".

    Por dentro es una lista como las demas: reusarlas evita una segunda tabla,
    un segundo borrado en cascada y un segundo lugar donde contar fotos. Por
    fuera no es una lista: no se renombra, no se activa (los me-gusta nunca
    caen ahi) y no sale entre las listas del cliente.
    """
    cx.execute("INSERT OR IGNORE INTO lista (cliente_id, nombre, orden, activa) "
               "VALUES (?, ?, -1, 0)", (cid, CARRITO))
    return cx.execute("SELECT * FROM lista WHERE cliente_id = ? AND nombre = ?",
                      (cid, CARRITO)).fetchone()


def listas_activas(cx, cid):
    """Donde caen los me-gusta. Si el cliente apago todas, cae en Favoritos:
    un like que no se guarda en ningun lado es peor que uno mal guardado."""
    act = cx.execute("SELECT * FROM lista WHERE cliente_id = ? AND activa = 1 "
                     "ORDER BY orden, id", (cid,)).fetchall()
    if act:
        return act
    fav = favoritos(cx, cid)
    cx.execute("UPDATE lista SET activa = 1 WHERE id = ?", (fav["id"],))
    return [fav]


def migrar_megusta(cx):
    """Los me-gusta de antes de que existieran las listas pasan a Favoritos."""
    pendientes = cx.execute(
        "SELECT DISTINCT cliente_id FROM megusta WHERE cliente_id NOT IN "
        "(SELECT cliente_id FROM lista WHERE nombre = ?)", (FAVORITOS,)).fetchall()
    for r in pendientes:
        fav = favoritos(cx, r["cliente_id"])
        cx.execute("INSERT OR IGNORE INTO lista_foto (lista_id, foto_id) "
                   "SELECT ?, foto_id FROM megusta WHERE cliente_id = ?",
                   (fav["id"], r["cliente_id"]))
    if pendientes:
        cx.commit()


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


# --------------------------------------------------------- textos editables
# Lo que se puede cambiar desde /panel/textos, agrupado como se ve en el
# formulario: (clave, etiqueta, filas, default). El default es lo que se ve
# mientras no lo toques: la tabla `texto` guarda SOLO lo que cambiaste, asi
# una version nueva del codigo puede mejorar los textos que nunca editaste.
CAMPOS = (
    ("Portada", (
        ("hero_titulo", "Titulo grande", 1,
         "Fotografia de deportes y eventos."),
        ("hero_bajada", "Bajada del titulo", 3,
         "Ligas, fechas deportivas, actos y presentaciones. Cubro el partido "
         "entero y te lo entrego en una galeria privada, no en un pendrive con "
         "cuatro mil archivos."),
        ("hero_boton_wsp", "Boton de WhatsApp", 1, "Escribime por WhatsApp"),
        ("hero_boton_ev", "Boton de eventos", 1, "Ver los eventos"),
        ("hero_boton_mi", "Boton de sobre mi", 1, "Sobre mi"),
        ("rotulo_eventos", "Rotulo de la franja de eventos", 1, "Eventos que cubri"),
        ("rotulo_eventos_mas", "Enlace al final de esa franja", 1,
         "Ver todos los eventos"),
        ("rotulo_trabajos", "Rotulo de la tira de trabajos", 1, "Trabajos"),
    )),
    ("Calculadora (proximamente)", (
        ("calc_titulo", "Titulo de la pagina", 1,
         "Calculadora de cotizacion"),
        ("calc_texto", "Texto", 4,
         "Vas a poder armar el presupuesto vos mismo: horas de cobertura, "
         "canchas en simultaneo, cuantas fotos entrego y en cuanto tiempo. "
         "Mientras tanto escribime y te lo paso el mismo dia."),
        ("calc_sello", "Sello", 1, "En produccion"),
    )),
    ("Como recibis tus fotos", (
        ("como_titulo", "Titulo (vacio = no se muestra la seccion)", 1,
         "Como recibis tus fotos"),
        ("como1_t", "Paso 1, titulo", 1, "Entras al evento"),
        ("como1_p", "Paso 1, texto", 3,
         "Buscas tu fecha en la lista de eventos, o entras con el link que te "
         "paso. Dejas tu email y ves todas las fotos del dia."),
        ("como2_t", "Paso 2, titulo", 1, "Marcas las tuyas"),
        ("como2_p", "Paso 2, texto", 3,
         "Vas marcando con el corazon las que queres. Yo veo esa seleccion del "
         "otro lado, asi no hay que ir y venir con numeros de archivo."),
        ("como3_t", "Paso 3, titulo", 1, "Pagas y te las llevas"),
        ("como3_p", "Paso 3, texto", 3,
         "El total sale de cuantas marcaste. Transferis, me mandas el "
         "comprobante por WhatsApp y te las paso en calidad completa, de a una "
         "o en un ZIP."),
    )),
    ("Pagina de eventos", (
        ("ev_rotulo", "Rotulo", 1, "Eventos que cubri"),
        ("ev_titulo", "Titulo", 1, "Busca tu fecha"),
        ("ev_bajada", "Bajada", 3,
         "Cada evento abre en su galeria. Entras con tu email, marcas las tuyas "
         "con el corazon y desde ahi las compras o las descargas, segun el evento."),
        ("ev_vacio", "Que decir cuando no hay ninguno publicado", 2,
         "Todavia no hay eventos publicados aca. Escribime y te aviso cuando "
         "suba el tuyo."),
    )),
    ("Dentro de un evento", (
        ("gal_entrega_tit", "Titulo del bloque de que recibis", 1,
         "Que recibis"),
        ("gal_precios_tit", "Titulo del bloque de precios", 1,
         "Precios de este evento"),
        ("gal_precios_nota", "Nota bajo los precios", 3,
         "Marca con el corazon las fotos que quieras: el total se calcula solo "
         "con lo que elijas."),
        ("gal_gratis_tit", "Titulo cuando el evento es gratis", 1,
         "Evento liberado"),
        ("gal_gratis_texto",
         "Texto cuando el evento es gratis (vacio = no se muestra nada)", 3,
         "Todas son tuyas: mira, descarga y aprecia cada una de las fotos de "
         "este evento."),
    )),
    ("Que recibis", (
        ("ent_resolucion_nom", "Etiqueta de la resolucion", 1, "Resolucion"),
        ("ent_resolucion", "Resolucion (si la galeria no pone la suya)", 1,
         "2K, 2560 x 1440 pixeles"),
        ("ent_edicion_nom", "Etiqueta de la edicion", 1, "Edicion"),
        ("ent_metodo_nom", "Etiqueta del metodo", 1, "Metodos de entrega"),
        ("ent_metodo", "Metodos (si la galeria no pone los suyos)", 2,
         "Por correo o Google Drive temporal"),
        ("ent_plazo_nom", "Etiqueta de los plazos", 1, "Opciones de entrega"),
        ("ent_plazo", "Plazos (si la galeria no pone los suyos)", 2,
         "Google Drive (1 mes), ZIP comprimido, TransferNow (7 dias)"),
        ("ent_detalles_nom", "Etiqueta de los detalles", 1, "Detalles"),
        ("ent_detalles", "Detalles generales (vacio = no aparece la fila)", 4, ""),
    )),
    ("Packs de edicion", (
        ("edicion_basica_nom", "Nombre del pack basico", 1, "Edicion basica"),
        ("edicion_basica", "Que incluye, una linea por item", 8,
         "Balance de blancos (temperatura y matiz)\n"
         "Exposicion y contraste\n"
         "Iluminaciones, sombras, blancos y negros\n"
         "Textura, claridad y neblina\n"
         "Intensidad y saturacion\n"
         "Recorte de imagen"),
        ("edicion_completa_nom", "Nombre del pack completo", 1,
         "Edicion completa"),
        ("edicion_completa", "Que incluye, una linea por item", 8,
         "Todo lo de la edicion basica\n"
         "Mascaras y ajustes por zonas\n"
         "Retoque de piel\n"
         "Eliminacion de distracciones\n"
         "Reduccion de ruido (denoise)\n"
         "Adapto el preset o estilo de la liga"),
    )),
    ("Pasos de la compra", (
        ("compra_paso1", "Paso 1 \u2014 {n} son las fotos, {total} el precio", 2,
         "Me escribis por WhatsApp con el boton de aca abajo: el pedido va "
         "armado."),
        ("compra_paso2", "Paso 2", 2,
         "Te paso los datos, transferis {total} y me mandas el comprobante."),
        ("compra_paso3", "Paso 3", 3,
         "Te entrego las {n} fotos en calidad indicada de la manera a "
         "preferir, y el logo PrimeLeague-RB.Deportes_"),
    )),
    ("Pagina sobre mi", (
        ("mi_titulo", "Titulo", 1, "Sobre mi"),
        ("mi_cuerpo", "Texto. Una linea en blanco separa parrafos.", 14,
         "Soy Renzo. Fotografio deporte amateur y eventos: ligas "
         "universitarias, fechas de futbol, actos y presentaciones.\n\n"
         "Trabajo con volumen. Una fecha son 300 fotos en veinte minutos, y de "
         "ahi salen las diez que te importan a vos. Por eso arme mi propio "
         "sistema de entrega en vez de mandar carpetas de Drive que nadie "
         "termina de mirar: cada evento queda en una galeria donde vos marcas "
         "las tuyas y te las llevas.\n\n"
         "Y las fotos no viven en un servicio de terceros que manana cambia de "
         "precio o cierra: estan en un servidor mio."),
        ("mi_boton", "Boton al final (vacio = sin boton)", 1,
         "Escribime por WhatsApp"),
    )),
    ("Pie de todas las paginas", (
        ("pie", "Linea del pie", 1,
         "Fotografia deportiva y de eventos \u00b7 Chile"),
    )),
)

TODOS = tuple(c for _grupo, campos in CAMPOS for c in campos)
DEFAULTS = {c[0]: c[3] for c in TODOS}


def textos():
    """Los textos de las paginas: el default del codigo, pisado por lo editado."""
    vals = dict(DEFAULTS)
    for r in db().execute("SELECT clave, valor FROM texto"):
        if r["clave"] in vals:
            vals[r["clave"]] = r["valor"]
    return vals


def lineas(s):
    """Una vi\u00f1eta por linea, sin las vacias."""
    return [l.strip() for l in (s or "").splitlines() if l.strip()]


def parrafos(s):
    """Una linea en blanco separa parrafos."""
    return [p.strip() for p in (s or "").split("\n\n") if p.strip()]


def tramos(gal=None):
    """Los tramos de precio de una galeria, ordenados: [(fotos, precio), ...].

    Formato tolerante a proposito —"20 10000", "20 $10.000", "20, 10000" son
    lo mismo— porque esto lo escribe Renzo a mano en el panel. Una linea que
    no se entiende se ignora en vez de romper la galeria; si no queda ninguna,
    cae en los precios de siempre.
    """
    crudo = PRECIOS
    if gal is not None and "precios" in gal.keys() and gal["precios"]:
        crudo = gal["precios"]
    out = []
    for l in crudo.splitlines():
        p = l.replace("$", " ").replace(",", " ").replace(".", "").split()
        if len(p) >= 2 and p[0].isdigit() and p[1].isdigit():
            out.append((int(p[0]), int(p[1])))
    if not out and crudo != PRECIOS:
        return tramos()      # lo que escribio a mano no se entiende: los de siempre
    return sorted(out)


# ent_resolucion / ent_metodo / ent_plazo: NULL hereda el texto general,
# OCULTO saca la fila, cualquier otro valor se muestra tal cual.
OCULTO = "-"
ENTREGA_COLS = ("ent_resolucion", "ent_metodo", "ent_plazo")
RESOLUCIONES = ("1080p", "2K", "4K", "Original")
METODOS = ("Google Drive", "WeTransfer", "ZIP por correo")


def _campo(gal, col, texto):
    """El valor propio de la galeria, o el general si no puso nada."""
    if gal is not None and col in gal.keys() and (gal[col] or "").strip():
        v = gal[col].strip()
        return "" if v == OCULTO else v      # "" -> indicaciones() saca la fila
    return texto


def _dias(n):
    return "%d %s" % (n, "día" if n == 1 else "días")


def entrega_de_form(form, col):
    """Lo que se guarda en la columna segun lo elegido en el panel."""
    modo = (form.get(col + "_modo") or "").strip()
    if modo == "":
        return None                                     # hereda el general
    if modo == OCULTO:
        return OCULTO
    if modo == "otro":                                  # vacio = no aparece
        return (form.get(col + "_otro") or "").strip() or OCULTO
    if col == "ent_resolucion" and modo in RESOLUCIONES:
        return modo
    if col == "ent_metodo" and modo == "elegir":
        elegidos = [m for m in METODOS if m in form.getlist("ent_metodo_op")]
        return ", ".join(elegidos) or OCULTO
    if col == "ent_plazo" and modo == "dias":
        n = (form.get("ent_plazo_dias") or "").strip()
        return _dias(int(n)) if n.isdigit() and int(n) > 0 else OCULTO
    return None                                         # opcion inventada


def entrega_a_form(col, valor):
    """(modo, extra) para dibujar el selector a partir de lo guardado."""
    if not valor:
        return "", ""
    if valor == OCULTO:
        return OCULTO, ""
    if col == "ent_resolucion" and valor in RESOLUCIONES:
        return valor, ""
    if col == "ent_metodo":
        partes = [p.strip() for p in valor.split(",")]
        if all(p in METODOS for p in partes):
            return "elegir", partes
    if col == "ent_plazo":
        m = re.fullmatch(r"(\d+) d[ií]as?", valor)
        if m:
            return "dias", m.group(1)
    return "otro", valor


def edicion_de(gal):
    """(nombre del pack, [items]) de la edicion de esta galeria, o None.

    El detalle vive en los textos y no en cada galeria: si manana agregas un
    paso a la edicion completa, lo cambias una vez y vale para todas.
    """
    clave = (gal["ent_edicion"] or "").strip() if (
        gal is not None and "ent_edicion" in gal.keys()) else ""
    if clave not in EDICIONES:
        return None
    T = textos()
    items = lineas(T["edicion_%s" % clave])
    # se comparan por texto y no por posicion: si manana cambias ese renglon en
    # los textos generales, la omision deja de calzar y el renglon vuelve a
    # aparecer. Preferible que se vea de mas que esconder algo que ya no dice
    # lo mismo
    quita = set(lineas(gal["ent_quita"] if "ent_quita" in gal.keys() else None))
    return (T["edicion_%s_nom" % clave], [i for i in items if i not in quita])


def indicaciones(gal=None):
    """Las filas de "Que recibis": [(etiqueta, valor, [items]), ...].

    Cada fila es un campo propio y no una linea de texto libre: asi el panel
    puede ofrecer un selector para la edicion en vez de pedir que se escriba
    la lista entera a mano cada vez.
    """
    T = textos()
    ed = edicion_de(gal)
    filas = [
        (T["ent_resolucion_nom"],
         _campo(gal, "ent_resolucion", T["ent_resolucion"]), []),
        (T["ent_edicion_nom"], ed[0] if ed else "", ed[1] if ed else []),
        (T["ent_metodo_nom"], _campo(gal, "ent_metodo", T["ent_metodo"]), []),
        (T["ent_plazo_nom"], _campo(gal, "ent_plazo", T["ent_plazo"]), []),
        (T["ent_detalles_nom"],
         _campo(gal, "ent_detalles", T["ent_detalles"]), []),
    ]
    return [f for f in filas if f[1] or f[2]]


def rango_captura(gid):
    """(primera, ultima) hora de captura de la galeria, o None.

    Se calcula sobre timestamps completos y no sobre la hora suelta: una
    fecha que cruza la medianoche seguiria siendo un rango continuo.
    """
    r = db().execute(
        "SELECT MIN(tomada) a, MAX(tomada) b FROM foto "
        "WHERE galeria_id = ? AND tomada IS NOT NULL", (gid,)).fetchone()
    if r is None or not r["a"]:
        return None
    try:
        a, b = datetime.fromisoformat(r["a"]), datetime.fromisoformat(r["b"])
    except ValueError:
        return None
    return (a, b) if b > a else None


def _minutos(valor, defecto, tope):
    try:
        return min(tope, max(0, int(valor)))
    except (TypeError, ValueError):
        return defecto


# Como se puede ordenar la galeria. La expresion SQL sale de aca y NO de la
# URL: lo que llega por ?por= solo elige una de estas claves.
ORDENES = {
    "captura": ("Hora de captura", "COALESCE(foto.tomada, '')"),
    "likes": ("Mas elegidas", "(SELECT COUNT(DISTINCT l.cliente_id) "
                              "FROM lista_foto lf JOIN lista l ON l.id = lf.lista_id "
                              "WHERE lf.foto_id = foto.id)"),
}
# el desempate y el fallback siguen siendo el orden con el que entraron: una
# galeria sin fechas EXIF se ordena como antes en vez de quedar al azar
DESEMPATE = "foto.orden"


def precio_de(n, tr):
    """Lo que sale llevarse n fotos: el tramo mas chico que las cubra."""
    if n <= 0 or not tr:
        return 0
    for cant, precio in tr:
        if n <= cant:
            return precio
    cant, precio = tr[-1]
    # ponytail: arriba del ultimo tramo se sigue al precio unitario de ESE
    # tramo, redondeado a la centena. Si vendes packs mas grandes seguido,
    # agregale el tramo al evento y esta rama no se usa mas.
    return precio + round((n - cant) * precio / cant / 100) * 100


# Como se puede ordenar la galeria. La expresion SQL sale de aca y NO de la
# URL: lo que llega por ?por= solo elige una de estas claves.
ORDENES = {
    "captura": ("Hora de captura", "COALESCE(foto.tomada, '')"),
    "likes": ("Mas elegidas", "(SELECT COUNT(DISTINCT l.cliente_id) "
                              "FROM lista_foto lf JOIN lista l ON l.id = lf.lista_id "
                              "WHERE lf.foto_id = foto.id)"),
}
# el desempate y el fallback siguen siendo el orden con el que entraron: una
# galeria sin fechas EXIF se ordena como antes en vez de quedar al azar
DESEMPATE = "foto.orden"


def grupos_de(gid):
    """Los partidos de una galeria: [(nombre, "#hex,#hex", cuantas), ...].

    Sale de las propias fotos, asi que una galeria sin agrupar no muestra
    nada y no hay una tabla mas que mantener.
    """
    return db().execute(
        "SELECT grupo, MIN(grupo_col) col, COUNT(*) n FROM foto "
        "WHERE galeria_id = ? AND grupo IS NOT NULL AND grupo <> '' "
        "GROUP BY grupo ORDER BY grupo", (gid,)).fetchall()


def como_ordenar(args):
    """(clave, ASC|DESC) a partir de la query. Las mas elegidas arrancan al
    reves: ahi lo que interesa es el techo."""
    por = args.get("por")
    por = por if por in ORDENES else "captura"
    d = args.get("dir")
    if d not in ("asc", "desc"):
        d = "desc" if por == "likes" else "asc"
    return por, d.upper()


def fecha_corta(s):
    """2026-08-20T19:00 -> 20/08/2026. Lo que no parsea vuelve tal cual."""
    try:
        return date.fromisoformat((s or "")[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return s or ""


def estado(gal, hoy):
    """Los chips de estado de una galeria. Uno solo para el indice y la ficha:
    duplicados, uno de los dos se olvidaba de actualizar."""
    ch = []
    if "tipo" in gal.keys() and gal["tipo"]:
        ch.append(("", TIPO_NOMBRE.get(gal["tipo"], gal["tipo"]).lower()))
    if gal["expira"] and gal["expira"] < hoy:
        ch.append(("no", "vencida"))
    elif gal["expira"]:
        ch.append(("", "hasta " + fecha_corta(gal["expira"])))
    if gal["venta"]:
        ch.append(("si", "en venta"))
    if gal["clave_hash"]:
        ch.append(("", "con clave"))
    if gal["descargas"]:
        ch.append(("", "descarga"))
    if not gal["marca"]:
        ch.append(("", "sin marca"))
    return Markup("".join('<span class="chip %s">%s</span>' % c for c in ch))


def publicas(limite=None):
    """Las galerias en venta, la mas nueva primero. Son las que se listan.

    Un solo interruptor hace las dos cosas —aparecer en la vidriera y dejar
    comprar— porque en la practica no hay una sin la otra: un evento que no
    esta a la venta no tiene por que estar en la vidriera.

    Una vencida o vacia se cae sola: listar una galeria que al abrirla dice
    "vencio" es peor que no listarla.
    """
    filas = [g for g in db().execute(
        "SELECT galeria.*, "
        "  (SELECT COUNT(*) FROM foto WHERE galeria_id = galeria.id) AS fotos "
        "FROM galeria WHERE venta = 1 "
        "ORDER BY COALESCE(fecha, creada) DESC, id DESC").fetchall()
        if not expirada(g) and g["fotos"]]
    return filas[:limite] if limite else filas


@app.template_global()
def srcset(url, grande=None, webp=False):
    """Las variantes chicas de una foto de la vidriera, para srcset.

    El telefono baja la de 480/800 en vez de la de 1400: era 1,3 MB de mas en
    la portada segun PageSpeed. La ultima entrada es la URL tal cual, que
    sigue sirviendo el tamaño de siempre (y es la que usa el og:image).
    """
    sep = "&" if "?" in url else "?"
    fin = "%sf=webp" % sep if webp else ""
    sep = "&" if "?" in url + fin else "?"
    return ", ".join(["%s%s%sw=%d %dw" % (url, fin, sep, w, w) for w in ANCHOS]
                     + ["%s%s %dw" % (url, fin, grande or ANCHO_MUESTRA)])


@app.template_global()
def foto(url, sizes, grande=None, clase=None, **attrs):
    """<picture> con WebP y el JPEG de respaldo, para la vidriera.

    El WebP va en un <source> y no directo en el srcset del <img>: un Safari
    de iOS 12-13 (un iPhone 6 no pasa de ahi) no lo dibuja, y con <picture>
    se queda con el JPEG solo. picture{display:contents} en el CSS hace que
    el <img> se acomode como si el envoltorio no estuviera.
    """
    if clase:
        attrs["class"] = clase
    extra = Markup("").join(Markup(' {}="{}"').format(k, v)
                            for k, v in attrs.items())
    fuente = Markup('<source type="image/webp" srcset="{}" sizes="{}">').format(
        srcset(url, grande, webp=True), sizes) if WEBP_OK else Markup("")
    return Markup('<picture>{}<img src="{}" srcset="{}" sizes="{}"{}></picture>'
                  ).format(fuente, url, srcset(url, grande), sizes, extra)


@app.template_global()
def url_muestra(nombre):
    """La URL de una foto de la vidriera, versionada por su fecha de cambio.

    Con ?h= el navegador y Cloudflare la guardan un año sin preguntar
    (immutable): si Renzo reemplaza la foto con el mismo nombre, cambia el
    mtime, cambia la URL y se baja la nueva. Sin version habia que elegir
    entre cache corto o fotos viejas.
    """
    try:
        h = int((MUESTRAS / nombre).stat().st_mtime)
    except OSError:
        h = 0
    return "/muestra/%s?h=%d" % (urllib.parse.quote(nombre), h)


@app.template_global()
def url_tapa(gal):
    """La URL de la tapa de una galeria, versionada por lo que la dibuja.

    ponytail: la version sale de la fila de la galeria (foto elegida, marca,
    logos, firma, cuantas fotos tiene), no del disco: leer el mtime de cada
    tapa en /eventos son N stats al HDD por pagina. Lo que no ve: reemplazar
    el archivo de la foto o un logo con el mismo nombre. Para eso, cambiar la
    portada y volverla a poner.
    """
    campos = ("portada_foto_id", "fotos", "marca", "marca_texto", "logos",
              "firma", "firma_pos", "firma_tam")
    ks = gal.keys()
    h = hashlib.sha1(repr([gal[k] if k in ks else None for k in campos])
                     .encode()).hexdigest()[:10]
    return "/portada/%s?h=%s" % (gal["slug"], h)


def _ancho_pedido():
    """El ?w= de la request si es uno de ANCHOS, o None."""
    w = request.args.get("w", "")
    return int(w) if w.isdigit() and int(w) in ANCHOS else None


def _webp_pedido():
    """?f=webp, si este Pillow sabe escribirlo."""
    return WEBP_OK and request.args.get("f") == "webp"


UN_ANO = 365 * 24 * 3600


def servir_vidriera(archivo, max_age):
    """Manda una foto de la vidriera (JPEG o WebP, segun como se genero), con
    cache de un año si la URL viene versionada (?h=)."""
    tipo = "image/webp" if Path(archivo).suffix == ".webp" else "image/jpeg"
    versionada = bool(request.args.get("h"))
    r = send_file(archivo, mimetype=tipo,
                  max_age=UN_ANO if versionada else max_age)
    if versionada:
        r.cache_control.immutable = True
    return r


def _viz(plantilla, titulo=None, desc=None, og_img=None, indexar=False,
         precarga=None, **kw):
    """Todas las paginas publicas comparten los mismos datos de cabecera."""
    kw.setdefault("seccion", "")
    T0 = textos()
    return render_template_string(
        BASE_HTML,
        titulo=titulo or ("rb.fotos \u00b7 " + T0["hero_titulo"]),
        desc=desc or T0["hero_bajada"], og_img=og_img,
        indexar=indexar, canonical=SITIO + request.path, precarga=precarga,
        cuerpo=render_template_string(
        plantilla, wsp=WHATSAPP, fecha_corta=fecha_corta, T=textos(),
        lineas=lineas, parrafos=parrafos, tramos=tramos, precio_de=precio_de,
        pesos=pesos, **kw))


# --------------------------------------------------------------------- vistas

@app.get("/")
def home():
    fotos = muestras()
    return _viz(PORTADA, fotos=fotos, eventos=publicas(6), seccion="inicio",
                og_img=("%smuestra/%s" % (request.url_root, fotos[0])
                        if fotos else None),
                indexar=True,
                precarga=url_muestra(fotos[0]) if fotos else None)


@app.get("/sobre-mi")
def sobre_mi():
    return _viz(SOBREMI, seccion="sobre-mi", titulo="Sobre mi \u00b7 rb.fotos",
                indexar=True)


@app.get("/calculadora")
def calculadora():
    """Todavia no calcula nada: es la pagina que dice que viene."""
    return _viz(CALCULADORA, seccion="calculadora",
                titulo="Calculadora de cotizacion \u00b7 rb.fotos")


@app.get("/eventos")
def eventos_publicos():
    """La vidriera de eventos. Solo lista: para ver las fotos hay que entrar
    a la galeria como siempre, con el email."""
    evs = publicas()
    return _viz(EVENTOS, eventos=evs, seccion="eventos", indexar=True,
                titulo="Eventos \u00b7 rb.fotos",
                desc="%d eventos publicados" % len(evs) if evs else None,
                og_img=("%sportada/%s?og=1&v=%s" % (request.url_root, evs[0]["slug"], evs[0]["portada_foto_id"])
                        if evs else None))


# lo que se indexa: las mismas paginas que piden indexar=True en _viz
INDEXABLES = ("/", "/eventos", "/sobre-mi")


@app.get("/robots.txt")
def robots():
    """El panel y la API fuera. /g/ NO se bloquea aca a proposito: un
    Disallow impide que el buscador lea el noindex de la galeria, y entonces
    puede listar el link igual (sin contenido) si alguien lo publico."""
    return Response("User-agent: *\nDisallow: /panel\nDisallow: /api/\n\n"
                    "Sitemap: %s/sitemap.xml\n" % SITIO, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    urls = "".join("<url><loc>%s%s</loc></url>" % (SITIO, p) for p in INDEXABLES)
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
                    '"http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n'
                    % urls, mimetype="application/xml")


@lru_cache(maxsize=1)
def _icono():
    """El icono del sitio: "rb" y el punto verde de la marca sobre el negro
    de la pagina. Se dibuja aca, una vez por proceso, en vez de ser un archivo
    mas que llevar a la Pi y que se pueda perder."""
    lado = 256
    im = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, lado - 1, lado - 1), radius=48, fill="#090C0A")
    letra = _fuente(150)
    x0, y0, x1, y1 = d.textbbox((0, 0), "rb", font=letra)
    punto = 34
    x = (lado - (x1 - x0) - punto - 8) // 2 - x0
    y = (lado - (y1 - y0)) // 2 - y0
    d.text((x, y), "rb", font=letra, fill="#F0F3EA")
    px = x + x1 + 8
    d.ellipse((px, y + y1 - punto, px + punto, y + y1), fill="#C6F04A")
    return im


@app.get("/favicon.ico")
def favicon():
    buf = io.BytesIO()
    _icono().save(buf, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    buf.seek(0)
    return send_file(buf, mimetype="image/x-icon", max_age=604800)


@app.get("/apple-touch-icon.png")
def icono_apple():
    """El que usa el iPhone al "agregar a inicio": cuadrado y sin
    transparencia, iOS le redondea las esquinas solo."""
    fondo = Image.new("RGB", (256, 256), "#090C0A")
    fondo.paste(_icono(), mask=_icono())
    buf = io.BytesIO()
    fondo.resize((180, 180), Image.LANCZOS).save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=604800)


# El beacon de Cloudflare lo inyecta el proxy, sin nuestro nonce: va por
# nombre. 'unsafe-inline' en los estilos se queda: son 128 atributos style=
# en las plantillas y sacarlos no cambia el riesgo real (no ejecutan codigo).
CSP = ("default-src 'self'; "
       "script-src 'self' 'nonce-%s' https://static.cloudflareinsights.com; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
       "connect-src 'self' https://cloudflareinsights.com; "
       "form-action 'self'; frame-ancestors 'self'; base-uri 'none'; "
       "object-src 'none'; report-uri /csp-reportes")
CSP_TOPE = 1_000_000          # un buzon publico no puede llenar el disco


def csp_log():
    """Se calcula al llamar, no al importar: el self-check mueve CACHE."""
    return CACHE / "csp.log"


@app.before_request
def _nonce():
    g.nonce = secrets.token_urlsafe(12)


@app.post("/csp-reportes")
def csp_reportes():
    """Buzon de los avisos de la CSP. Publico a proposito: lo manda el
    navegador del visitante, no nuestro codigo."""
    try:
        if csp_log().exists() and csp_log().stat().st_size > CSP_TOPE:
            return "", 204                      # ya hay de sobra para mirar
        datos = request.get_json(silent=True, force=True) or {}
        r = datos.get("csp-report", datos)
        linea = {
            "cuando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pagina": str(r.get("document-uri") or r.get("documentURL") or "")[:200],
            "regla": str(r.get("violated-directive") or r.get("effectiveDirective") or "")[:60],
            "bloqueado": str(r.get("blocked-uri") or r.get("blockedURL") or "")[:200],
            "muestra": str(r.get("script-sample") or "")[:120],
        }
        CACHE.mkdir(parents=True, exist_ok=True)
        with open(csp_log(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception:
        pass                                     # un buzon no devuelve errores
    return "", 204


def csp_resumen():
    """Que habria bloqueado la CSP, agrupado. Para decidir si se puede activar."""
    if not csp_log().exists():
        print("todavia no llego ningun aviso (%s)" % csp_log())
        return
    cuenta, paginas, desde = {}, {}, None
    for l in csp_log().read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(l)
        except ValueError:
            continue
        desde = desde or d["cuando"]
        clave = (d["regla"], d["bloqueado"] or d["muestra"][:40])
        cuenta[clave] = cuenta.get(clave, 0) + 1
        paginas.setdefault(clave, set()).add(d["pagina"])
    print("%d avisos distintos desde %s" % (len(cuenta), desde))
    for (regla, que), n in sorted(cuenta.items(), key=lambda x: -x[1]):
        ps = sorted(paginas[(regla, que)])
        print("  %5d  %-28s %s" % (n, regla, que or "(en linea)"))
        print("         en: %s%s" % (", ".join(ps[:3]), " ..." if len(ps) > 3 else ""))


@app.after_request
def cabeceras(resp):
    """Lo que PageSpeed pedia en "Confianza y seguridad".

    - HSTS: el navegador no vuelve a intentar http://. Un año, sin
      includeSubDomains ni preload a proposito: esas dos son las que no se
      pueden deshacer rapido. Para sacarlo: max-age=0, y esperar a que cada
      navegador vuelva a pasar.
    - X-Frame-Options: nadie puede meter el sitio en un iframe ajeno y hacer
      clickear "comprar" o "me gusta" por debajo. El sitio no usa iframes.
    - nosniff: el navegador respeta el tipo que mandamos.
    Los navegadores ignoran HSTS sobre http, asi que en local no molesta.
    """
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    # COOP: una ventana que abrimos con target=_blank no puede tocar esta.
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    # el link secreto de una galeria no viaja en el Referer hacia otro sitio
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # el sitio no usa camara, microfono ni ubicacion: que nadie los pida
    resp.headers.setdefault("Permissions-Policy",
                            "camera=(), microphone=(), geolocation=(), payment=()")
    nonce = g.get("nonce")
    if nonce:
        resp.headers.setdefault("Content-Security-Policy-Report-Only", CSP % nonce)
        # el nonce va en los <script> del sitio aca y no en cada plantilla:
        # son 17 repartidos en 11 paginas y se olvidaria uno
        if resp.mimetype == "text/html" and not resp.direct_passthrough:
            cuerpo = resp.get_data()
            if b"<script>" in cuerpo:
                resp.set_data(cuerpo.replace(
                    b"<script>", b'<script nonce="%s">' % nonce.encode()))
    return resp


def galerias_de_carpeta(cx, cid):
    """Las galerias de una carpeta, como publicas(): sin vencidas ni vacias."""
    return [g for g in cx.execute(
        "SELECT galeria.*, "
        "  (SELECT COUNT(*) FROM foto WHERE galeria_id = galeria.id) AS fotos "
        "FROM galeria JOIN carpeta_galeria ON carpeta_galeria.galeria_id = galeria.id "
        "WHERE carpeta_galeria.carpeta_id = ? "
        "ORDER BY COALESCE(fecha, creada) DESC, galeria.id DESC", (cid,)).fetchall()
        if not expirada(g) and g["fotos"]]


@app.get("/c/<slug>")
def carpeta_publica(slug):
    """Un link que junta varias galerias. Solo lista: a cada una se entra
    como siempre, con el email."""
    c = db().execute("SELECT * FROM carpeta WHERE slug = ?", (slug,)).fetchone()
    if c is None:
        abort(404)
    evs = galerias_de_carpeta(db(), c["id"])
    return _viz(CARPETA, carpeta=c, eventos=evs,
                titulo="%s \u00b7 rb.fotos" % c["nombre"],
                desc="%d galerias" % len(evs),
                og_img=("%sportada/%s?og=1&v=%s" % (request.url_root, evs[0]["slug"], evs[0]["portada_foto_id"])
                        if evs else None))


@app.get("/g/<slug>/comprar")
def comprar(slug):
    """Lo que hay que pagar y a que cuenta. El pedido es la seleccion que ya
    esta en la DB, asi que no hay tabla de ordenes que mantener."""
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal) or not gal["venta"] or gal["descargas"]:
        abort(404)
    if gal["id"] not in entradas():
        return redirect(url_for("galeria", slug=slug))
    cid = cliente_actual(gal["id"])

    # se compra UNA lista: el carrito por defecto, o la que se elija. La
    # consulta ya esta acotada al cliente, asi que un ?lista= ajeno no aparece
    # aca y cae en el carrito en vez de mostrar la seleccion de otro.
    if cid:
        carrito(db(), cid)
        db().commit()
    listas = db().execute(
        "SELECT lista.id, lista.nombre, "
        "  (SELECT COUNT(*) FROM lista_foto WHERE lista_id = lista.id) n "
        "FROM lista WHERE cliente_id = ? ORDER BY lista.nombre <> ?, orden, id",
        (cid, CARRITO)).fetchall() if cid else []
    elegida = None
    if request.args.get("lista", "").isdigit():
        pedida = int(request.args["lista"])
        elegida = next((l for l in listas if l["id"] == pedida), None)
    if elegida is None:
        elegida = listas[0] if listas else None

    n = elegida["n"] if elegida else 0
    fila = db().execute("SELECT email FROM cliente WHERE id = ?",
                        (cid,)).fetchone() if cid else None
    correo = fila["email"] if fila else ""
    total = precio_de(n, tramos(gal))
    codigo = codigo_lista(elegida["id"]) if elegida else ""

    # las que se lleva, para verlas al lado del total. Con tope: alguien que
    # marco 800 fotos no necesita 800 miniaturas para decidir que compra
    fotos = db().execute(
        "SELECT foto.id FROM lista_foto JOIN foto ON foto.id = lista_foto.foto_id "
        "WHERE lista_foto.lista_id = ? ORDER BY foto.orden, foto.id LIMIT 120",
        (elegida["id"],)).fetchall() if elegida else []

    # el correo NO va en el mensaje: viaja el ID, que no dice quien es y le
    # sirve a Renzo para encontrar la lista en el panel
    msg = ("Hola Renzo, El ID de las fotos que quiero comprar es: *%s*, "
           "son %d fotos de \"%s\" (%s). Para cuando las crees poder tener?"
           % (codigo, n, gal["nombre"], pesos(total)))
    # {n} y {total} salen del pedido; el resto lo escribe Renzo en el panel
    T = textos()
    pasos = [T["compra_paso%d" % i].replace("{n}", str(n))
             .replace("{total}", pesos(total)) for i in (1, 2, 3)]
    return _viz(COMPRAR, gal=gal, n=n, total=pesos(total), correo=correo,
                msg=msg, tarifas=tramos(gal), listas=listas, elegida=elegida,
                fotos=fotos, codigo=codigo, pasos=pasos)


@app.get("/muestra/<nombre>")
def muestra(nombre):
    """Foto de la portada. Es publica a proposito: es la vidriera."""
    f = MUESTRAS / nombre
    # nombre plano y nada mas: ni subcarpetas ni ..
    if ("/" in nombre or "\\" in nombre or not f.is_file()
            or f.resolve().parent != MUESTRAS.resolve()):
        abort(404)
    try:
        return servir_vidriera(sin_marca(f, _ancho_pedido(), _webp_pedido()),
                               604800)
    except Exception:
        app.logger.exception("no pude generar la muestra %s", f)
        abort(404)


def _tarjeta(gal, cuantas=None):
    """Titulo, bajada e imagen para la vista previa del link en un chat."""
    dia = fecha_corta(gal["fecha"] or gal["creada"])
    partes = [p for p in (("%d fotos" % cuantas) if cuantas else None, dia) if p]
    img = None
    if tiene_tapa(gal):
        img = "%sportada/%s?og=1&v=%s" % (request.url_root, gal["slug"], gal["portada_foto_id"])
    return {"titulo": "Galeria de %s" % gal["nombre"],
            "desc": " \u00b7 ".join(partes) or "rb.fotos",
            "og_img": img}


@app.get("/g/<slug>")
def galeria(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    if expirada(gal):
        return render_template_string(BASE_HTML,
                                      cuerpo=render_template_string(EXPIRADA, gal=gal))
    if gal["id"] not in entradas():
        return render_template_string(
            BASE_HTML, cuerpo=render_template_string(
                GATE, gal=gal, error=request.args.get("error")),
            **_tarjeta(gal, db().execute(
                "SELECT COUNT(*) c FROM foto WHERE galeria_id = ?",
                (gal["id"],)).fetchone()["c"]))
    if slug == VIDEOS_SLUG:
        return videos(gal)

    cid = cliente_actual(gal["id"])
    # el criterio y la direccion salen de ?por= y ?dir=. Lo que antes era
    # "ver las ultimas primero" es ahora "como las tome, descendente".
    por, dire = como_ordenar(request.args)

    # franja horaria: los sliders mandan minutos desde la primera foto, no
    # horas del reloj. Asi el rango siempre existe aunque el evento cruce la
    # medianoche, y el slider no ofrece horas en las que no hay nada.
    grupos = grupos_de(gal["id"])
    grupo = request.args.get("grupo") or ""
    if grupo and grupo not in [g["grupo"] for g in grupos]:
        grupo = ""      # un ?grupo= inventado no filtra a la nada

    rango = rango_captura(gal["id"])
    largo = int((rango[1] - rango[0]).total_seconds() // 60) if rango else 0
    vdesde, vhasta = 0, largo
    if rango:
        vdesde = _minutos(request.args.get("desde"), 0, largo)
        vhasta = _minutos(request.args.get("hasta"), largo, largo)
        if vdesde > vhasta:
            vdesde, vhasta = vhasta, vdesde
    # donde se puede descargar no hay nada que vender: la descarga gana
    vende = bool(gal["venta"] and not gal["descargas"])

    # ?lista=<id> muestra solo esa lista. Se valida contra el cliente: sin eso
    # los id de lista serian enumerables y se verian selecciones ajenas.
    lista = None
    if request.args.get("lista", "").isdigit():
        lista = db().execute("SELECT * FROM lista WHERE id = ? AND cliente_id = ?",
                             (int(request.args["lista"]), cid)).fetchone()
        if lista is None:
            abort(404)

    # ?bajadas=1: las que el cliente se bajo. No es una lista de verdad, se lee
    # de `descarga`: por eso se llena sola y no hay como sacar una de ahi
    bajadas = request.args.get("bajadas") == "1" and lista is None
    # ?repetidas=1: las que estan en mas de una lista, para limpiar la seleccion
    repetidas = request.args.get("repetidas") == "1" and lista is None and not bajadas
    if bajadas:
        lista = {"id": "", "nombre": "Descargadas"}
        donde = ("FROM foto WHERE galeria_id = ? AND foto.id IN "
                 "(SELECT foto_id FROM descarga WHERE cliente_id = ?)")
        args = (gal["id"], cid)
    elif repetidas:
        lista = {"id": "", "nombre": "Repetidas entre listas"}
        donde = "FROM foto WHERE galeria_id = ? AND foto.id IN (" + REPETIDAS + ")"
        args = (gal["id"], cid, CARRITO)
    elif lista:
        donde = ("FROM foto JOIN lista_foto ON lista_foto.foto_id = foto.id "
                 "WHERE foto.galeria_id = ? AND lista_foto.lista_id = ?")
        args = (gal["id"], lista["id"])
    else:
        donde = "FROM foto WHERE galeria_id = ?"
        args = (gal["id"],)
    if grupo:
        donde += " AND foto.grupo = ?"
        args = args + (grupo,)
    if rango and (vdesde > 0 or vhasta < largo):
        donde += " AND foto.tomada BETWEEN ? AND ?"
        args = args + (
            (rango[0] + timedelta(minutes=vdesde)).strftime("%Y-%m-%d %H:%M:%S"),
            # +1 minuto para que el tope del slider incluya su propio minuto
            (rango[0] + timedelta(minutes=vhasta + 1)).strftime("%Y-%m-%d %H:%M:%S"))
    total = db().execute(f"SELECT COUNT(*) c {donde}", args).fetchone()["c"]
    paginas = max(1, -(-total // PAGINA))
    trozo = request.args.get("trozo")
    try:
        n = int(trozo) if trozo else int(request.args.get("p", 1))
    except ValueError:
        n = 1
    if trozo and n > paginas:
        return ""          # se acabo: asi el scroll infinito sabe que parar
    p = min(max(1, n), paginas)

    fotos = db().execute(
        "SELECT foto.id, foto.raw IS NOT NULL AS tiene_raw, "
        "  foto.id IN (" + EN_TODAS + ") AS mia, "
        "  EXISTS(SELECT 1 FROM lista_foto JOIN lista ON lista.id = lista_foto.lista_id"
        "         WHERE lista_foto.foto_id = foto.id AND lista.cliente_id = ?"
        "           AND lista.nombre = ?) AS en_carro, "
        "  (SELECT COUNT(*) FROM comentario WHERE foto_id = foto.id) AS ncom, "
        "  (SELECT GROUP_CONCAT(lista.nombre, ' + ') FROM lista_foto"
        "     JOIN lista ON lista.id = lista_foto.lista_id"
        "    WHERE lista_foto.foto_id = foto.id AND lista.cliente_id = ?"
        "      AND lista.nombre <> ?) AS en_listas "
        f"{donde} ORDER BY {ORDENES[por][1]} {dire}, {DESEMPATE} {dire}, "
        f"foto.id {dire} LIMIT ? OFFSET ?",
        (cid, cid, cid, CARRITO, cid, CARRITO) + args
        + (PAGINA, (p - 1) * PAGINA)).fetchall()
    marcadas = cuenta_marcadas(db(), cid)
    if trozo:
        # solo las celdas: lo que el scroll pega al final de la grilla
        return render_template_string(CELDAS, gal=gal, fotos=fotos, vende=vende,
                                      repetidas=repetidas)
    return render_template_string(BASE_HTML, **_tarjeta(gal, total),
        cuerpo=render_template_string(
        GRILLA, gal=gal, fotos=fotos, total=total, marcadas=marcadas,
        p=p, paginas=paginas, lista=lista, marca=gal["marca_texto"] or MARCA,
        tapa=tiene_tapa(gal), tarifas=tramos(gal), pesos=pesos, T=textos(),
        entrega=indicaciones(gal),
        vende=vende, por=por, dire=dire, ordenes=ORDENES, repetidas=repetidas,
        bajadas=bajadas, terminada=db().execute(
            "SELECT 1 FROM seleccion_lista WHERE galeria_id = ? AND cliente_id = ?",
            (gal["id"], cid)).fetchone() is not None,
        rango=rango, largo=largo, vdesde=vdesde, vhasta=vhasta,
        grupos=grupos, grupo=grupo,
        t0=(rango[0].hour * 60 + rango[0].minute) if rango else 0, ncarro=db().execute(
            "SELECT COUNT(*) c FROM lista_foto "
            "JOIN lista ON lista.id = lista_foto.lista_id "
            "WHERE lista.cliente_id = ? AND lista.nombre = ?",
            (cid, CARRITO)).fetchone()["c"]))


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
        "INSERT INTO cliente (galeria_id, email, visto, nombre) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(galeria_id, email) DO UPDATE SET visto = excluded.visto, "
        # volver a entrar sin escribir el nombre no borra el que ya habia dado
        "  nombre = COALESCE(NULLIF(excluded.nombre, ''), cliente.nombre)",
        (gal["id"], email, ahora_iso(),
         (request.form.get("nombre") or "").strip()[:60]))
    cx.commit()
    cid = cx.execute("SELECT id FROM cliente WHERE galeria_id = ? AND email = ?",
                     (gal["id"], email)).fetchone()["id"]
    favoritos(cx, cid)          # todo cliente tiene la suya desde el minuto cero
    asegurar_listas(cx, gal, cid)
    cx.commit()

    session["galerias"] = sorted(entradas() | {gal["id"]})
    clientes = session.get("clientes") or {}
    clientes[str(gal["id"])] = cid
    session["clientes"] = clientes
    session.permanent = True
    return redirect(url_for("galeria", slug=slug))


# ------------------------------------------------------ galeria de videos

def frigate(ruta, metodo="GET", **query):
    url = FRIGATE + ruta + ("?" + urllib.parse.urlencode(query) if query else "")
    # el nginx de Frigate cachea la API 5 s: sin el bypass, un corazon recien
    # sacado seguia apareciendo en "Guardados" al recargar
    return urllib.request.urlopen(urllib.request.Request(
        url, method=metodo, headers={"X-Cache-Bypass": "1"}), timeout=30)


def cual_comida(inicio):
    """"Comida 13:30" si esta visita cae en un horario del dispensador."""
    for h in COMIDAS:
        try:
            hh, mm = (int(x) for x in h.split(":"))
        except ValueError:
            continue
        pauta = inicio.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if -COMIDA_ANTES <= (inicio - pauta).total_seconds() <= COMIDA_DESPUES:
            return h
    return None


def visitas_de(eventos):
    """Junta los clips seguidos en una visita.

    Sin esto, una sola comida se ve como 18 tarjetas del mismo minuto.
    """
    utiles = [e for e in eventos
              if e.get("has_clip") and EVENTO_ID.match(str(e.get("id", "")))]
    utiles.sort(key=lambda e: e["start_time"], reverse=True)
    visitas = []
    for e in utiles:
        fin = e.get("end_time") or e["start_time"]
        if visitas and visitas[-1]["desde"] - fin < HUECO_VISITA:
            v = visitas[-1]
        else:
            v = {"clips": [], "desde": e["start_time"], "hasta": fin,
                 "etiquetas": set(), "guardado": True}
            visitas.append(v)
        # llegan de la mas nueva a la mas vieja: el clip mas viejo va primero
        v["clips"].insert(0, e["id"])
        v["desde"] = min(v["desde"], e["start_time"])
        v["hasta"] = max(v["hasta"], fin)
        v["etiquetas"].add(ETIQUETAS.get(e.get("label"), e.get("label")))
        v["guardado"] = v["guardado"] and bool(e.get("retain_indefinitely"))
        v["portada"] = e["id"]      # la miniatura es la del clip mas viejo
    for v in visitas:
        ini = datetime.fromtimestamp(v["desde"])
        largo = round(v["hasta"] - v["desde"])
        v["hora"] = ini.strftime("%d/%m %H:%M")
        v["quien"] = " y ".join(sorted(v["etiquetas"]))
        v["dur"] = ("%d:%02d min" % (largo // 60, largo % 60)) if largo >= 60 else "%d s" % largo
        v["comida"] = cual_comida(ini)
    return visitas


def videos(gal):
    """La grilla de visitas. Frigate es la fuente: aca no se guarda nada."""
    que = request.args.get("que", "")
    if que not in ETIQUETAS and que not in ("guardados", "comidas"):
        que = ""
    q = {"cameras": FRIGATE_CAMARA, "limit": VIDEOS_PAGINA,
         "labels": que if que in ETIQUETAS else ",".join(ETIQUETAS)}
    if que == "guardados":
        q["favorites"] = 1
    try:
        dia = date.fromisoformat(request.args.get("dia", ""))
    except ValueError:
        dia = None
    if dia:
        # hora local de la Pi, que es la de la camara
        ini = datetime(dia.year, dia.month, dia.day)
        q["after"] = ini.timestamp()
        q["before"] = (ini + timedelta(days=1)).timestamp()
    antes = request.args.get("antes", "")
    if re.fullmatch(r"[0-9]{9,11}(\.[0-9]+)?", antes):
        q["before"] = min(float(antes), q.get("before", float(antes)))

    caido = False
    try:
        with frigate("/api/events", **q) as r:
            eventos = json.load(r)
    except (urllib.error.URLError, ValueError, OSError):
        eventos, caido = [], True

    items = visitas_de(eventos)
    # la visita mas vieja puede continuar en la pagina siguiente: se corta
    siguiente = eventos[-1]["start_time"] if len(eventos) == VIDEOS_PAGINA else None
    if siguiente and items:
        siguiente = items[-1]["hasta"] + 1
        items.pop()
    if que == "comidas":
        items = [v for v in items if v["comida"]]
    filtros = (("", "Todos"), ("dog", "Perros"), ("cat", "Gatos"),
               ("comidas", "\U0001F37D Comidas"), ("guardados", "♥ Guardados"))
    return render_template_string(BASE_HTML, **_tarjeta(gal), cuerpo=render_template_string(
        VIDEOS, gal=gal, items=items, que=que, dia=dia, filtros=filtros,
        siguiente=siguiente, caido=caido, comidas=COMIDAS))


def evento_de(slug, eid, consultar=True):
    """El evento, solo si el cliente entro a la galeria de videos.

    Sin el chequeo de entrada cualquiera con un id veria el clip; sin el
    regex, el id viajaria tal cual a la URL de Frigate.
    """
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if (gal is None or slug != VIDEOS_SLUG or gal["id"] not in entradas()
            or expirada(gal) or not EVENTO_ID.match(eid)):
        abort(404)
    if not consultar:
        return None
    try:
        with frigate(f"/api/events/{eid}") as r:
            ev = json.load(r)
    except (urllib.error.URLError, ValueError, OSError):
        abort(404)
    if ev.get("camera") != FRIGATE_CAMARA or ev.get("label") not in ETIQUETAS:
        abort(404)
    return ev


@app.get("/g/<slug>/v/<eid>.jpg")
def video_foto(slug, eid):
    evento_de(slug, eid, consultar=False)
    try:
        # limpia: sin los recuadros de deteccion ni la hora encima
        with frigate(f"/api/events/{eid}/snapshot.jpg", height=400,
                     bbox=0, timestamp=0) as r:
            datos = r.read()
    except (urllib.error.URLError, OSError):
        abort(404)
    return Response(datos, mimetype="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


def preparar_clip(eid):
    """Deja el clip listo en el cache y lo devuelve. None si no se pudo.

    Frigate arma el clip al vuelo y no acepta Range. Desde el disco si, que es
    lo que necesita el navegador para adelantar y el iPhone para reproducir.
    """
    destino = CACHE / "videos" / f"{eid}.mp4"
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    FF_PUENTE.mkdir(parents=True, exist_ok=True)
    marca = secrets.token_hex(4)
    crudo = FF_PUENTE / f"{eid}.{marca}.frag.mp4"
    plano = FF_PUENTE / f"{eid}.{marca}.mp4"
    try:
        try:
            with frigate(f"/api/events/{eid}/clip.mp4") as r, open(crudo, "wb") as f:
                shutil.copyfileobj(r, f)
            if crudo.stat().st_size == 0:
                return None
        except (urllib.error.URLError, OSError):
            return None
        try:
            adentro = "/media/frigate/" + FF_PUENTE.name
            hecho = subprocess.run(
                ["docker", "exec", FF_DOCKER, "ffmpeg", "-v", "error", "-y",
                 "-i", f"{adentro}/{crudo.name}", "-c", "copy",
                 "-movflags", "+faststart", f"{adentro}/{plano.name}"],
                capture_output=True, timeout=180).returncode == 0
        except (OSError, subprocess.SubprocessError):
            hecho = False
        # sin ffmpeg igual se sirve el crudo: se ve, pero sin barra de tiempo
        shutil.move(str(plano if hecho and plano.exists() else crudo), destino)
        return destino
    finally:
        for p in (crudo, plano):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def cachear_videos(dias=30):
    """Prepara los clips de lo recien grabado y borra los viejos. Lo corre el
    cron: asi abrir un video es servir un archivo que ya esta en el disco."""
    try:
        with frigate("/api/events", cameras=FRIGATE_CAMARA, limit=200,
                     labels=",".join(ETIQUETAS), after=time.time() - 3600) as r:
            eventos = json.load(r)
    except (urllib.error.URLError, ValueError, OSError) as e:
        sys.exit("frigate no responde: %s" % e)
    nuevos = listos = fallados = 0
    for e in eventos:
        if not e.get("end_time") or not e.get("has_clip"):
            continue
        if (CACHE / "videos" / f"{e['id']}.mp4").exists():
            listos += 1
        elif preparar_clip(e["id"]):
            nuevos += 1
        else:
            fallados += 1
    viejos, limite = 0, time.time() - dias * 86400
    for p in (CACHE / "videos").glob("*.mp4"):
        if p.stat().st_mtime < limite:
            p.unlink()
            viejos += 1
    print(f"clips: {nuevos} nuevos, {listos} ya estaban, {fallados} fallaron, "
          f"{viejos} borrados por viejos")


@app.get("/g/<slug>/v/<eid>.mp4")
def video_clip(slug, eid):
    ev = evento_de(slug, eid)
    if not ev.get("end_time"):
        abort(404)          # sigue grabando: el clip todavia no existe entero
    destino = preparar_clip(eid)
    if destino is None:
        abort(503)
    return send_file(destino, mimetype="video/mp4", conditional=True, max_age=86400)


@app.post("/g/<slug>/v/<eid>/guardar")
def video_guardar(slug, eid):
    """El corazon le dice a Frigate que no borre esta pasada a los 30 dias.

    Es de la galeria, no de cada cliente: lo que guarda uno lo ve guardado
    toda la familia. JSON obligatorio, como /api/megusta: un form de otro
    sitio no puede mandarlo.
    """
    evento_de(slug, eid)
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        abort(400)
    on = bool(datos.get("on"))
    try:
        frigate(f"/api/events/{eid}/retain", metodo="POST" if on else "DELETE").close()
    except (urllib.error.URLError, OSError):
        abort(503)
    return jsonify(guardado=on)


@app.get("/p/<int:foto_id>")
def ver_preview(foto_id):
    f = foto_autorizada(foto_id)
    if not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    try:
        # NULL o 1 = con marca. Solo un 0 explicito la saca, asi una galeria
        # creada antes de que existiera la columna sigue igual que siempre.
        # ?g=1 lo pide el visor: la misma foto, mas grande y con la misma
        # marca. La grilla sigue trayendo la de 1000 px.
        img = preview_de(f, foto_id, f["ruta"],
                         ANCHO_VISOR if request.args.get("g") else None)
    except Exception:
        # una foto ilegible rompe su miniatura, no la pagina entera del cliente
        app.logger.exception("no pude generar preview de %s", f["ruta"])
        abort(404)
    # solo la miniatura se anota sola. La grande la pide tambien la precarga
    # (3 adelante), asi que contarla aca inflaba "la abrio en grande": eso lo
    # avisa el visor con /api/visto cuando estuvo de verdad en pantalla.
    if not request.args.get("g"):
        anotar_vista(f["galeria_id"], foto_id, False)
    return send_file(img, mimetype="image/jpeg", max_age=86400)


def anotar_vista(galeria_id, foto_id, visor):
    """Deja constancia de que este cliente pidio esta foto.

    Es la unica forma de saber despues si miro el evento entero o abandono en
    el primer partido: el log del servicio lo dice, pero se rota solo y ademas
    no distingue quien pide, porque detras del tunel todos llegan como
    127.0.0.1.

    Cuenta por lo bajo, nunca por lo alto: send_file manda max_age, asi que la
    foto que el navegador ya tiene guardada no se vuelve a pedir y esa segunda
    mirada no queda registrada. "0 vistas" si es una respuesta firme; "5
    vistas" quiere decir al menos 5.
    """
    cid = cliente_actual(galeria_id)
    if not cid:
        return
    ahora = ahora_iso()
    cx = db()
    try:
        cx.execute(
            "INSERT INTO vista (cliente_id, foto_id, visor, primera, ultima, veces) "
            "VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT (cliente_id, foto_id, visor) DO UPDATE SET "
            "  ultima = excluded.ultima, veces = veces + 1",
            (cid, foto_id, 1 if visor else 0, ahora, ahora))
        cx.commit()
    except sqlite3.OperationalError:
        # llevar la cuenta no vale romper la foto: se pierde ESTA vista y ya
        cx.rollback()


def cobertura(cx, gid):
    """Cuanto vio cada cliente de cada grupo de fotos, para el panel."""
    total, orden = {}, []
    for r in cx.execute(
            "SELECT COALESCE(grupo, '') AS grupo, COUNT(*) AS n FROM foto "
            "WHERE galeria_id = ? GROUP BY grupo ORDER BY MIN(orden), MIN(id)",
            (gid,)):
        total[r["grupo"]] = r["n"]
        orden.append(r["grupo"])
    filas = {}
    for r in cx.execute(
            "SELECT cliente.email AS quien, COALESCE(foto.grupo, '') AS grupo, "
            "  vista.visor AS visor, COUNT(DISTINCT vista.foto_id) AS n, "
            "  MAX(vista.ultima) AS fin "
            "FROM vista JOIN foto ON foto.id = vista.foto_id "
            "JOIN cliente ON cliente.id = vista.cliente_id "
            # cliente de ESTA galeria: un id de cliente reusado (sqlite reusa el
            # mas alto al borrarlo) no puede heredar vistas ajenas
            "WHERE foto.galeria_id = ? AND cliente.galeria_id = foto.galeria_id "
            "GROUP BY quien, grupo, visor", (gid,)):
        f = filas.setdefault(
            (r["quien"], r["grupo"]),
            {"quien": r["quien"], "grupo": r["grupo"], "fin": "",
             "total": total.get(r["grupo"], 0), "grilla": 0, "visor": 0})
        f["visor" if r["visor"] else "grilla"] = r["n"]
        f["fin"] = max(f["fin"], r["fin"] or "")
    return sorted(filas.values(),
                  key=lambda f: (f["quien"], orden.index(f["grupo"])
                                 if f["grupo"] in orden else 99))


@app.get("/d/<int:foto_id>")
@app.get("/d/<int:foto_id>/<cual>")
def descargar(foto_id, cual="original"):
    f = foto_autorizada(foto_id, firmada=True)
    if not f["descargas"] or not f["descargable"]:
        abort(404)
    if cual == "raw" and not f["raw_descarga"]:
        abort(404)
    ruta = f["raw"] if cual == "raw" and f["raw"] else f["ruta"]
    if not dentro_de_raiz(ruta) or not os.path.exists(ruta):
        abort(404)
    # una fila por bajada, no una por foto: bajar dos veces la misma se ve
    cx = db()
    cx.execute("INSERT INTO descarga (cliente_id, foto_id, cual, cuando) "
               "VALUES (?, ?, ?, ?)",
               (cliente_actual(f["galeria_id"]) or cliente_firmado(f["galeria_id"]),
                f["id"], cual,
                datetime.now(timezone.utc).isoformat(timespec="seconds")))
    cx.commit()

    # el RAW se entrega crudo: firmarlo seria convertirlo en un JPEG
    salida = ruta
    if cual != "raw":
        try:
            salida = entregable(ruta, firma_de(f))
        except Exception:
            # que la firma falle no puede dejar al cliente sin su foto
            app.logger.exception("no pude firmar %s", ruta)
    return send_file(salida, as_attachment=True,
                     download_name=os.path.basename(ruta))


@app.get("/portada/<slug>")
def portada(slug):
    """La tapa se ve ANTES de entrar, asi que no pasa por foto_autorizada().

    Es el preview marcado al agua, nunca el original, y solo si la foto es de
    esa galeria: sin ese AND, un portada_foto_id apuntaria a cualquier foto.
    """
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal):
        abort(404)
    # sin portada elegida cae en la primera foto: una galeria sin tapa se veria
    # rota, y elegir tapa es opcional a proposito
    f = db().execute(
        "SELECT * FROM foto WHERE galeria_id = ? AND (? IS NULL OR id = ?) "
        "ORDER BY orden, id LIMIT 1",
        (gal["id"], gal["portada_foto_id"], gal["portada_foto_id"])).fetchone()
    if f is None or not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    # ?w= es la miniatura de la tarjeta de evento (srcset); sin el, la tapa
    # de siempre, que el gate usa de fondo a pantalla completa
    chica = _ancho_pedido()
    try:
        return servir_vidriera(
            preview(f["id"], f["ruta"], gal["marca_texto"],
                    # ?og=1 la pide chica: WhatsApp descarta las imagenes
                    # pesadas y no dibuja ninguna vista previa
                    chica or (1200 if request.args.get("og") else ANCHO_TAPA),
                    0.017, logos_de(gal), firma_de(gal), _webp_pedido())
            if gal["marca"] else sin_marca(f["ruta"], chica, _webp_pedido()),
            86400)
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
    # Descarga masiva apagada por decision: las fotos se bajan de a una. El
    # armado del zip queda abajo, sin uso; para reactivarlo se saca este abort.
    abort(404)
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal) or gal["id"] not in entradas():
        abort(404)
    if not gal["descargas"]:
        abort(404)

    if cuales == "elegidas":
        filas = db().execute(
            "SELECT DISTINCT foto.ruta, foto.orden FROM foto "
            "JOIN lista_foto ON lista_foto.foto_id = foto.id "
            "JOIN lista ON lista.id = lista_foto.lista_id "
            "WHERE foto.galeria_id = ? AND lista.cliente_id = ? AND lista.activa = 1 "
            "ORDER BY foto.orden",
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


# las fotos que estan en TODAS las listas activas: las del corazon lleno.
# Con "cualquiera" en vez de "todas", tocar una favorita con Edicion prendida
# la sacaba de Favoritos en vez de sumarla a Edicion (Renato, 21/9/2026).
EN_TODAS = (
    "SELECT lista_foto.foto_id FROM lista_foto "
    "JOIN lista ON lista.id = lista_foto.lista_id "
    "WHERE lista.cliente_id = ? AND lista.activa = 1 "
    "GROUP BY lista_foto.foto_id HAVING COUNT(DISTINCT lista.id) = "
    "  (SELECT COUNT(*) FROM lista WHERE cliente_id = ? AND activa = 1)")


def cuenta_marcadas(cx, cid):
    """Fotos con el corazon lleno: las que estan en todas las listas activas."""
    return cx.execute(f"SELECT COUNT(*) c FROM ({EN_TODAS})",
                      (cid, cid)).fetchone()["c"]


def _listas_json(cx, cid):
    car = carrito(cx, cid)
    cx.commit()
    filas = cx.execute(
        "SELECT lista.id, lista.nombre, lista.activa, "
        "  (SELECT COUNT(*) FROM lista_foto WHERE lista_id = lista.id) n "
        "FROM lista WHERE cliente_id = ? AND nombre <> ? ORDER BY orden, id",
        (cid, CARRITO)).fetchall()
    return jsonify(listas=[dict(r) for r in filas], marcadas=cuenta_marcadas(cx, cid),
                   # se llena sola con lo que bajo: no es una lista, es una vista
                   bajadas=cx.execute(
                       "SELECT COUNT(DISTINCT foto_id) FROM descarga WHERE cliente_id = ?",
                       (cid,)).fetchone()[0],
                   carrito_id=car["id"], carrito=cuenta_lista(cx, car["id"]),
                   repetidas=cx.execute(f"SELECT COUNT(*) FROM ({REPETIDAS})",
                                        (cid, CARRITO)).fetchone()[0],
                   distintas=cx.execute(
                       "SELECT COUNT(DISTINCT lista_foto.foto_id) FROM lista_foto "
                       "JOIN lista ON lista.id = lista_foto.lista_id "
                       "WHERE lista.cliente_id = ? AND lista.nombre <> ?",
                       (cid, CARRITO)).fetchone()[0])


def codigo_lista(lid):
    """El ID corto que el cliente manda por WhatsApp en vez de su correo.

    Sale del id de la lista mezclado con la clave del sitio: no es secuencial
    ni adivinable —con el numero pelado se podrian pedir listas ajenas— y es
    siempre el mismo, asi que sirve para buscarlo despues.
    """
    return hashlib.sha1(
        ("lista|%s|%s" % (app.secret_key, lid)).encode()).hexdigest()[:6].upper()


def lista_por_codigo(cx, codigo):
    """La lista cuyo ID es ese, o None. Recorre y compara: son unas pocas
    filas por galeria y evita guardar un codigo mas en la base."""
    codigo = (codigo or "").strip().upper()
    if len(codigo) != 6:
        return None
    for r in cx.execute("SELECT id FROM lista"):
        if codigo_lista(r["id"]) == codigo:
            return r["id"]
    return None


def cuenta_lista(cx, lid):
    return cx.execute("SELECT COUNT(*) c FROM lista_foto WHERE lista_id = ?",
                      (lid,)).fetchone()["c"]


def tiene_tapa(gal):
    """Hay foto para la portada: la elegida, o la primera de la galeria."""
    return db().execute("SELECT 1 FROM foto WHERE galeria_id = ? LIMIT 1",
                        (gal["id"],)).fetchone() is not None


def _cliente_de(slug):
    """El cliente que entro a esta galeria, o corta. Todas las rutas de listas
    pasan por aca: sin esto los id de lista serian enumerables entre clientes."""
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal) or gal["id"] not in entradas():
        abort(404)
    cid = cliente_actual(gal["id"])
    if cid is None:
        abort(403)
    return cid


NTFY_TEMA = BASE / "ntfy_tema.txt"  # el mismo tema privado de seleccion_ig.py; sin el archivo no avisa
SITIO = "https://rbfotos.studio"


def avisar_renzo(titulo, mensaje, link=None):
    """Notificacion al telefono de Renzo por ntfy.sh. Sin tema configurado no hace nada."""
    if not NTFY_TEMA.exists():
        return False
    datos = {"topic": NTFY_TEMA.read_text(encoding="utf-8").strip(), "title": titulo,
             "message": mensaje, "tags": ["white_check_mark"]}
    if link:
        datos["click"] = link
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(datos).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except (urllib.error.URLError, OSError):
        return False


@app.post("/api/terminar/<slug>")
def terminar_seleccion(slug):
    """El cliente avisa que termino de elegir (galerias de seleccion, 1/10/2026). Queda en
    seleccion_lista, que es lo que mira la edicion nocturna, y le llega un aviso a Renzo."""
    cid = _cliente_de(slug)
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal["tipo"] != "seleccion":
        abort(404)
    cx.execute("INSERT OR REPLACE INTO seleccion_lista (galeria_id, cliente_id, cuando) VALUES (?, ?, ?)",
               (gal["id"], cid, ahora_iso()))
    cx.commit()
    cli = cx.execute("SELECT nombre, email FROM cliente WHERE id = ?", (cid,)).fetchone()
    quien = cli["nombre"] or cli["email"].split("@")[0]
    cuentas = cx.execute(
        "SELECT lista.nombre, COUNT(lista_foto.foto_id) n FROM lista "
        "LEFT JOIN lista_foto ON lista_foto.lista_id = lista.id "
        "WHERE lista.cliente_id = ? AND lista.nombre <> ? GROUP BY lista.id HAVING n > 0 "
        "ORDER BY lista.orden", (cid, CARRITO)).fetchall()
    detalle = " · ".join(f"{r['nombre']}: {r['n']}" for r in cuentas) or "sin fotos en listas"
    avisar_renzo(f"{quien} terminó su selección", f"{gal['nombre']} — {detalle}",
                 f"{SITIO}/panel/{slug}/cliente/{cid}")
    return jsonify(ok=True)


@app.get("/api/listas/<slug>")
def ver_listas(slug):
    cid = _cliente_de(slug)
    cx = db()
    favoritos(cx, cid)
    asegurar_listas(cx, cx.execute("SELECT * FROM galeria WHERE slug = ?",
                                   (slug,)).fetchone(), cid)
    cx.commit()
    return _listas_json(cx, cid)


@app.post("/api/listas/<slug>")
def crear_lista(slug):
    cid = _cliente_de(slug)
    nombre = ((request.get_json(silent=True) or {}).get("nombre") or "").strip()[:40]
    if not nombre:
        abort(400)
    cx = db()
    cx.execute("INSERT OR IGNORE INTO lista (cliente_id, nombre, orden, activa) "
               "SELECT ?, ?, COALESCE(MAX(orden), 0) + 1, 0 FROM lista "
               "WHERE cliente_id = ?", (cid, nombre, cid))
    cx.commit()
    return _listas_json(cx, cid)


@app.post("/api/listas/<slug>/orden")
def ordenar_listas(slug):
    cid = _cliente_de(slug)
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    cx = db()
    for i, lid in enumerate(ids):
        # el WHERE cliente_id evita que alguien reordene listas ajenas
        cx.execute("UPDATE lista SET orden = ? WHERE id = ? AND cliente_id = ?",
                   (i, int(lid), cid))
    cx.commit()
    return _listas_json(cx, cid)


@app.post("/api/listas/<slug>/<int:lid>/activa")
def activar_lista(slug, lid):
    cid = _cliente_de(slug)
    cx = db()
    fila = cx.execute("SELECT nombre FROM lista WHERE id = ? AND cliente_id = ?",
                      (lid, cid)).fetchone()
    if fila is None:
        abort(404)
    if fila["nombre"] == CARRITO:
        abort(400)          # el carrito no recibe me-gusta: es lo que compras
    activa = bool((request.get_json(silent=True) or {}).get("activa"))
    cx.execute("UPDATE lista SET activa = ? WHERE id = ?", (1 if activa else 0, lid))
    cx.commit()
    return _listas_json(cx, cid)


def _foto_de(slug, foto_id):
    """La foto, solo si es de ESTA galeria y el cliente entro a ella."""
    f = foto_autorizada(foto_id)
    if f["slug"] != slug:
        abort(404)
    return f


@app.get("/api/listas/<slug>/foto/<int:foto_id>")
def listas_de_foto(slug, foto_id):
    """En cuales listas del cliente esta esta foto (el carrito aparte)."""
    cid = _cliente_de(slug)
    f = _foto_de(slug, foto_id)
    return jsonify(listas=[dict(r) for r in db().execute(
        "SELECT lista.id, lista.nombre, EXISTS(SELECT 1 FROM lista_foto "
        "  WHERE lista_id = lista.id AND foto_id = ?) AS en "
        "FROM lista WHERE cliente_id = ? AND nombre <> ? ORDER BY orden, id",
        (f["id"], cid, CARRITO))])


@app.post("/api/listas/<slug>/<int:lid>/foto")
def poner_en_lista(slug, lid):
    """Pone o saca UNA foto de UNA lista, sin mirar cuales estan prendidas."""
    cid = _cliente_de(slug)
    datos = request.get_json(silent=True) or {}
    f = _foto_de(slug, int(datos.get("foto_id", 0)))
    cx = db()
    l = cx.execute("SELECT nombre FROM lista WHERE id = ? AND cliente_id = ?",
                   (lid, cid)).fetchone()
    if l is None:
        abort(404)                  # lista ajena o inventada
    if l["nombre"] == CARRITO:
        abort(400)                  # el carrito tiene su propio boton
    if datos.get("en"):
        cx.execute("INSERT OR IGNORE INTO lista_foto (lista_id, foto_id, cuando) "
                   "VALUES (?, ?, ?)", (lid, f["id"], ahora_iso()))
    else:
        cx.execute("DELETE FROM lista_foto WHERE lista_id = ? AND foto_id = ?",
                   (lid, f["id"]))
    cx.commit()
    return _listas_json(cx, cid)


@app.get("/api/marcadas/<slug>")
def ids_marcados(slug):
    """Los id que estan en las listas activas.

    Cambiar que listas estan activas cambia que corazones van llenos. Recargar
    la pagina perderia el scroll infinito ya cargado, asi que se manda la lista
    de id y el navegador repinta. Son los marcados, no las 5.407.
    """
    cid = _cliente_de(slug)
    return jsonify(ids=[r["foto_id"] for r in db().execute(EN_TODAS, (cid, cid))])


@app.post("/api/megusta")
def megusta():
    datos = request.get_json(silent=True) or {}
    f = foto_autorizada(int(datos.get("foto_id", 0)))
    cid = cliente_actual(f["galeria_id"])
    if cid is None:
        abort(403)
    cx = db()
    activas = [l["id"] for l in listas_activas(cx, cid)]
    marca = ",".join("?" * len(activas))
    # lleno = esta en TODAS las activas. Si falta en alguna, se agrega ahi
    # y no se saca de ninguna: asi se pasan favoritas a otra lista
    ya = cx.execute(
        f"SELECT COUNT(*) FROM lista_foto WHERE foto_id = ? AND lista_id IN ({marca})",
        [f["id"]] + activas).fetchone()[0] == len(activas)
    if ya:
        cx.execute(
            f"DELETE FROM lista_foto WHERE foto_id = ? AND lista_id IN ({marca})",
            [f["id"]] + activas)
    else:
        cx.executemany("INSERT OR IGNORE INTO lista_foto (lista_id, foto_id, cuando) "
                       "VALUES (?, ?, ?)",
                       [(l, f["id"], ahora_iso()) for l in activas])
    cx.commit()
    d = _listas_json(cx, cid).get_json()
    d.update(mia=not ya, en=[l["nombre"] for l in listas_activas(cx, cid)])
    return jsonify(d)


LARGO_COMENTARIO = 500


def _gal_comenta(slug):
    """La galeria, solo si tiene los comentarios prendidos y el cliente entro.
    Esconder la caja no es proteger nada: todo pasa por aca."""
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None or expirada(gal) or not gal["comentarios"]:
        abort(404)
    return gal


def comentarios_json(cx, foto_id, cid):
    filas = cx.execute(
        "SELECT comentario.id, comentario.texto, comentario.cuando, "
        "  comentario.cliente_id, cliente.nombre, cliente.email FROM comentario "
        "JOIN cliente ON cliente.id = comentario.cliente_id "
        "WHERE comentario.foto_id = ? ORDER BY comentario.id", (foto_id,)).fetchall()
    return jsonify(comentarios=[
        {"id": r["id"], "texto": r["texto"], "cuando": r["cuando"],
         "hora": hora_local(r["cuando"]),
         # sin nombre no se muestra el correo de nadie: es dato de otro cliente
         "nombre": r["nombre"] or "Alguien",
         "mio": r["cliente_id"] == cid} for r in filas])


@app.get("/api/comentarios/<int:foto_id>")
def ver_comentarios(foto_id):
    f = foto_autorizada(foto_id)
    _gal_comenta(f["slug"])
    return comentarios_json(db(), foto_id, cliente_actual(f["galeria_id"]))


@app.post("/api/comentario")
def comentar():
    datos = request.get_json(silent=True) or {}
    f = foto_autorizada(int(datos.get("foto_id", 0)))
    _gal_comenta(f["slug"])
    cid = cliente_actual(f["galeria_id"])
    if cid is None:
        abort(403)
    cx = db()
    nombre = cx.execute("SELECT nombre FROM cliente WHERE id = ?",
                        (cid,)).fetchone()["nombre"]
    if not (nombre or "").strip():
        # 409 y no 400: al navegador le sirve distinguir "arregla el texto" de
        # "falta tu nombre", que se pide sin perder lo que ya escribio
        return jsonify(falta_nombre=True), 409
    texto = (datos.get("texto") or "").strip()
    if not texto or len(texto) > LARGO_COMENTARIO:
        return jsonify(error="El comentario va entre 1 y %d letras" % LARGO_COMENTARIO), 400
    cx.execute("INSERT INTO comentario (foto_id, cliente_id, texto, cuando) "
               "VALUES (?, ?, ?, ?)", (f["id"], cid, texto, ahora_iso()))
    cx.commit()
    return comentarios_json(cx, f["id"], cid), 201


@app.post("/api/comentario/<int:com_id>/borrar")
def borrar_comentario(com_id):
    """Cada uno borra los suyos. El de otro no existe para el: 404, no 403,
    asi el id ajeno tampoco sirve para saber que hay algo ahi."""
    cx = db()
    c = cx.execute(
        "SELECT comentario.*, galeria.slug, galeria.id AS gid FROM comentario "
        "JOIN foto ON foto.id = comentario.foto_id "
        "JOIN galeria ON galeria.id = foto.galeria_id "
        "WHERE comentario.id = ?", (com_id,)).fetchone()
    if c is None or c["gid"] not in entradas():
        abort(404)
    if c["cliente_id"] != cliente_actual(c["gid"]):
        abort(404)
    cx.execute("DELETE FROM comentario WHERE id = ?", (com_id,))
    cx.commit()
    return comentarios_json(cx, c["foto_id"], c["cliente_id"])


@app.post("/api/nombre/<slug>")
def poner_nombre(slug):
    """El nombre que firma los comentarios. Se pide recien cuando hace falta."""
    cid = _cliente_de(slug)
    nombre = ((request.get_json(silent=True) or {}).get("nombre") or "").strip()[:60]
    if not nombre:
        return jsonify(error="Escribi tu nombre"), 400
    cx = db()
    cx.execute("UPDATE cliente SET nombre = ? WHERE id = ?", (nombre, cid))
    cx.commit()
    return jsonify(nombre=nombre)


@app.post("/api/visto/<int:foto_id>")
def api_visto(foto_id):
    """El visor avisa que ESTA foto estuvo en pantalla. Llega por sendBeacon,
    sin cuerpo y sin esperar respuesta: 204 y nada mas."""
    f = foto_autorizada(foto_id)
    anotar_vista(f["galeria_id"], foto_id, True)
    return "", 204


@app.post("/api/carrito")
def api_carrito():
    """Pone y saca una foto del carrito. Existe aparte de /api/megusta porque
    el carrito no depende de que listas esten activas: es una sola."""
    datos = request.get_json(silent=True) or {}
    f = foto_autorizada(int(datos.get("foto_id", 0)))
    cid = cliente_actual(f["galeria_id"])
    if cid is None:
        abort(403)
    cx = db()
    car = carrito(cx, cid)
    ya = cx.execute("SELECT 1 FROM lista_foto WHERE lista_id = ? AND foto_id = ?",
                    (car["id"], f["id"])).fetchone()
    if ya:
        cx.execute("DELETE FROM lista_foto WHERE lista_id = ? AND foto_id = ?",
                   (car["id"], f["id"]))
    else:
        cx.execute("INSERT OR IGNORE INTO lista_foto (lista_id, foto_id, cuando) "
                   "VALUES (?, ?, ?)", (car["id"], f["id"], ahora_iso()))
    cx.commit()
    return jsonify(en=not ya, carrito=cuenta_lista(cx, car["id"]))


OCULTAS = {"_galeria", "_thumbs_curador", "_vistas_curador", "_autocull_curador",
           "$RECYCLE.BIN", "System Volume Information"}


PREFIJO_DERIVADAS = "derivadas"


def fotos_de(aqui, hondo=False):
    """Las fotos de una carpeta, ordenadas.

    hondo=True baja por las subcarpetas: una tarjeta de camara trae 100MSDCF,
    101MSDCF... y todas son el mismo evento. Se salta lo oculto y las carpetas
    de trabajo del curador, igual que el navegador.
    """
    it = aqui.rglob("*") if hondo else aqui.iterdir()
    fotos = []
    for f in it:
        if not f.is_file() or not es_visible(f):
            continue
        rel = f.relative_to(aqui).parts[:-1]
        if any(p.startswith(".") or p in OCULTAS for p in rel):
            continue
        fotos.append(str(f))
    return sorted(fotos)


def _con_fotos(carpeta):
    """True si hay al menos una foto adentro, bajando por subcarpetas (la
    tarjeta trae 100MSDCF, 101MSDCF...). Corta en la primera."""
    try:
        return any(f.is_file() and es_visible(f) for f in carpeta.rglob("*"))
    except OSError:
        return False


def fuentes_de(carpeta):
    """Las subcarpetas del evento que pueden ser fuente: [(carpeta, n fotos)].

    Vacia si la carpeta no sigue la convencion RAW / Jpeg / Export.
    """
    try:
        hijas = {h.name.lower(): h for h in carpeta.iterdir() if h.is_dir()}
    except OSError:
        return []
    return [(hijas[n], len(fotos_de(hijas[n], True)))
            for n in ("export", "jpeg") if n in hijas]


def _resolver_export(carpeta):
    """La carpeta real desde la que publicar, siguiendo la convencion por
    evento RAW / Jpeg / Export.

    Se prefiere Export y, si esta vacia, Jpeg: una fecha recien descargada
    trae las fotos de camara y todavia no tiene edicion. Busca entre las HIJAS
    (elegiste el evento entero) o entre las HERMANAS (entraste a una de las
    tres). Una carpeta vacia nunca gana: sin esto, una export vacia dejaba al
    evento sin forma de publicarse. Sin la convencion, la carpeta tal cual.
    """
    if carpeta.name.lower() == "export" and _con_fotos(carpeta):
        return carpeta
    for base in (carpeta, carpeta.parent):
        try:
            hijas = {h.name.lower(): h for h in base.iterdir() if h.is_dir()}
        except OSError:
            continue
        if "export" not in hijas and "jpeg" not in hijas:
            continue
        for nombre in ("export", "jpeg"):
            if nombre in hijas and _con_fotos(hijas[nombre]):
                return hijas[nombre]
    return carpeta


def _carpeta(rel):
    """La primera parte de la ruta elige la raiz: originales o derivadas.

    El guard sigue siendo dentro_de_raiz() sobre la ruta ya resuelta, asi que
    un `..` no saca a nadie de las dos raices permitidas.
    """
    rel = rel.strip("/")
    if rel == PREFIJO_DERIVADAS or rel.startswith(PREFIJO_DERIVADAS + "/"):
        base = RAIZ_DERIVADAS
        resto = rel[len(PREFIJO_DERIVADAS):].strip("/")
    else:
        base, resto = RAIZ_FOTOS, rel
    aqui = (base / resto).resolve() if resto else base.resolve()
    if not dentro_de_raiz(aqui) or not aqui.is_dir():
        abort(404)
    return aqui


@app.get("/panel/nueva")
def nueva():
    rel = (request.args.get("dir") or "").strip("/")
    aqui = _carpeta(rel)
    hondo = request.args.get("sub") == "1"
    subdirs = []
    with os.scandir(aqui) as it:
        for e in it:
            if e.name.startswith(".") or e.name in OCULTAS:
                continue
            if e.is_dir():
                subdirs.append(e.name)
    if not rel and RAIZ_DERIVADAS.is_dir() and any(RAIZ_DERIVADAS.iterdir()):
        subdirs.append(PREFIJO_DERIVADAS)

    # el conteo hondo solo cuando se pide: recorrer una raiz de 2,5 TB para
    # dibujar un numero que nadie miro seria carisimo. fotos_de() sobre la
    # carpeta YA resuelta: si aqui sigue la convencion RAW/Jpeg/Export, lo
    # que se cuenta (y lo que se publica) es siempre lo de Export.
    publicar_de = _resolver_export(aqui)
    # una fuente del evento se lee entera: sus fotos estan en 100MSDCF, 101MSDCF
    fotos = fotos_de(publicar_de, hondo or publicar_de != aqui)
    migas, acum = [], ""
    for parte in rel.split("/") if rel else []:
        acum = f"{acum}/{parte}".strip("/")
        migas.append((parte, acum))
    # ?a=<slug> reusa este mismo navegador para AGREGAR a una galeria que ya
    # existe, en vez de crear una nueva
    dest = None
    if request.args.get("a"):
        dest = db().execute("SELECT * FROM galeria WHERE slug = ?",
                            (request.args["a"],)).fetchone()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_NUEVA, rel=rel, migas=migas, subdirs=sorted(subdirs), marcas=MARCAS,
        cuantas=len(fotos), dest=dest, hondo=hondo, hay_sub=bool(subdirs),
        resuelto=(publicar_de.name if publicar_de != aqui else None),
        fuentes=[(c.name, n) for c, n in fuentes_de(aqui)],
        por_defecto=publicar_de.name, tipos=TIPOS, tipo_nombre=TIPO_NOMBRE))


@app.post("/panel/nueva")
def crear():
    rel = (request.form.get("dir") or "").strip("/")
    nombre = (request.form.get("nombre") or "").strip()
    hondo = request.form.get("sub") == "1"
    carpeta = _carpeta(rel)
    # la fuente elegida, solo si es de verdad una de las subcarpetas del
    # evento: un nombre inventado en el formulario no puede apuntar a otro lado
    elegida = (request.form.get("fuente") or "").lower()
    fuente = next((c for c, _n in fuentes_de(carpeta) if c.name.lower() == elegida),
                  None) or _resolver_export(carpeta)
    rutas = fotos_de(fuente, hondo or fuente != carpeta)
    if not nombre or not rutas:
        abort(400)
    slug, _puestas, _faltan = crear_galeria(db(), nombre, rutas)
    texto = request.form.get("marca_texto")
    tipo = request.form.get("tipo")
    db().execute("UPDATE galeria SET marca = ?, descargas = ?, marca_texto = ?, "
                 "tipo = ? WHERE slug = ?",
                 (0 if request.form.get("sin_marca") else 1,
                  1 if request.form.get("descargas") else 0,
                  texto if texto in MARCAS else MARCAS[0],
                  tipo if tipo in TIPOS else None, slug))
    db().commit()
    return redirect(url_for("panel", slug=slug))


def _grupos_panel(cx):
    gals = cx.execute(
        "SELECT id, nombre, tipo, "
        "  (SELECT COUNT(*) FROM foto WHERE galeria_id = galeria.id) AS fotos, "
        "  (SELECT id FROM foto WHERE galeria_id = galeria.id "
        "    ORDER BY orden, id LIMIT 1) AS tapa "
        "FROM galeria ORDER BY creada DESC").fetchall()
    grupos = [(titulo, [g for g in gals
                        if (g["tipo"] if g["tipo"] in TIPOS else None) == t])
              for t, titulo in TIPO_GRUPO]
    return [x for x in grupos if x[1]]


@app.get("/panel/carpetas")
def panel_carpetas():
    cx = db()
    carpetas = [dict(c, ids={r[0] for r in cx.execute(
                    "SELECT galeria_id FROM carpeta_galeria WHERE carpeta_id = ?",
                    (c["id"],))})
                for c in cx.execute("SELECT * FROM carpeta ORDER BY creada DESC")]
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_CARPETAS, carpetas=carpetas, grupos=_grupos_panel(cx)))


@app.post("/panel/carpetas")
def guardar_carpeta():
    """Crea (sin slug) o reescribe (con slug) una carpeta y sus galerias."""
    cx = db()
    nombre = (request.form.get("nombre") or "").strip()[:80]
    if not nombre:
        abort(400)
    slug = request.form.get("slug")
    if slug:
        c = cx.execute("SELECT id FROM carpeta WHERE slug = ?", (slug,)).fetchone()
        if c is None:
            abort(404)
        cid = c["id"]
        cx.execute("UPDATE carpeta SET nombre = ? WHERE id = ?", (nombre, cid))
        cx.execute("DELETE FROM carpeta_galeria WHERE carpeta_id = ?", (cid,))
    else:
        slug = secrets.token_urlsafe(12)
        cid = cx.execute("INSERT INTO carpeta (slug, nombre, creada) VALUES (?, ?, ?)",
                         (slug, nombre, datetime.now(timezone.utc).isoformat())).lastrowid
    # solo ids que existen: un id inventado en el formulario no entra
    ids = [int(x) for x in request.form.getlist("g") if x.isdigit()]
    cx.executemany(
        "INSERT OR IGNORE INTO carpeta_galeria (carpeta_id, galeria_id) "
        "SELECT ?, id FROM galeria WHERE id = ?", [(cid, i) for i in ids])
    cx.commit()
    return redirect(url_for("panel_carpetas") + "#c-" + slug)


@app.post("/panel/carpetas/<slug>/borrar")
def borrar_carpeta(slug):
    """Borra la carpeta y nada mas: las galerias siguen donde estaban."""
    cx = db()
    cx.execute("DELETE FROM carpeta WHERE slug = ?", (slug,))
    cx.commit()
    return redirect(url_for("panel_carpetas"))


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


LATIDO_MAX = 120      # segundos sin latido = la notebook no esta


def notebook_viva():
    """True si el agente de la notebook dio señales hace menos de LATIDO_MAX."""
    try:
        return time.time() - (CACHE / "notebook-vivo").stat().st_mtime < LATIDO_MAX
    except OSError:
        return False


def en_notebook(slug):
    """True si el calentado de esta galeria esta pedido o corriendo en la
    notebook. Un pedido con la notebook muda no cuenta: queda huerfano."""
    return notebook_viva() and any(
        (CACHE / ("%s-%s" % (e, slug))).exists() for e in ("pedido", "trabajando"))


@app.post("/panel/<slug>/raw")
def buscar_raw_ahora(slug):
    """Vuelve a buscar el RAW de las fotos que todavia no lo tienen."""
    cx = db()
    gal = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    n, de = _buscar_raws(cx, "raw IS NULL AND galeria_id = ?", (gal["id"],))
    return redirect(url_for("panel", slug=slug, raw=n, raw_de=de))


@app.post("/panel/<slug>/calentar")
def calentar_ahora(slug):
    if db().execute("SELECT 1 FROM galeria WHERE slug = ?", (slug,)).fetchone() is None:
        abort(404)
    if en_notebook(slug):
        return redirect(url_for("panel", slug=slug))
    CACHE.mkdir(parents=True, exist_ok=True)
    for e in ("pedido", "trabajando"):          # restos de una notebook que se fue
        (CACHE / ("%s-%s" % (e, slug))).unlink(missing_ok=True)
    if notebook_viva():
        (CACHE / ("pedido-" + slug)).write_text(datetime.now(timezone.utc).isoformat())
        return redirect(url_for("panel", slug=slug))
    if not _calentando(slug):
        CACHE.mkdir(parents=True, exist_ok=True)
        # proceso aparte: calentar tarda minutos y el server no puede
        # quedarse sin atender al resto del sitio mientras tanto
        p = subprocess.Popen([sys.executable, __file__, "calentar", slug],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        _lock_calentando(slug).write_text(str(p.pid))
    return redirect(url_for("panel", slug=slug))


@app.get("/panel/p/<int:foto_id>")
def panel_preview(foto_id):
    """Caddy protege /panel* con basic_auth. Sin esta ruta el panel no puede
    mostrar miniaturas: /p/<id> exige sesion de cliente."""
    f = db().execute("SELECT * FROM foto WHERE id = ?", (foto_id,)).fetchone()
    if f is None or not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    try:
        # siempre limpia: el panel esta detras del basic_auth de Caddy, la marca
        # no protege nada ahi y tapa justo lo que hay que mirar para decidir
        return send_file(sin_marca(f["ruta"]), mimetype="image/jpeg", max_age=86400)
    except Exception:
        abort(404)


# ---------------------------------------------------------------------------
# Revision de la seleccion para Instagram (1/10/2026)
#
# seleccion_ig.py, en la notebook, elige 10 fotos por trabajo y deja la lista en
# /mnt/fotos/_para_publicar/<trabajo>/seleccion.json (rutas de la notebook). Aca Renzo
# confirma foto por foto si pasa a edicion. Lo que pasa se copia a aprobadas/ al lado,
# para editarlo desde la notebook; los veredictos quedan en revision.json, que es el gusto
# de Renzo para Instagram (distinto del de Renato, el cliente de la liga).
# ---------------------------------------------------------------------------

REVISION = RAIZ_FOTOS / "_para_publicar"
_PREFIJOS_NOTEBOOK = ("//192.168.1.62/fotos/", "//100.78.164.81/fotos/", "k:/")


def _ruta_pi(original):
    r"""\\192.168.1.62\Fotos\X o K:\X (como los guarda la notebook) -> /mnt/fotos/X."""
    r = (original or "").replace("\\", "/")
    for pref in _PREFIJOS_NOTEBOOK:
        if r.lower().startswith(pref):
            return RAIZ_FOTOS / r[len(pref):]
    return None


SECCIONES = {"": "seleccion.json", "renato": "renato.json"}


def _trabajo(trabajo):
    """La carpeta de un trabajo con algo que revisar, o 404. dentro_de_raiz ataja '..'."""
    d = REVISION / trabajo
    if not dentro_de_raiz(d) or not any((d / f).exists() for f in SECCIONES.values()):
        abort(404)
    return d


def _seccion(d, s=""):
    """seleccion.json (las que eligio Claude del carrete) o renato.json (el top de lo que eligio
    Renato, 7/10/2026). {} si esa seccion no esta."""
    if s not in SECCIONES:
        abort(404)
    try:
        return json.loads((d / SECCIONES[s]).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


_EXT_RAW = (".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2")


def _raw_de(jpeg):
    """El RAW hermano de un JPEG del trabajo, o None. Renzo edita sobre el RAW (Denoise lo exige):
    vive en <trabajo>/RAW/, en la misma subcarpeta de tarjeta (100MSDCF) o en la raiz de RAW/, con el
    mismo nombre, incluido el ' (2)' de la segunda tarjeta."""
    jpeg = Path(jpeg)
    for trabajo in jpeg.parents:
        crudo = trabajo / "RAW"
        if crudo.is_dir():
            break
    else:
        return None
    for carpeta in (crudo / jpeg.parent.name, crudo):
        for ext in _EXT_RAW:
            for e in (ext, ext.upper()):
                p = carpeta / (jpeg.stem + e)
                if p.exists():
                    return p
    return None


def _veredictos(d):
    try:
        return json.loads((d / "revision.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.get("/panel/revision")
def revision_indice():
    """Las fechas agrupadas por categoria (la liga), las secciones con lo mas reciente primero
    (pedido de Renzo, 14/10/2026: con varias ligas la lista plana ya no se leia)."""
    trabajos = []
    propias = _categorias()
    if REVISION.is_dir():
        for d in sorted(REVISION.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not any((d / f).exists() for f in SECCIONES.values()):
                continue
            v = _veredictos(d)
            n = sum(len(_seccion(d, s).get("elegidas", [])) for s in SECCIONES)
            pasa = sum(1 for x in v.values() if x.get("pasa"))
            buenas = sum(1 for x in v.values() if x.get("buena"))
            trabajos.append({"nombre": d.name, "url": urllib.parse.quote(d.name), "n": n, "pasa": pasa,
                             "categoria": categoria_de(d.name, propias),
                             "buenas": buenas, "no": len(v) - pasa - buenas,
                             "faltan": n - len(v),
                             "fecha": datetime.fromtimestamp(d.stat().st_mtime).strftime("%d/%m %H:%M")})
    secciones = {}
    for x in trabajos:
        secciones.setdefault(x["categoria"], []).append(x)
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_REVISION_INDICE, trabajos=trabajos, secciones=list(secciones.items())))


CATEGORIAS = REVISION / "categorias.json"   # {trabajo: categoria} que Renzo eligio a mano en el panel


def _categorias():
    try:
        return json.loads(CATEGORIAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def categoria_de(nombre, propias):
    """La que Renzo eligio, o la liga: lo que va antes del primer " - ", sin una fecha al final
    ("LigaUandes Max 24-09-2026" -> "LigaUandes Max")."""
    if propias.get(nombre):
        return propias[nombre]
    base = nombre.split(" - ", 1)[0]
    return re.sub(r"\s+\d{1,2}-\d{1,2}-\d{4}$", "", base).strip() or "Otros"


@app.post("/panel/revision-categoria")
def revision_categoria():
    """Mueve una fecha a otra categoria; vacio la devuelve a su liga."""
    trabajo = (request.form.get("t") or "").strip()
    cat = (request.form.get("c") or "").strip()[:60]
    _trabajo(trabajo)                       # 404 si no existe o se sale de _para_publicar
    propias = _categorias()
    if cat:
        propias[trabajo] = cat
    else:
        propias.pop(trabajo, None)
    tmp = CATEGORIAS.with_suffix(".tmp")
    tmp.write_text(json.dumps(propias, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CATEGORIAS)
    return redirect("/panel/revision")


@app.get("/panel/revision/<path:trabajo>")
def revision_trabajo(trabajo):
    d = _trabajo(trabajo)
    v = _veredictos(d)
    sel, ren = _seccion(d), _seccion(d, "renato")

    def fotos(datos):
        return [dict(e, n=i, veredicto=_clase(v.get(e["archivo"])))
                for i, e in enumerate(datos.get("elegidas", []), 1)]
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_REVISION, nombre=d.name, url=urllib.parse.quote(d.name), fotos=fotos(sel),
        apunte=sel.get("apunte", ""), fotos_r=fotos(ren), de_r=ren.get("de", 0),
        pasan=sum(1 for x in v.values() if x.get("pasa")),
        faltan=len(sel.get("elegidas", [])) + len(ren.get("elegidas", [])) - len(v)))


@app.get("/panel/revision-foto/<path:trabajo>/<int:n>")
def revision_foto(trabajo, n):
    """La foto n del trabajo, sin marca. ?full=1 manda el original tal cual."""
    elegidas = _seccion(_trabajo(trabajo), request.args.get("s", "")).get("elegidas", [])
    if not 1 <= n <= len(elegidas):
        abort(404)
    ruta = _ruta_pi(elegidas[n - 1].get("original"))
    if ruta is None or not dentro_de_raiz(ruta) or not ruta.exists():
        abort(404)
    if request.args.get("full"):
        return send_file(ruta, mimetype="image/jpeg")
    return send_file(sin_marca(str(ruta), ancho=1600), mimetype="image/jpeg", max_age=86400)


def _clase(x):
    """El veredicto guardado como clase CSS: si / buena / no, o "" sin votar."""
    if not x:
        return ""
    return "si" if x.get("pasa") else "buena" if x.get("buena") else "no"


_VOTO = threading.Lock()   # dos votos seguidos (toques rapidos) leian el mismo revision.json y uno se perdia


@app.post("/panel/revision/<path:trabajo>/<int:n>")
def revision_votar(trabajo, n):
    with _VOTO:
        return _votar(trabajo, n)


def _votar(trabajo, n):
    """Pasa / buena pero no la subiria / no pasa. Lo que pasa se copia a aprobadas/; si se
    arrepiente, se saca. "buena" no se copia: es senal de gusto (buena foto, no para Instagram)."""
    d = _trabajo(trabajo)
    elegidas = _seccion(d, request.args.get("s", "")).get("elegidas", [])
    if not 1 <= n <= len(elegidas):
        abort(404)
    e = elegidas[n - 1]
    pasa = request.form.get("veredicto") == "pasa"
    v = _veredictos(d)
    anterior = (v.get(e["archivo"]) or {}).get("copia")
    entrada = {"pasa": pasa, "cuando": datetime.now().isoformat(timespec="seconds")}
    if request.form.get("veredicto") == "buena":
        entrada["buena"] = True
    if pasa:
        # A aprobadas/ va el RAW (Denoise de Lightroom solo trabaja sobre RAW); si no hay, el JPEG.
        jpeg = _ruta_pi(e.get("original"))
        fuente = _raw_de(jpeg) if jpeg is not None else None
        fuente = fuente or jpeg
        if fuente is not None and dentro_de_raiz(fuente) and fuente.exists():
            nombre = Path(e["archivo"]).stem + fuente.suffix  # 01_DSC05000.ARW: conserva el orden
            copia = d / "aprobadas" / nombre
            if not copia.exists():
                copia.parent.mkdir(exist_ok=True)
                shutil.copy2(fuente, copia)
            entrada["copia"] = nombre
    elif anterior and (d / "aprobadas" / anterior).exists():
        (d / "aprobadas" / anterior).unlink()  # es la copia que hizo este mismo panel, el original no se toca
    v[e["archivo"]] = entrada
    tmp = d / "revision.json.tmp"
    tmp.write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "revision.json")
    return jsonify(ok=True, pasa=pasa, clase=_clase(entrada),
                   pasan=sum(1 for x in v.values() if x.get("pasa")),
                   faltan=sum(len(_seccion(d, s).get("elegidas", [])) for s in SECCIONES) - len(v))




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
    gals = db().execute("SELECT slug, nombre FROM galeria "
                        "ORDER BY creada DESC").fetchall()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_EVENTO, ev=ev, fotos=fotos, p=pg, paginas=paginas, gals=gals))


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
    # la fecha del evento viene del catalogo; sin esto /eventos ordenaria
    # todo por el dia en que lo publicaste, que es el mismo para todos
    cx.execute("UPDATE galeria SET fecha = ? WHERE slug = ?",
               ((ev["inicio"] or "")[:10] or None, slug))
    cx.commit()
    return redirect(url_for("panel", slug=slug))


@app.get("/panel/logos")
def logos_form():
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_LOGOS, logos=logos_disponibles(), max_logos=MAX_LOGOS,
        error=request.args.get("error")))


@app.post("/panel/logos")
def subir_logo():
    """Un PNG con fondo transparente es lo que mejor queda; se acepta
    cualquier imagen y se valida que abra de verdad, como en /panel/subir."""
    f = request.files.get("logo")
    if f is None or not f.filename:
        return redirect(url_for("logos_form", error="elegi un archivo"))
    nombre = secure_filename(f.filename)
    if not nombre or not es_visible(Path(nombre)):
        return redirect(url_for("logos_form", error="ese formato no sirve"))
    LOGOS.mkdir(parents=True, exist_ok=True)
    destino = LOGOS / nombre
    n = 2
    while destino.exists():          # no pisar uno que ya usa otra galeria
        destino = LOGOS / f"{Path(nombre).stem}-{n}{Path(nombre).suffix}"
        n += 1
    f.save(destino)
    if not abrible(destino):
        destino.unlink(missing_ok=True)
        return redirect(url_for("logos_form", error="no es una imagen"))
    return redirect(url_for("logos_form"))


@app.post("/panel/logos/borrar")
def borrar_logo():
    """Borrarlo lo saca solo de las galerias que lo usaban: logos_de() ignora
    los que ya no estan, y como el nombre entra en la clave del cache, las
    miniaturas se regeneran sin el."""
    nombre = request.form.get("nombre") or ""
    f = LOGOS / nombre
    if ("/" not in nombre and "\\" not in nombre and f.is_file()
            and f.resolve().parent == LOGOS.resolve()):
        f.unlink()
    return redirect(url_for("logos_form"))


@app.get("/panel/logo/<nombre>")
def ver_logo(nombre):
    f = LOGOS / nombre
    if ("/" in nombre or "\\" in nombre or not f.is_file()
            or f.resolve().parent != LOGOS.resolve()):
        abort(404)
    return send_file(f, max_age=3600)


@app.get("/panel/textos")
def textos_form():
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_TEXTOS, CAMPOS=CAMPOS, T=textos()))


@app.post("/panel/textos")
def guardar_textos():
    """Guarda solo lo que difiere del default: asi un texto que nunca tocaste
    sigue mejorando cuando cambia el codigo, en vez de quedar congelado."""
    cx = db()
    for clave, _et, _fi, defecto in TODOS:
        if clave not in request.form:
            continue                      # campo que el formulario no mando
        v = (request.form[clave] or "").replace("\r\n", "\n").strip()
        if v == defecto.strip():
            cx.execute("DELETE FROM texto WHERE clave = ?", (clave,))
        else:
            cx.execute("INSERT INTO texto (clave, valor) VALUES (?, ?) "
                       "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                       (clave, v))
    cx.commit()
    return redirect(url_for("textos_form"))


@app.post("/panel/textos/restaurar")
def restaurar_textos():
    db().execute("DELETE FROM texto")
    db().commit()
    return redirect(url_for("textos_form"))


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

    nombre = (request.form.get("nombre") or "").strip()[:120]
    if nombre:
        cx.execute("UPDATE galeria SET nombre = ? WHERE id = ?", (nombre, gal["id"]))

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

    texto = request.form.get("marca_texto")
    # "" es una eleccion valida: marca de solo logo
    nuevo_texto = texto if (texto in MARCAS or texto == "") else gal["marca_texto"]
    cx.execute("UPDATE galeria SET marca = ?, descargas = ?, marca_texto = ?, "
               "venta = ?, raw_descarga = ?, comentarios = ? WHERE id = ?",
               (0 if request.form.get("sin_marca") else 1,
                1 if request.form.get("descargas") else 0, nuevo_texto,
                1 if request.form.get("venta") else 0,
                1 if request.form.get("raw_descarga") else 0,
                1 if request.form.get("comentarios") else 0, gal["id"]))

    # que recibe el cliente: vacio = se usa el texto general
    for col in ENTREGA_COLS:
        cx.execute("UPDATE galeria SET %s = ? WHERE id = ?" % col,
                   (entrega_de_form(request.form, col), gal["id"]))
    cx.execute("UPDATE galeria SET ent_detalles = ? WHERE id = ?",
               ((request.form.get("ent_detalles") or "").strip() or None, gal["id"]))
    ed = (request.form.get("ent_edicion") or "").strip()
    ed = ed if ed in EDICIONES else None
    cx.execute("UPDATE galeria SET ent_edicion = ? WHERE id = ?", (ed, gal["id"]))
    # las casillas que trae el formulario son las del preset que se estaba
    # viendo: si en este mismo guardado cambio de preset, no valen para el nuevo
    if ed and ed == (request.form.get("ent_edicion_previa") or "").strip():
        deja = set(request.form.getlist("ent_deja"))
        quita = [l for l in lineas(textos()["edicion_%s" % ed]) if l not in deja]
        cx.execute("UPDATE galeria SET ent_quita = ? WHERE id = ?",
                   ("\n".join(quita) or None, gal["id"]))
    else:
        cx.execute("UPDATE galeria SET ent_quita = NULL WHERE id = ?", (gal["id"],))
    tp = (request.form.get("tipo") or "").strip()
    cx.execute("UPDATE galeria SET tipo = ? WHERE id = ?",
               (tp if tp in TIPOS else None, gal["id"]))

    # listas que se le crean al cliente: los ticks + las escritas a mano
    nombres = [n for n in request.form.getlist("lista_auto") if n in LISTAS_SUGERIDAS]
    for n in (request.form.get("lista_auto_otras") or "").split(","):
        n = n.strip()[:40]
        if n and _norm(n) not in {_norm(x) for x in nombres + [FAVORITOS, CARRITO]}:
            nombres.append(n)
    cx.execute("UPDATE galeria SET listas_auto = ? WHERE id = ?",
               ("\n".join(nombres) or None, gal["id"]))
    # los que ya entraron las reciben ahora, no recien la proxima vez
    g2 = cx.execute("SELECT * FROM galeria WHERE id = ?", (gal["id"],)).fetchone()
    for r in cx.execute("SELECT id FROM cliente WHERE galeria_id = ?",
                        (gal["id"],)).fetchall():
        asegurar_listas(cx, g2, r["id"])

    dia = (request.form.get("fecha") or "").strip()
    try:
        dia = date.fromisoformat(dia).isoformat() if dia else None
    except ValueError:
        dia = gal["fecha"]   # una fecha rota no borra la que ya estaba
    cx.execute("UPDATE galeria SET fecha = ? WHERE id = ?", (dia, gal["id"]))

    # solo nombres que existen de verdad en la carpeta: eso cierra la puerta
    # a que el formulario mande una ruta cualquiera
    hay = set(logos_disponibles())
    elegidos = [n for n in request.form.getlist("logos") if n in hay][:MAX_LOGOS]
    antes_logos, antes_texto = logos_de(gal), gal["marca_texto"]
    antes_firma = firma_de(gal)

    # la firma de la entrega: un solo logo, o ninguno
    fir = request.form.get("firma") or ""
    fir = fir if fir in hay else ""
    fpos = request.form.get("firma_pos")
    fpos = fpos if fpos in POSICIONES else FIRMA_POS
    try:
        ftam = min(40, max(5, int(request.form.get("firma_tam") or FIRMA_TAM)))
    except (TypeError, ValueError):
        ftam = FIRMA_TAM
    cx.execute("UPDATE galeria SET firma = ?, firma_pos = ?, firma_tam = ? "
               "WHERE id = ?", (fir or None, fpos, ftam, gal["id"]))
    cx.execute("UPDATE galeria SET logos = ? WHERE id = ?",
               ("\n".join(elegidos) or None, gal["id"]))

    # si la marca cambio, las previews con la marca vieja son basura: se
    # borran ahora y no cuando el disco se llene
    # la firma tambien va en la preview, asi que cambiarla invalida las dos
    nueva_firma = (fir, fpos, ftam) if fir else None
    limpiadas = 0
    if (tuple(elegidos) != antes_logos or nuevo_texto != antes_texto
            or nueva_firma != antes_firma):
        limpiadas += limpiar_cache(gal["id"], antes_texto, antes_logos, antes_firma)
    if antes_firma and nueva_firma != antes_firma:
        limpiadas += limpiar_firmas(gal["id"], antes_firma)

    # vacio = NULL = los precios de siempre. El texto se guarda tal cual y
    # lo interpreta tramos(), que ya ignora las lineas que no entiende.
    precios = (request.form.get("precios") or "").replace("\r\n", "\n").strip()
    cx.execute("UPDATE galeria SET precios = ? WHERE id = ?",
               (precios or None, gal["id"]))

    # mismo trato para las indicaciones de entrega: vacio = las de siempre
    entrega = (request.form.get("entrega") or "").replace("\r\n", "\n").strip()
    cx.execute("UPDATE galeria SET entrega = ? WHERE id = ?",
               (entrega or None, gal["id"]))

    tapa = (request.form.get("portada") or "").strip()
    fid = None
    if tapa.isdigit():
        # solo una foto de ESTA galeria puede ser su tapa
        fila = cx.execute("SELECT id FROM foto WHERE id = ? AND galeria_id = ?",
                          (int(tapa), gal["id"])).fetchone()
        fid = fila["id"] if fila else None
    cx.execute("UPDATE galeria SET portada_foto_id = ? WHERE id = ?", (fid, gal["id"]))

    cx.commit()
    return redirect(url_for("panel", slug=slug, limpiadas=limpiadas or None))


PANEL_BARRA = """<nav class="pbarra">
{# la seccion sale de la URL: asi ninguna ruta tiene que pasarla a mano.
   /panel/<slug> y sus subpaginas cuentan como "galerias". #}
{% set sec = request.path.split('/')[2] if request.path.count('/') > 1 else '' %}
<a class="logo" href="/panel">rb<span>.</span>fotos</a>
<div class="links">
<a href="/panel" class="{{ 'on' if sec not in ('subir','eventos','textos','logos','carpetas','revision','revision-foto') }}"
  >Galerias</a>
<a href="/panel/revision" class="{{ 'on' if sec in ('revision','revision-foto') }}">Revision</a>
<a href="/panel/carpetas" class="{{ 'on' if sec == 'carpetas' }}">Carpetas</a>
<a href="/panel/subir" class="{{ 'on' if sec == 'subir' }}">Subir</a>
<a href="/panel/eventos" class="{{ 'on' if sec == 'eventos' }}">Eventos</a>
<a href="/panel/textos" class="{{ 'on' if sec == 'textos' }}">Textos</a>
<a href="/panel/logos" class="{{ 'on' if sec == 'logos' }}">Logos</a>
</div>
<form class="buscaid" method="get" action="/panel/buscar">
<input name="id" placeholder="ID del pedido" maxlength="6" size="9"
  value="{{ request.args.get('id', '') }}" autocomplete="off">
<button title="Buscar la seleccion por su ID">Buscar</button>
</form>
<a class="ver" href="/" target="_blank" rel="noopener">Ver el sitio &nearr;</a>
</nav>
"""

PANEL_CSS = """<style>
/* El panel, con la misma tematica que el sitio: el negro verdoso de la cancha,
   el verde del foco para lo que se toca, y monoespaciada en mayusculas para
   rotulos, datos y botones. Va aca y no en BASE_HTML porque ese bloque lo
   comparten las galerias de cliente, que tienen identidad propia (ver PIXIE).
   Es una herramienta de trabajo: densa, plana, de a un pelo de borde. El verde
   aparece solo en la accion principal y en lo que esta activo. */
header{padding:1rem 1.5rem;align-items:center;gap:.9rem;
  border-bottom:1px solid var(--linea);background:rgba(9,12,10,.9)}
header::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,rgba(198,240,74,.4),transparent);
  pointer-events:none}
header b{font-size:1.02rem;font-weight:700;letter-spacing:-.04em;color:var(--cal);
  opacity:1}
header span{font-family:var(--mono);font-size:.68rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--humo);opacity:1}
main{padding:1.6rem 1.5rem 5rem}
a{color:var(--cal);text-decoration:none;border-bottom:1px solid var(--linea2)}
a:hover{border-bottom-color:var(--cancha)}
h3{margin:2.2rem 0 .6rem;font-family:var(--mono);font-size:.7rem;font-weight:600;
  letter-spacing:.18em;text-transform:uppercase;color:var(--humo);
  display:flex;align-items:center;gap:.7rem}
h3::before{content:"";width:.4rem;height:.4rem;background:var(--cancha);flex:none}

/* ------------------------------------------------------------------ botones */
.btn{background:var(--cancha);color:var(--noche);border:1px solid var(--cancha);
  border-radius:2px;padding:.55rem 1.1rem;font-family:var(--mono);font-size:.7rem;
  font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  transition:box-shadow .18s}
.btn:hover{box-shadow:0 6px 20px rgba(198,240,74,.18)}
.btn.hueco{background:transparent;color:var(--cal);border-color:var(--linea2)}
.btn.hueco:hover{border-color:var(--cal);box-shadow:none}
.btn.mal{background:transparent;color:#FF8A9B;border-color:rgba(255,138,155,.35)}
.btn.mal:hover{background:rgba(255,138,155,.08);border-color:#FF8A9B;box-shadow:none}
.btn:disabled{opacity:.35;cursor:default;box-shadow:none}
.btn+.btn{margin-left:.5rem}

/* ------------------------------------------------------------- formularios */
input,select,textarea{background:var(--panel);border:1px solid var(--linea2);
  border-radius:2px;color:var(--cal);font:inherit;font-size:.92rem;
  padding:.5rem .7rem}
input[type=checkbox],input[type=radio]{padding:0;accent-color:var(--cancha)}
input[type=file]{padding:.4rem;font-size:.85rem}
::placeholder{color:#5d635a}
label{color:var(--humo)}
/* sin mayusculas: algunos de estos rotulos son una frase entera (el de
   precios son tres lineas) y en versales no se leen */
.campo span{font-family:var(--mono);font-size:.7rem;letter-spacing:.06em;
  text-transform:none;color:var(--humo)}
.publicar{background:var(--panel);border:1px solid var(--linea);border-radius:2px;
  padding:1rem}
.publicar input,.publicar select{background:#0C110B}
.link{background:var(--panel);border:1px solid var(--linea);border-radius:2px;
  padding:.6rem .7rem}
.link input{border:0;background:transparent;font-family:var(--mono);
  font-size:.84rem;letter-spacing:.02em;color:var(--cancha)}

/* ------------------------------------------------------------------ tablas */
table{margin:1.2rem 0}
th{font-family:var(--mono);font-size:.64rem;font-weight:600;letter-spacing:.16em;
  text-transform:uppercase;color:var(--humo);opacity:1;
  border-bottom:1px solid var(--linea2)}
td{border-bottom:1px solid var(--linea);font-size:.9rem}
td:not(:first-child){font-family:var(--mono);font-size:.82rem;
  font-variant-numeric:tabular-nums;color:var(--humo)}
tr:hover td{background:var(--panel)}
code{background:var(--panel);border:1px solid var(--linea);border-radius:2px;
  font-family:var(--mono);color:var(--cancha)}

/* ------------------------------------------------------------------ listas */
.migas{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;
  color:var(--humo);opacity:1;margin:.2rem 0 1.3rem}
.dirs,.evs{border-top:1px solid var(--linea)}
.dirs a,.evs a{border-bottom:1px solid var(--linea);color:var(--cal)}
.dirs a:hover,.evs a:hover{background:var(--panel);color:var(--cancha)}
.evs .fec,.evs .n,.col .cuantos{font-family:var(--mono);font-size:.72rem;
  letter-spacing:.06em;font-variant-numeric:tabular-nums;color:var(--humo);
  opacity:1}
.col h3{margin:0 0 .2rem;font-family:var(--sans);font-size:1.02rem;
  font-weight:600;letter-spacing:-.015em;color:var(--cal);text-transform:none}
.col h3::before{display:none}
.nav a{background:transparent;border:1px solid var(--linea2);border-radius:2px;
  font-family:var(--mono);font-size:.68rem;letter-spacing:.13em;
  text-transform:uppercase}
.nav a:hover{border-color:var(--cancha)}
.nav span{font-family:var(--mono);font-size:.68rem;letter-spacing:.13em;
  text-transform:uppercase;color:var(--humo);opacity:1}

/* ---------------------------------------------------------- subir y elegir */
.soltar{border:1px dashed var(--linea2);border-radius:2px}
.soltar.encima{border-color:var(--cancha);background:var(--panel)}
.soltar label{color:var(--cancha);text-decoration:none;
  border-bottom:1px solid var(--cancha)}
.tapas img,.tapas .nada{border-radius:2px;background:var(--panel)}
.tapas input:checked ~ img,.tapas input:checked ~ .nada{border-color:var(--cancha)}
.tapas.logos img{background:var(--panel)}
.combo{background:var(--panel);border:1px solid var(--linea);border-radius:2px}
.combo:hover{border-color:var(--cancha)}
.combo img{background:var(--noche)}
.barrasel{background:var(--noche);border-bottom:1px solid var(--linea)}
.barrasel button[type=button]{background:transparent;border:1px solid var(--linea2);
  color:var(--cal);border-radius:2px;font-family:var(--mono);font-size:.66rem;
  letter-spacing:.13em;text-transform:uppercase;padding:.4rem .8rem}
.barrasel button[type=button]:hover{border-color:var(--cancha)}
.barrasel .cuantas{font-family:var(--mono);font-size:.7rem;letter-spacing:.1em;
  color:var(--humo);opacity:1}
.elegir input{accent-color:#FF8A9B}
.celda{background:var(--panel);border-radius:2px}
.grid{gap:.4rem}


/* ----------------------------------------------------- barra del panel
   Misma idea que la del sitio publico, pero con las secciones del panel.
   Sin esto cada pagina era un callejon sin salida. */
.pbarra{position:sticky;top:0;z-index:60;display:flex;align-items:center;
  gap:1.6rem;padding:.8rem 1.5rem;background:rgba(9,12,10,.92);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--linea)}
.pbarra::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,rgba(198,240,74,.4),transparent);
  pointer-events:none}
.pbarra .logo{font-weight:700;font-size:1.02rem;letter-spacing:-.04em;
  color:var(--cal);border:0;flex:none}
.pbarra .logo span{color:var(--cancha)}
.pbarra .links{display:flex;gap:1.5rem;min-width:0;overflow-x:auto;
  scrollbar-width:none}
.pbarra .links::-webkit-scrollbar{display:none}
.pbarra a{font-family:var(--mono);font-size:.7rem;letter-spacing:.15em;
  text-transform:uppercase;color:var(--humo);border:0;white-space:nowrap;
  padding:.3rem 0;border-bottom:2px solid transparent}
.pbarra a:hover{color:var(--cal);border-bottom-color:transparent}
.pbarra .links a.on{color:var(--cal);border-bottom-color:var(--cancha)}
.pbarra .ver{margin-left:auto;flex:none}
@media (max-width:640px){
  .pbarra{gap:1rem;padding:.7rem 1rem}
  .pbarra .links{gap:1.1rem}
  .pbarra .ver{display:none}
}

/* --------------------------------------------------- cabecera de pagina */
.cab{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin:.3rem 0 .2rem}
.tit{font-size:1.5rem;letter-spacing:-.025em;margin:0}
.cab .btn{margin-left:auto}
.pista{color:var(--humo);font-size:.9rem;max-width:70ch;margin:.5rem 0 1.8rem}
.acciones{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:1.1rem 0}
.acciones .link{margin:0;flex:1 1 22rem}

/* ------------------------------------------------------------- estados
   Lo que antes habia que leer en prosa ("tiene contrasena", "en venta") se
   ve de un vistazo y con el mismo codigo en el indice y en la ficha. */
.chips{display:flex;gap:.35rem;flex-wrap:wrap}
.chip{font-family:var(--mono);font-size:.6rem;letter-spacing:.13em;
  text-transform:uppercase;padding:.2rem .5rem;border:1px solid var(--linea2);
  color:var(--humo);white-space:nowrap}
.chip.si{color:var(--cancha);border-color:rgba(198,240,74,.35)}
.chip.no{color:#FF8A9B;border-color:rgba(255,138,155,.35)}

/* -------------------------------------------------- el indice de galerias */
.gals{list-style:none;padding:0;margin:0 0 2rem;border-top:1px solid var(--linea)}
.grupo{font-family:var(--mono);font-size:.72rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--humo);font-weight:400;margin:1.6rem 0 .5rem}
.grupo a{margin-left:.6rem;color:var(--cancha)}
.carpeta{display:flex;flex-direction:column;gap:.7rem;margin-bottom:1.6rem;
  background:var(--panel);border:1px solid var(--linea);border-radius:2px;padding:1rem}
.carpeta>input[name=nombre]{max-width:24rem;font-size:1.05rem}
.carpeta .link{display:flex;gap:.6rem;align-items:center}
.carpeta .link input{flex:1;min-width:0}
.chkgal .gals{margin:0 0 .6rem}
.elige{grid-template-columns:4.5rem 1fr auto auto;cursor:pointer;padding:.45rem .6rem}
.elige .mini{grid-row:auto}
.elige .cifras{grid-column:3}
.elige input{grid-column:4;width:1.15rem;height:1.15rem;accent-color:var(--cancha);margin:0}
.elige:has(input:checked){background:#0C110B;box-shadow:inset 3px 0 var(--cancha)}
.elige:has(input:checked) .nom{color:var(--cancha)}
.grupo-mini{font-family:var(--mono);font-size:.62rem;letter-spacing:.13em;
  text-transform:uppercase;color:var(--humo);margin:.6rem 0 .1rem}
.gal{display:grid;grid-template-columns:5.5rem 1fr auto;gap:0 1.1rem;
  align-items:center;padding:.7rem 0;border-bottom:1px solid var(--linea)}
.gal:hover{background:var(--panel)}
/* la altura la pone el aspect-ratio de la IMAGEN, no un height:100% sobre un
   padre sin altura: asi la miniatura no se desborda a tamano natural */
.gal>*{min-width:0}
.gal .mini{grid-row:span 2;border:0;display:block;background:var(--panel);
  overflow:hidden;align-self:center}
.gal .mini img{width:100%;aspect-ratio:3/2;object-fit:cover;display:block}
.gal .nom{font-size:1rem;font-weight:600;letter-spacing:-.012em;color:var(--cal);
  border:0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gal:hover .nom{color:var(--cancha)}
.gal .cifras{grid-column:3;grid-row:1;font-family:var(--mono);font-size:.72rem;
  letter-spacing:.06em;color:var(--humo);text-align:right;white-space:nowrap}
.gal .chips{grid-column:2/4;grid-row:2;margin-top:.35rem}
@media (max-width:640px){
  .gal{grid-template-columns:4.2rem 1fr;gap:0 .8rem}
  .gal .cifras{grid-column:2;grid-row:2;text-align:left;margin-top:.2rem}
  .gal .chips{grid-column:2;grid-row:3}
}

/* ------------------------------------------------ formulario de la galeria
   El bloque de ajustes era un flex-wrap: los controles se reacomodaban con el
   ancho y quedaban campos sin relacion pegados. En grilla cada uno queda
   debajo de su rotulo y los grupos no se mezclan. */
.campos{display:grid;gap:1rem 1.3rem;margin:0 0 1.6rem;
  grid-template-columns:repeat(auto-fit,minmax(14rem,1fr))}
.campos .campo{margin:0}
.campos .campo input,.campos .campo select{width:100%}
/* los renglones de la edicion son una lista larga: ocupan la fila entera de
   la grilla, si no cada uno cae en una columna de 14rem y se parte palabra
   por palabra */
.campos .edlineas{grid-column:1/-1;max-width:none}
.edlineas .edwrap{display:grid;gap:.2rem 1.2rem;margin-top:.35rem;
  grid-template-columns:repeat(auto-fit,minmax(17rem,1fr))}
.edlineas .chk{align-self:start;padding-bottom:0;font-size:.85rem}
.chk{display:flex;align-items:center;gap:.55rem;color:var(--cal);
  font-size:.92rem;align-self:end;padding-bottom:.5rem}
.chk input{flex:none}
/* opciones de entrega: el campo extra aparece solo con su opcion elegida.
   ponytail: :has() (Chrome 105+, Firefox 121+); sin :has los extras quedan
   ocultos. Si alguna vez hace falta un navegador viejo, pasar a 3 lineas de JS. */
.entop .eo{display:none;margin-top:.45rem}
.entop:has(option[value=otro]:checked) .eo-otro,
.entop:has(option[value=elegir]:checked) .eo-elegir,
.entop:has(option[value=dias]:checked) .eo-dias{display:flex;flex-wrap:wrap;
  gap:.35rem .9rem;align-items:center}
.campos .entop .eo input[type=checkbox]{width:auto}
.campos .entop .eo-dias input{width:6rem}
.ancho{grid-column:1/-1}
.nota{color:var(--humo);font-size:.85rem;margin:.4rem 0 0;max-width:62ch}
.guardar{position:sticky;bottom:0;margin:0;padding:.9rem 0;
  background:linear-gradient(transparent,var(--noche) 40%);z-index:20}

/* --------------------------------------------------------------- avisos */
.aviso{color:#FF8A9B}
#fallos{color:#FF8A9B}
#avance{font-family:var(--mono);font-size:.78rem;letter-spacing:.08em;
  color:var(--humo)}
</style>"""

PANEL_VISOR = """
<div id="pv" hidden><span id="pvpos"></span><img id="pvimg" alt="">
<button id="pvcerrar" title="Cerrar (Esc)">&times;</button>
<button id="pvant" title="Anterior">&lsaquo;</button>
<button id="pvsig" title="Siguiente">&rsaquo;</button>
<button id="pvmarca" hidden></button></div>
<style>
#pv{position:fixed;inset:0;z-index:90;background:rgba(10,10,10,.96);
  display:grid;place-items:center}
/* el display:grid de arriba le gana al [hidden] del navegador por
   especificidad, asi que el visor quedaba SIEMPRE abierto tapando la pagina */
#pv[hidden]{display:none}
#pv img{max-width:96vw;max-height:92vh;object-fit:contain}
#pvpos{position:absolute;top:1rem;left:1.2rem;font-family:var(--mono);font-size:.72rem;letter-spacing:.12em;color:var(--humo)}
#pvcerrar{position:absolute;top:.7rem;right:1rem;background:transparent;border:0;
  color:#eee;font-size:2rem;cursor:pointer;line-height:1}
#pvant,#pvsig{position:absolute;top:50%;transform:translateY(-50%);
  background:rgba(0,0,0,.45);border:0;color:#eee;font-size:2.4rem;line-height:1;
  padding:.1rem .8rem;cursor:pointer;border-radius:9px}
#pvant{left:.6rem}
#pvsig{right:.6rem}
#pvmarca{position:absolute;bottom:1.4rem;left:50%;transform:translateX(-50%);
  background:transparent;border:1px solid var(--linea2);color:var(--cal);
  border-radius:2px;padding:.55rem 1.3rem;cursor:pointer;font-family:var(--mono);
  font-size:.68rem;font-weight:700;letter-spacing:.13em;text-transform:uppercase}
#pvmarca.on{background:rgba(255,138,155,.1);color:#FF8A9B;
  border-color:rgba(255,138,155,.5)}
main img[data-ver]{cursor:zoom-in}
</style>
<script>
// visor del panel: cualquier miniatura con data-ver entra, asi sirve igual en
// la lista de un cliente, en las elegidas y en las descargadas
const pvs = [...document.querySelectorAll("main img[data-ver]")];
let pvi = -1;
// la casilla de esa miniatura, si la pantalla tiene (solo la de quitar fotos)
const pvCaja = i => pvs[i] && pvs[i].closest(".elegir")
  ? pvs[i].closest(".elegir").querySelector('input[type="checkbox"]') : null;

function pvAbrir(i) {
  if (!pvs.length) return;
  pvi = (i + pvs.length) % pvs.length;
  pvimg.src = pvs[pvi].src;
  pvpos.textContent = (pvi + 1) + " / " + pvs.length + "  ·  " + (pvs[pvi].dataset.ver || "");
  const caja = pvCaja(pvi);
  pvmarca.hidden = !caja;
  if (caja) {
    pvmarca.textContent = caja.checked
      ? "\u2713 marcada para quitar  (x)" : "Marcar para quitar  (x)";
    pvmarca.classList.toggle("on", caja.checked);
  }
  pv.hidden = false;
  document.body.style.overflow = "hidden";
}
function pvCerrar() { pv.hidden = true; pvimg.src = ""; document.body.style.overflow = ""; }
function pvMarcar() {
  const caja = pvCaja(pvi);
  if (!caja) return;
  caja.checked = !caja.checked;
  // el change lo escucha el contador de la pantalla de quitar: sin esto el
  // boton de abajo seguiria diciendo que no hay ninguna marcada
  caja.dispatchEvent(new Event("change"));
  pvAbrir(pvi);
}
pvs.forEach((im, i) => im.onclick = () => pvAbrir(i));
pvcerrar.onclick = pvCerrar;
pvant.onclick = e => { e.stopPropagation(); pvAbrir(pvi - 1); };
pvsig.onclick = e => { e.stopPropagation(); pvAbrir(pvi + 1); };
pvmarca.onclick = e => { e.stopPropagation(); pvMarcar(); };
pv.onclick = e => { if (e.target === pv) pvCerrar(); };
document.addEventListener("keydown", e => {
  if (pv.hidden) return;
  if (e.key === "Escape") pvCerrar();
  else if (e.key === "ArrowLeft") pvAbrir(pvi - 1);
  else if (e.key === "ArrowRight") pvAbrir(pvi + 1);
  else if (e.key === "x" || e.key === "X") { e.preventDefault(); pvMarcar(); }
});
</script>"""


PANEL_FOTOS = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">{{ gal['nombre'] }}</h1>
<p class="migas"><a href="/panel/{{ gal['slug'] }}">&larr; {{ gal['nombre'] }}</a>
&middot; {{ total }} fotos &middot;
<a href="/panel/nueva?a={{ gal['slug'] }}">+ agregar fotos de una carpeta</a>
&middot; <a href="?{{ '' if nuevas else 'orden=nuevas' }}">{{ 'ver desde la primera'
  if nuevas else 'ver desde la ultima' }}</a></p>

{% if request.args.get('quitadas') %}<p style="opacity:.6;font-size:.9rem">
Saque {{ request.args.get('quitadas') }} fotos de la galeria. Los archivos
siguen en el disco.</p>{% endif %}
{% if request.args.get('puestas') %}<p style="opacity:.6;font-size:.9rem">
Agregue {{ request.args.get('puestas') }} fotos.
{% if request.args.get('repes') %}{{ request.args.get('repes') }} ya estaban y
las saltee.{% endif %}
{% if request.args.get('faltan') %}<span class="aviso">{{
  request.args.get('faltan') }} no se pudieron leer.</span>{% endif %}</p>{% endif %}

{% if fotos %}
<form method="post" action="/panel/{{ gal['slug'] }}/fotos/quitar"
  onsubmit="return confirm('Sacar las fotos marcadas de esta galeria?\n\nLos archivos NO se borran del disco. Se pierden los me-gusta y el registro de descargas de esas fotos.')">
<input type="hidden" name="p" value="{{ p }}">
<input type="hidden" name="orden" value="{{ 'nuevas' if nuevas else '' }}">
<div class="barrasel">
<button type="button" id="todas">Marcar toda la pagina</button>
<button type="button" id="ninguna">Ninguna</button>
<span class="cuantas" id="cuantas">0 marcadas</span>
<button class="btn mal" id="bquitar" disabled>Quitar las marcadas</button>
</div>
<div class="grid">{% for f in fotos %}<figure class="celda elegir" style="margin:0">
<input type="checkbox" name="foto" value="{{ f['id'] }}"
  title="Marcar para quitar" aria-label="{{ nombre(f['ruta']) }}">
<img src="/panel/p/{{ f['id'] }}" loading="lazy" alt=""
  data-ver="{{ nombre(f['ruta']) }}"></figure>{% endfor %}</div>
</form>
<p style="opacity:.4;font-size:.85rem;margin:.8rem 0 0">Tocá una foto para verla
en grande. Adentro: flechas para pasar, <b>x</b> para marcarla, Esc para salir.</p>
{% else %}<p style="opacity:.55">Esta galeria no tiene fotos.
<a href="/panel/nueva?a={{ gal['slug'] }}">Agregale algunas</a>.</p>{% endif %}

{% if paginas > 1 %}<p class="nav">
{% set oo = '&orden=nuevas' if nuevas else '' %}
{% if p > 1 %}<a href="?p={{ p - 1 }}{{ oo }}">&larr; anterior</a>{% else %}<span></span>{% endif %}
<span>pagina {{ p }} de {{ paginas }}</span>
{% if p < paginas %}<a href="?p={{ p + 1 }}{{ oo }}">siguiente &rarr;</a>{% else %}<span></span>{% endif %}
</p>{% endif %}

<script>
const cajas = [...document.querySelectorAll('input[name="foto"]')];
const cuantas = document.getElementById("cuantas");
const bquitar = document.getElementById("bquitar");
function contar() {
  const n = cajas.filter(c => c.checked).length;
  cuantas.textContent = n + " marcadas";
  bquitar.disabled = !n;
}
cajas.forEach(c => c.onchange = contar);
document.getElementById("todas").onclick = () => {
  cajas.forEach(c => c.checked = true); contar();
};
document.getElementById("ninguna").onclick = () => {
  cajas.forEach(c => c.checked = false); contar();
};
contar();
</script>
</main>""" + PANEL_VISOR

PANEL_SIN_ID = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Ese ID no existe</h1>
<p class="nota">Busque <code>{{ buscado }}</code> y no hay ninguna seleccion con
ese ID. Fijate que sean los 6 caracteres tal cual te los mando el cliente.</p>
<p><a class="btn" href="/panel">Volver al panel</a></p>
</main>"""

COM_CUERPO = """<main>
<h1 class="tit">Comentarios</h1>
<p class="migas"><a href="{{ ('/panel/' if panel else '/g/') ~ gal['slug'] }}">&larr;
{{ gal['nombre'] }}</a> &middot; {{ filas|length }}
{{ 'comentario' if filas|length == 1 else 'comentarios' }}
&middot; del mas nuevo al mas viejo</p>
{% for c in filas %}<div class="com">
<img src="{{ ('/panel/p/' if panel else '/p/') ~ c['foto_id'] }}" loading="lazy" alt=""
  style="width:6rem;aspect-ratio:3/2;object-fit:cover;border-radius:4px;display:block">
<div style="flex:1">
<p style="margin:0"><b>{{ c['nombre'] or 'Alguien' }}</b>
<span style="opacity:.5">{{ hora_local(c['cuando']) }}</span>
{% if panel %}<span style="opacity:.4">&middot; {{ c['email'] }}
&middot; {{ nombre(c['ruta']) }}</span>{% endif %}</p>
<p style="margin:.25rem 0 0;white-space:pre-wrap">{{ c['texto'] }}</p>
</div>
{% if panel %}<form method="post"
  action="/panel/{{ gal['slug'] }}/comentario/{{ c['id'] }}/borrar"
  onsubmit="return confirm('Borrar este comentario?')">
<button class="btn mal" style="font-size:.75rem;padding:.2rem .6rem">Borrar</button></form>
{% elif c['id'] in mios %}<button class="btn" data-borrar="{{ c['id'] }}"
  style="font-size:.75rem;padding:.2rem .6rem">Borrar</button>{% endif %}
</div>
{% else %}<p style="opacity:.55">Todavia nadie comento.</p>{% endfor %}
</main>
<style>.com{display:flex;gap:.8rem;align-items:flex-start;padding:.7rem 0;
  border-bottom:1px solid #2a2a2a}</style>
{% if not panel %}<script>
document.querySelectorAll("[data-borrar]").forEach(b => b.onclick = async () => {
  if (!confirm("Borrar tu comentario?")) return;
  const r = await fetch("/api/comentario/" + b.dataset.borrar + "/borrar",
                        {method: "POST"});
  if (r.ok) location.reload();
});
</script>{% endif %}"""


PANEL_CLIENTE = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">{{ cli['email'] }}</h1>
<p class="migas"><a href="/panel/{{ gal['slug'] }}">&larr; {{ gal['nombre'] }}</a>
&middot; {{ visitas|length }} visitas &middot; vio {{ vistas }} de {{ total }} fotos
&middot; marco {{ marcas }} &middot; bajo {{ bajadas }}</p>
<p class="nota">Horas de Chile. La primera senal fue
{{ hora_local(visitas[0]['desde']) if visitas else 'ninguna' }} y la ultima
{{ hora_local(visitas[-1]['hasta']) if visitas else '-' }}.
Se cuenta por lo bajo: la foto que el navegador ya tiene guardada no la vuelve
a pedir, asi que pudo ver mas de lo que dice, nunca menos.</p>

<h3>Sus visitas <span style="opacity:.4;font-size:.8rem;font-weight:400">media
hora sin senales y ya cuenta como otra</span></h3>
<table><tr><th>Cuando</th><th>Duro</th><th>Fotos</th><th>Abrio grande</th>
<th>Marco</th><th>Bajo</th></tr>
{% for v in visitas %}<tr>
<td>{{ hora_local(v['desde']) }} &rarr; {{ hora_local(v['hasta'], '%H:%M') }}</td>
<td style="opacity:.6">{{ v['minutos'] }} min</td>
<td>{{ v['fotos'] }}</td><td>{{ v['visor'] }}</td>
<td>{{ v['marcas'] }}</td><td>{{ v['bajadas'] }}</td></tr>
{% else %}<tr><td colspan="6">Entro pero no llego a mirar nada.</td></tr>
{% endfor %}</table>

<h3>Que hizo, en orden <span style="opacity:.4;font-size:.8rem;font-weight:400">de
lo mas nuevo a lo mas viejo, hasta 200</span></h3>
<table><tr><th></th><th>Cuando</th><th>Que</th><th>Archivo</th></tr>
{% for x in linea %}<tr>
<td style="width:6.5rem"><img src="/panel/p/{{ x['foto'] }}" loading="lazy" alt=""
  style="width:6rem;aspect-ratio:3/2;object-fit:cover;display:block;border-radius:4px"></td>
<td style="opacity:.6">{{ hora_local(x['cuando']) }}</td>
<td>{% if x['tipo'] == 'visor' %}la abrio en grande
{% elif x['tipo'] == 'marco' %}la marco en <b>{{ x['detalle'] }}</b>
{% else %}la bajo{% if x['detalle'] == 'raw' %} en RAW{% endif %}{% endif %}</td>
<td><code>{{ nombre(rutas.get(x['foto'], '?')) }}</code></td></tr>
{% else %}<tr><td colspan="4">Nada todavia: solo paso por la grilla.</td></tr>
{% endfor %}</table>
</main>"""


PANEL_COMENTARIOS = PANEL_CSS + PANEL_BARRA + COM_CUERPO
COMENTARIOS_CLIENTE = ("""<header><b>rb.fotos</b> <span>{{ gal['nombre'] }}</span></header>""" + COM_CUERPO)


PANEL_LISTA = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">{{ l['nombre'] }} de {{ l['email'] }}</h1><p class="migas"><a href="/panel/{{ gal['slug'] }}">&larr; {{ gal['nombre'] }}</a>
&middot; {{ fotos|length }} fotos{% if codigo %} &middot; ID
<code>{{ codigo }}</code>{% endif %}</p>
{% if compra %}<div class="compra" style="border:1px solid #3a3a3a;padding:1rem 1.2rem;margin:1rem 0">
{% set accion = "/panel/" ~ gal['slug'] ~ "/lista/" ~ l['id'] ~ "/" %}
{% if compra.estado == "error" %}<p>No se pudo: {{ compra.error }}</p>
{% elif compra.estado == "sin_raw" %}<p>Ninguna foto de esta lista tiene RAW vinculado: no hay de donde copiar.</p>
{% elif compra.estado == "nueva" %}<form method="post" action="{{ accion }}solicitar">
<button class="btn">Lista solicitada</button></form>
<p class="nota">Copia los RAW a <code>{{ win(compra.ped) }}</code> y arma
<code>{{ win(compra.ent) }}</code> con RAW y Export adentro.</p>
{% elif compra.estado == "pedida" %}<p><b>Pedida.</b> {{ compra.raws }} RAW para editar en
<code>{{ win(compra.ped) }}</code></p>
<p class="nota">Exporta los JPEG editados a <code>{{ win(compra.ent) }}\\Export</code>. Al marcarla
entregada los RAW pasan a <code>{{ win(compra.ent) }}\\RAW</code> y la carpeta de pedidos se borra.</p>
<form method="post" action="{{ accion }}entregar" style="display:inline"
  onsubmit="return confirm('Mover los RAW a entregados y sacarla de pedidos?')">
<button class="btn">Marcar entregada</button></form>
<form method="post" action="{{ accion }}solicitar" style="display:inline">
<button class="btn" style="background:transparent;color:#eee;border:1px solid #3a3a3a;margin-left:.5rem">Copiar las que falten</button></form>
{% else %}<p><b>Entregada.</b> RAW y Export en <code>{{ win(compra.ent) }}</code></p>{% endif %}
{% if compra.sin_raw %}<p class="nota">{{ compra.sin_raw }} foto(s) sin RAW vinculado: esas buscalas a mano.</p>{% endif %}
</div>{% endif %}
<div class="grid">{% for f in fotos %}<figure class="celda" style="margin:0">
<img src="/panel/p/{{ f['id'] }}" loading="lazy" alt="" data-ver="{{ nombre(f['ruta']) }}">
</figure>{% else %}<p>La lista esta vacia.</p>{% endfor %}</div>
{% if fotos %}<h3>Los nombres de archivo</h3>
<p class="nota">Separados por espacio, que es como los come el buscador de
Lightroom y el del explorador. El boton copia los {{ fotos|length }} de una.</p>
<textarea id="noms" readonly rows="{{ [1 + fotos|length // 6, 12]|min }}"
  onclick="this.select()">{{ nombres }}</textarea>
<p><button class="btn" id="bcopiar">Copiar los {{ fotos|length }} nombres</button>
<button class="btn" id="bcopiar2" style="background:transparent;color:#eee;
  border:1px solid #3a3a3a;margin-left:.5rem">Copiar sin extension</button></p>
<script>
// clipboard.writeText necesita https o localhost; detras de Cloudflare hay
// https, pero si algun dia se abre por IP pelada el fallback lo salva
function copiar(texto, boton) {
  const listo = () => { const t = boton.textContent;
                        boton.textContent = "Copiado";
                        setTimeout(() => boton.textContent = t, 1500); };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(texto).then(listo, () => alert(texto));
  } else {
    const a = document.getElementById("noms");
    a.select(); document.execCommand("copy"); listo();
  }
}
const noms = document.getElementById("noms");
document.getElementById("bcopiar").onclick = function () {
  copiar(noms.value, this);
};
document.getElementById("bcopiar2").onclick = function () {
  copiar(noms.value.split(" ").map(n => n.replace(/\\.[^.]+$/, ""))
                   .join(" "), this);
};
</script>{% endif %}
</main>""" + PANEL_VISOR


def carpeta_evento(raw):
    """El evento de un RAW: la carpeta que contiene a RAW/, o None."""
    for p in Path(raw).parents:
        if p.name.lower() == "raw":
            return p.parent
    return None


def _libre(destino):
    """destino, o destino-2, -3... si ya existe. Dos tarjetas pueden traer el
    mismo DSC00001 en un mismo pedido: aplanar sin esto pisaria uno."""
    d, n = Path(destino), 2
    while d.exists():
        d = Path(destino).with_name("%s-%d%s" % (Path(destino).stem, n, Path(destino).suffix))
        n += 1
    return d


def _ya_copiada(carpeta, origen):
    """Esta foto ya esta en la carpeta, con su nombre o renombrada -2, -3.
    Tamaño y fecha EXIF juntos: el nombre solo no alcanza (contador
    reiniciado) y el tamaño solo tampoco (hay ARW que pesan exacto igual)."""
    tam, fecha = origen.stat().st_size, None
    for a in carpeta.glob(origen.stem + "*" + origen.suffix):
        if a.stat().st_size == tam:
            fecha = fecha or _fecha_captura_raw(origen)
            if _fecha_captura_raw(a) == fecha:
                return True
    return False


def carpeta_pedido(lid, nombre):
    """"<ID>-<nombre de la lista>": la carpeta del pedido dice que es (pedido de
    Renzo, 7/10/2026). Sin los caracteres que Windows no acepta en un nombre."""
    limpio = re.sub(r'[\\/:*?"<>|]', "", nombre or "").strip(" .")
    return codigo_lista(lid) + ("-" + limpio if limpio else "")


def carpetas_compra(fotos, codigo):
    """(pedidos/<carpeta>, entregados/<carpeta>) dentro de <evento>/Compras, o
    None si ninguna foto tiene RAW para saber cual es el evento. Un pedido de
    antes del 7/10 se llama solo <ID>: si esta y el nuevo no, sigue siendo ese."""
    ev = next((carpeta_evento(f["raw"]) for f in fotos
               if f["raw"] and carpeta_evento(f["raw"])), None)
    if ev is None:
        return None
    c = ev / "Compras"
    viejo = codigo.split("-", 1)[0]
    if viejo != codigo and not any((c / s / codigo).exists() for s in ("pedidos", "entregados"))             and any((c / s / viejo).exists() for s in ("pedidos", "entregados")):
        codigo = viejo
    return c / "pedidos" / codigo, c / "entregados" / codigo


def estado_compra(fotos, codigo):
    """Como esta el pedido en disco, sin tocar nada: "nueva", "pedida" o
    "entregada" (entregados/<ID> esta y pedidos/<ID> ya no)."""
    sin_raw = sum(1 for f in fotos if not f["raw"])
    cs = carpetas_compra(fotos, codigo)
    if cs is None:
        return {"estado": "sin_raw", "sin_raw": sin_raw}
    ped, ent = cs
    if ped.is_dir():
        estado = "pedida"
    elif ent.is_dir():
        estado = "entregada"
    else:
        estado = "nueva"
    return {"estado": estado, "ped": ped, "ent": ent, "sin_raw": sin_raw,
            "raws": sum(1 for _ in ped.iterdir()) if estado == "pedida" else 0}


def solicitar_compra(fotos, codigo):
    """Copia los RAW de la lista a Compras/pedidos/<ID> y arma
    Compras/entregados/<ID>/RAW y /Export para lo que se entregue.

    Repetirlo solo copia lo que falte (si la lista crecio); lo ya copiado se
    reconoce por tamaño y fecha EXIF. Una entregada no se vuelve a pedir.
    """
    cs = carpetas_compra(fotos, codigo)
    if cs is None:
        return
    ped, ent = cs
    if ent.is_dir() and not ped.exists():
        return
    ped.mkdir(parents=True, exist_ok=True)
    (ent / "RAW").mkdir(parents=True, exist_ok=True)
    (ent / "Export").mkdir(exist_ok=True)
    # ponytail: copia dentro del POST, ~2 s por 10 ARW leyendo local a
    # 272 MB/s. Si algun pedido trae cientos, pasarlo a un hilo con avance.
    for f in fotos:
        if not f["raw"] or not os.path.exists(f["raw"]):
            continue
        origen = Path(f["raw"])
        if _ya_copiada(ped, origen):
            continue
        destino = _libre(ped / origen.name)
        tmp = destino.with_name(destino.name + ".parcial")
        shutil.copyfile(origen, tmp)
        os.replace(tmp, destino)                 # nunca queda un RAW a medias


def entregar_compra(fotos, codigo):
    """Mueve los RAW de pedidos/<ID> a entregados/<ID>/RAW, sin pisar nada,
    y saca pedidos/<ID>. Un .parcial (copia cortada) se borra: es basura."""
    cs = carpetas_compra(fotos, codigo)
    if cs is None or not cs[0].is_dir():
        return
    ped, ent = cs
    (ent / "RAW").mkdir(parents=True, exist_ok=True)
    (ent / "Export").mkdir(exist_ok=True)
    for a in ped.iterdir():
        if a.name.endswith(".parcial"):
            a.unlink()
        elif a.is_file():
            os.replace(a, _libre(ent / "RAW" / a.name))
    ped.rmdir()             # si quedo algo que no es archivo, falla y avisa


def ruta_windows(p):
    """/mnt/fotos/Fotos/x -> Fotos\\x: sin letra, porque la notebook monta el
    mismo disco como K:, Y: o Z: segun donde este."""
    try:
        return str(Path(p).relative_to(RAIZ_FOTOS)).replace("/", "\\")
    except ValueError:
        return str(p)


HUECO = 30 * 60      # sin senales por mas de esto, volvio: es otra visita


def hechos_de(cx, cid):
    """Todo lo que hizo el cliente, con hora, de lo mas viejo a lo mas nuevo.

    `vista` guarda por foto la primera y la ultima vez, no cada golpe: de ahi
    salen dos hechos y no mil, que es justo lo que se necesita para saber
    cuando estuvo. Las marcas viejas (cuando NULL) no entran: no se sabe
    cuando fueron y meterlas inventaria una visita.
    """
    h = []
    for r in cx.execute("SELECT foto_id, visor, primera, ultima FROM vista "
                        "WHERE cliente_id = ?", (cid,)):
        tipo = "visor" if r["visor"] else "grilla"
        h.append({"cuando": r["primera"], "tipo": tipo, "foto": r["foto_id"]})
        if r["ultima"] != r["primera"]:
            h.append({"cuando": r["ultima"], "tipo": tipo, "foto": r["foto_id"]})
    for r in cx.execute("SELECT foto_id, cual, cuando FROM descarga "
                        "WHERE cliente_id = ?", (cid,)):
        h.append({"cuando": r["cuando"], "tipo": "bajo", "foto": r["foto_id"],
                  "detalle": r["cual"]})
    for r in cx.execute(
            "SELECT lista_foto.foto_id, lista.nombre, lista_foto.cuando "
            "FROM lista_foto JOIN lista ON lista.id = lista_foto.lista_id "
            "WHERE lista.cliente_id = ? AND lista_foto.cuando IS NOT NULL", (cid,)):
        h.append({"cuando": r["cuando"], "tipo": "marco", "foto": r["foto_id"],
                  "detalle": r["nombre"]})
    return sorted(h, key=lambda x: x["cuando"])


def visitas_cliente(cx, cid):
    """Los hechos cortados en visitas: mas de HUECO sin nada = volvio."""
    visitas = []
    for x in hechos_de(cx, cid):
        t = datetime.fromisoformat(x["cuando"])
        v = visitas[-1] if visitas else None
        if v is None or (t - v["_fin"]).total_seconds() > HUECO:
            v = {"desde": x["cuando"], "hasta": x["cuando"], "_fin": t,
                 "_vistas": set(), "_visor": set(), "marcas": 0, "bajadas": 0}
            visitas.append(v)
        v["hasta"], v["_fin"] = x["cuando"], t
        if x["tipo"] in ("grilla", "visor"):
            v["_vistas"].add(x["foto"])
            if x["tipo"] == "visor":
                v["_visor"].add(x["foto"])
        elif x["tipo"] == "marco":
            v["marcas"] += 1
        elif x["tipo"] == "bajo":
            v["bajadas"] += 1
    for v in visitas:
        v["fotos"] = len(v["_vistas"])
        v["visor"] = len(v["_visor"])
        v["minutos"] = round((datetime.fromisoformat(v["hasta"])
                              - datetime.fromisoformat(v["desde"])).total_seconds() / 60)
    return visitas


def _cliente_ficha(slug, cid):
    """El cliente tiene que ser DE esta galeria: si no, el id de un cliente de
    otro evento mostraria su actividad desde cualquier panel."""
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    cli = cx.execute("SELECT * FROM cliente WHERE id = ? AND galeria_id = ?",
                     (cid, gal["id"])).fetchone()
    if cli is None:
        abort(404)
    return cx, gal, cli


@app.get("/panel/<slug>/cliente/<int:cid>")
def panel_cliente(slug, cid):
    cx, gal, cli = _cliente_ficha(slug, cid)
    visitas = visitas_cliente(cx, cid)
    rutas = {r["id"]: r["ruta"] for r in
             cx.execute("SELECT id, ruta FROM foto WHERE galeria_id = ?", (gal["id"],))}
    # la linea de tiempo cuenta lo que se hizo a proposito: abrir en grande,
    # marcar, bajar. Las miniaturas que pasaron volando ya estan contadas en
    # la fila de cada visita, y en una galeria de 1.268 serian puro ruido.
    linea = [x for x in reversed(hechos_de(cx, cid))
             if x["tipo"] != "grilla"][:200]
    total = cx.execute("SELECT COUNT(*) FROM foto WHERE galeria_id = ?",
                       (gal["id"],)).fetchone()[0]
    marcas = cx.execute(
        "SELECT COUNT(*) FROM lista_foto JOIN lista ON lista.id = lista_foto.lista_id "
        "WHERE lista.cliente_id = ?", (cid,)).fetchone()[0]
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_CLIENTE, gal=gal, cli=cli, visitas=visitas, linea=linea,
        hora_local=hora_local,
        rutas=rutas, total=total, marcas=marcas, nombre=os.path.basename,
        vistas=len({x["foto"] for x in hechos_de(cx, cid)
                    if x["tipo"] in ("grilla", "visor")}),
        bajadas=sum(v["bajadas"] for v in visitas)))


def ultimos_comentarios(cx, gid, tope=200):
    """Los ultimos de la galeria, sin tener que ir a buscar foto por foto."""
    return cx.execute(
        "SELECT comentario.id, comentario.texto, comentario.cuando, "
        "  comentario.foto_id, cliente.nombre, cliente.email, foto.ruta "
        "FROM comentario "
        "JOIN foto ON foto.id = comentario.foto_id "
        "JOIN cliente ON cliente.id = comentario.cliente_id "
        "WHERE foto.galeria_id = ? ORDER BY comentario.id DESC LIMIT ?",
        (gid, tope)).fetchall()


@app.get("/g/<slug>/comentarios")
def comentarios_galeria(slug):
    gal = _gal_comenta(slug)
    if gal["id"] not in entradas():
        abort(404)
    cx = db()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        COMENTARIOS_CLIENTE, gal=gal, filas=ultimos_comentarios(cx, gal["id"]),
        hora_local=hora_local, panel=False,
        mios={r["id"] for r in cx.execute(
            "SELECT comentario.id FROM comentario WHERE cliente_id = ?",
            (cliente_actual(gal["id"]),))}))


@app.get("/panel/<slug>/cliente/<int:cid>/descargadas")
def panel_descargadas(slug, cid):
    """Lo que bajo ese cliente, en la misma pagina que una lista. No es una
    lista: sale de `descarga`, y por eso no hay nada que sacar de aca."""
    cx, gal, cli = _cliente_ficha(slug, cid)
    fotos = cx.execute(
        "SELECT foto.id, foto.ruta, foto.raw, MAX(descarga.cuando) AS ult "
        "FROM descarga JOIN foto ON foto.id = descarga.foto_id "
        "WHERE descarga.cliente_id = ? AND foto.galeria_id = ? "
        "GROUP BY foto.id ORDER BY ult DESC", (cid, gal["id"])).fetchall()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_LISTA, gal=gal, fotos=fotos, nombre=os.path.basename,
        l={"id": "", "nombre": "Descargadas", "email": cli["email"]},
        codigo=None, compra=None, win=ruta_windows,
        nombres=" ".join(os.path.basename(f["ruta"]) for f in fotos)))


@app.get("/panel/<slug>/comentarios")
def panel_comentarios(slug):
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_COMENTARIOS, gal=gal, filas=ultimos_comentarios(cx, gal["id"]),
        hora_local=hora_local, panel=True, mios=set(),
        nombre=os.path.basename))


@app.post("/panel/<slug>/comentario/<int:com_id>/borrar")
def panel_borrar_comentario(slug, com_id):
    """Desde el panel se borra cualquiera, no solo los propios: con los
    comentarios a la vista de todos, alguien va a escribir una tontera."""
    cx = db()
    gal = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    cx.execute("DELETE FROM comentario WHERE id = ? AND foto_id IN "
               "(SELECT id FROM foto WHERE galeria_id = ?)", (com_id, gal["id"]))
    cx.commit()
    return redirect(url_for("panel_comentarios", slug=slug))


def _lista_de(slug, lid):
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    l = cx.execute(
        "SELECT lista.*, cliente.email FROM lista "
        "JOIN cliente ON cliente.id = lista.cliente_id "
        "WHERE lista.id = ? AND cliente.galeria_id = ?", (lid, gal["id"])).fetchone()
    if l is None:
        abort(404)
    fotos = cx.execute(
        "SELECT foto.id, foto.ruta, foto.raw FROM lista_foto "
        "JOIN foto ON foto.id = lista_foto.foto_id "
        "WHERE lista_foto.lista_id = ? ORDER BY foto.orden, foto.id",
        (lid,)).fetchall()
    return gal, l, fotos


@app.post("/panel/<slug>/lista/<int:lid>/<any(solicitar, entregar):accion>")
def panel_compra(slug, lid, accion):
    _, l, fotos = _lista_de(slug, lid)
    hacer = solicitar_compra if accion == "solicitar" else entregar_compra
    try:
        hacer(fotos, carpeta_pedido(lid, l["nombre"]))
    except OSError as e:                     # disco desmontado o solo lectura
        return redirect(url_for("panel_lista", slug=slug, lid=lid, error=str(e)))
    return redirect(url_for("panel_lista", slug=slug, lid=lid))


@app.get("/panel/<slug>/lista/<int:lid>")
def panel_lista(slug, lid):
    """La lista de un cliente, en grande. Caddy protege /panel*, asi que aca
    no hace falta sesion de cliente: alcanza con que la lista sea de esta
    galeria, para no cruzar selecciones entre eventos."""
    gal, l, fotos = _lista_de(slug, lid)
    compra = None
    if fotos:
        try:
            compra = estado_compra(fotos, carpeta_pedido(lid, l["nombre"]))
        except OSError as e:
            compra = {"estado": "error", "error": str(e)}
    if request.args.get("error"):
        compra = {"estado": "error", "error": request.args["error"]}
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_LISTA, gal=gal, l=l, fotos=fotos, nombre=os.path.basename,
        codigo=codigo_lista(lid), compra=compra, win=ruta_windows,
        # separados por espacio: es lo que Lightroom acepta de una
        # pegada. Un nombre con espacio adentro rompe la vuelta, pero
        # los de camara no los tienen y el que renombra a mano copia
        # del cuadro igual.
        nombres=" ".join(os.path.basename(f["ruta"]) for f in fotos)))


@app.get("/panel/buscar")
def panel_buscar():
    """Del ID que manda el cliente por WhatsApp a su seleccion, sin pedirle
    el correo."""
    cx = db()
    lid = lista_por_codigo(cx, request.args.get("id"))
    if lid is None:
        return render_template_string(BASE_HTML, cuerpo=render_template_string(
            PANEL_SIN_ID, buscado=(request.args.get("id") or "").strip())), 404
    gal = cx.execute(
        "SELECT galeria.slug FROM lista JOIN cliente ON cliente.id = lista.cliente_id "
        "JOIN galeria ON galeria.id = cliente.galeria_id WHERE lista.id = ?",
        (lid,)).fetchone()
    if gal is None:
        abort(404)
    return redirect(url_for("panel_lista", slug=gal["slug"], lid=lid))


@app.get("/panel/<slug>/firma")
def ver_firma(slug):
    """Como queda una foto entregada con la firma puesta, para mirarla antes
    de que la baje un cliente. ?foto=<id> para probar en una vertical."""
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    # los ?logo=/?pos=/?tam= son para PROBAR desde el panel sin guardar; si no
    # vienen, se muestra lo que la galeria tiene puesto
    firma = firma_de(gal)
    logo = request.args.get("logo") or (firma[0] if firma else "")
    if logo not in set(logos_disponibles()):
        abort(404)
    pos = request.args.get("pos") or (firma[1] if firma else FIRMA_POS)
    pos = pos if pos in POSICIONES else FIRMA_POS
    try:
        tam = int(request.args.get("tam") or (firma[2] if firma else FIRMA_TAM))
    except ValueError:
        tam = FIRMA_TAM
    tam = min(40, max(5, tam))
    pedida = request.args.get("foto", "")
    f = db().execute(
        "SELECT ruta FROM foto WHERE galeria_id = ? AND (? = '' OR id = ?) "
        "ORDER BY orden, id LIMIT 1",
        (gal["id"], pedida, pedida if pedida.isdigit() else -1)).fetchone()
    if f is None or not dentro_de_raiz(f["ruta"]) or not os.path.exists(f["ruta"]):
        abort(404)
    try:
        return send_file(firmada(f["ruta"], logo, pos, tam, ANCHO_MUESTRA_FIRMA),
                         mimetype="image/jpeg")
    except Exception:
        app.logger.exception("no pude firmar la muestra de %s", slug)
        abort(404)


@app.get("/panel/<slug>/fotos")
def panel_fotos(slug):
    """Las fotos de la galeria, para sacar las que no van. Paginado: una fecha
    son 3000 fotos y pintarlas todas cuelga el navegador."""
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    total = cx.execute("SELECT COUNT(*) c FROM foto WHERE galeria_id = ?",
                       (gal["id"],)).fetchone()["c"]
    paginas = max(1, -(-total // PAGINA))
    try:
        p = min(max(1, int(request.args.get("p", 1))), paginas)
    except ValueError:
        p = 1
    nuevas = request.args.get("orden") == "nuevas"
    dire = "DESC" if nuevas else "ASC"
    fotos = cx.execute(
        f"SELECT id, ruta FROM foto WHERE galeria_id = ? "
        f"ORDER BY orden {dire}, id {dire} LIMIT ? OFFSET ?",
        (gal["id"], PAGINA, (p - 1) * PAGINA)).fetchall()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_FOTOS, gal=gal, fotos=fotos, total=total, p=p, paginas=paginas,
        nuevas=nuevas, nombre=os.path.basename))


@app.post("/panel/<slug>/fotos/quitar")
def quitar_fotos(slug):
    """Saca fotos de la galeria. NO borra nada del disco: solo deja de
    mostrarlas. Se lleva puestos los me-gusta y el registro de descargas de
    esas fotos, que es lo que dice la confirmacion."""
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    ids = [int(i) for i in request.form.getlist("foto") if i.isdigit()]
    if ids:
        marcas = ",".join("?" * len(ids))
        # el AND galeria_id es el guard: sin el, un id de otra galeria metido
        # a mano en el formulario borraria la foto de esa otra galeria
        cx.execute(f"DELETE FROM foto WHERE galeria_id = ? AND id IN ({marcas})",
                   [gal["id"]] + ids)
        # la tapa no puede quedar apuntando a una foto que ya no esta
        cx.execute("UPDATE galeria SET portada_foto_id = NULL WHERE id = ? AND "
                   "portada_foto_id NOT IN (SELECT id FROM foto WHERE galeria_id = ?)",
                   (gal["id"], gal["id"]))
        cx.commit()
    return redirect(url_for("panel_fotos", slug=slug, p=request.form.get("p", 1),
                            orden=request.form.get("orden") or None,
                            quitadas=len(ids) or None))


@app.post("/panel/<slug>/agregar")
def agregar(slug):
    """Suma a la galeria una carpeta entera o un evento del catalogo."""
    cx = db()
    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    eid = (request.form.get("evento") or "").strip()
    if eid.isdigit():
        cat = catalogo()
        if cat is None:
            abort(404)
        rutas = [r["ruta"] for r in cat.execute(
            "SELECT f.ruta FROM evento_foto ef JOIN foto f ON f.id = ef.foto_id "
            "WHERE ef.evento_id = ? ORDER BY f.fecha, f.id", (int(eid),))]
        verificar = False
    else:
        elegida = _carpeta((request.form.get("dir") or "").strip("/"))
        carpeta = _resolver_export(elegida)
        rutas = fotos_de(carpeta, request.form.get("sub") == "1" or carpeta != elegida)
        verificar = True
    puestas, faltan, repes = agregar_fotos(cx, gal["id"], rutas, verificar)
    cx.commit()
    return redirect(url_for("panel_fotos", slug=slug, puestas=puestas,
                            repes=repes or None, faltan=len(faltan) or None))


@app.get("/panel/<slug>/descargables")
def descargables(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    fotos = db().execute(
        "SELECT id, descargable FROM foto WHERE galeria_id = ? ORDER BY orden, id",
        (gal["id"],)).fetchall()
    return render_template_string(BASE_HTML, cuerpo=render_template_string(
        PANEL_DESCARGABLES, gal=gal, fotos=fotos))


@app.post("/panel/<slug>/descargables")
def guardar_descargables(slug):
    gal = db().execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    si = [int(x) for x in request.form.getlist("si") if x.isdigit()]
    cx = db()
    cx.execute("UPDATE foto SET descargable = 0 WHERE galeria_id = ?", (gal["id"],))
    if si:
        # el AND galeria_id es a proposito: un id de otra galeria en el
        # formulario no tiene que poder tocarla
        cx.executemany("UPDATE foto SET descargable = 1 WHERE id = ? AND galeria_id = ?",
                       [(i, gal["id"]) for i in si])
    cx.commit()
    return redirect(url_for("panel", slug=slug))


@app.get("/panel/<slug>/seleccion.csv")
def seleccion_csv(slug):
    gal = db().execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    filas = db().execute(
        "SELECT foto.ruta, foto.orden, lista.nombre AS lista, COUNT(*) AS votos, "
        "  GROUP_CONCAT(cliente.email, ';') AS emails "
        "FROM lista_foto JOIN foto ON foto.id = lista_foto.foto_id "
        "JOIN lista ON lista.id = lista_foto.lista_id "
        "JOIN cliente ON cliente.id = lista.cliente_id "
        "WHERE foto.galeria_id = ? GROUP BY foto.id, lista.nombre "
        "ORDER BY lista.nombre, votos DESC, foto.orden", (gal["id"],)).fetchall()

    # el modulo csv y no un join a mano: las rutas y los emails traen comas
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["lista", "ruta", "nombre", "votos", "emails"])
    for f in filas:
        w.writerow([f["lista"], f["ruta"], os.path.basename(f["ruta"]),
                    f["votos"], f["emails"]])
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
            "  (SELECT COUNT(*) FROM cliente WHERE galeria_id = galeria.id) AS clientes, "
            "  (SELECT id FROM foto WHERE galeria_id = galeria.id "
            "    ORDER BY orden, id LIMIT 1) AS tapa "
            "FROM galeria ORDER BY creada DESC").fetchall()
        grupos = [(titulo, [g for g in gals
                            if (g["tipo"] if g["tipo"] in TIPOS else None) == t])
                  for t, titulo in TIPO_GRUPO]
        carpetas = cx.execute(
            "SELECT carpeta.*, (SELECT COUNT(*) FROM carpeta_galeria "
            "  WHERE carpeta_id = carpeta.id) AS n FROM carpeta "
            "ORDER BY creada DESC").fetchall()
        return render_template_string(BASE_HTML, cuerpo=render_template_string(
            PANEL_INDICE, gals=gals, grupos=[x for x in grupos if x[1]],
            carpetas=carpetas, estado=estado,
            hoy=datetime.now(timezone.utc).date().isoformat()))

    gal = cx.execute("SELECT * FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        abort(404)
    clientes = cx.execute(
        "SELECT cliente.*, "
        "  (SELECT COUNT(DISTINCT lista_foto.foto_id) FROM lista_foto "
        "     JOIN lista ON lista.id = lista_foto.lista_id "
        "    WHERE lista.cliente_id = cliente.id) AS marcadas, "
        "  (SELECT COUNT(*) FROM (SELECT lista_foto.foto_id FROM lista_foto "
        "     JOIN lista ON lista.id = lista_foto.lista_id "
        "    WHERE lista.cliente_id = cliente.id AND lista.nombre <> 'Carrito' "
        "    GROUP BY lista_foto.foto_id HAVING COUNT(DISTINCT lista.id) > 1)) AS repetidas, "
        "  (SELECT COUNT(*) FROM descarga "
        "    WHERE descarga.cliente_id = cliente.id) AS bajadas, "
        "  (SELECT COUNT(DISTINCT foto_id) FROM descarga "
        "    WHERE descarga.cliente_id = cliente.id) AS bajadas_fotos "
        "FROM cliente WHERE cliente.galeria_id = ? ORDER BY visto DESC",
        (gal["id"],)).fetchall()
    elegidas = cx.execute(
        "SELECT foto.id, foto.ruta, COUNT(DISTINCT cliente.id) AS votos, "
        "  GROUP_CONCAT(DISTINCT cliente.email) AS quienes, "
        "  GROUP_CONCAT(DISTINCT lista.nombre) AS listas "
        "FROM lista_foto JOIN foto ON foto.id = lista_foto.foto_id "
        "JOIN lista ON lista.id = lista_foto.lista_id "
        "JOIN cliente ON cliente.id = lista.cliente_id "
        "WHERE foto.galeria_id = ? GROUP BY foto.id "
        "ORDER BY votos DESC, foto.orden",
        (gal["id"],)).fetchall()
    # una fila por bajada: agrupando por foto se pierde quien bajo cada copia
    bajadas = cx.execute(
        "SELECT foto.id, foto.ruta, descarga.cuando, descarga.cual, "
        "  COALESCE(cliente.email, 'sin identificar') AS quien "
        "FROM descarga JOIN foto ON foto.id = descarga.foto_id "
        "LEFT JOIN cliente ON cliente.id = descarga.cliente_id "
        "WHERE foto.galeria_id = ? ORDER BY descarga.cuando DESC",
        (gal["id"],)).fetchall()
    listas = cx.execute(
        "SELECT lista.id, lista.cliente_id, lista.nombre, "
        "  (SELECT COUNT(*) FROM lista_foto WHERE lista_id = lista.id) AS n "
        "FROM lista JOIN cliente ON cliente.id = lista.cliente_id "
        "WHERE cliente.galeria_id = ? ORDER BY lista.orden, lista.id",
        (gal["id"],)).fetchall()
    por_cliente = {}
    for l in listas:
        por_cliente.setdefault(l["cliente_id"], []).append(l)
    cuenta = cx.execute(
        "SELECT COUNT(*) AS total, COUNT(raw) AS con_raw, "
        "  SUM(descargable) AS descargables FROM foto WHERE galeria_id = ?",
        (gal["id"],)).fetchone()
    calentadas, _total_cal = calentadas_de(cx, gal["id"])
    # ponytail: solo las primeras 24 para elegir tapa. Una fecha son 3000 fotos
    # y nadie elige la portada en la numero 1800.
    primeras = cx.execute(
        "SELECT id FROM foto WHERE galeria_id = ? ORDER BY orden, id LIMIT 24",
        (gal["id"],)).fetchall()
    # cuatro repartidas a lo largo de la galeria: la primera y la ultima
    # suelen ser del calentamiento, y en el medio esta el partido de verdad
    muestras = []
    for k in range(4):
        off = (cuenta["total"] - 1) * k // 3 if cuenta["total"] > 1 else 0
        fila = cx.execute("SELECT id, ruta FROM foto WHERE galeria_id = ? "
                          "ORDER BY orden, id LIMIT 1 OFFSET ?",
                          (gal["id"], off)).fetchone()
        if fila is not None and fila["id"] not in [m["id"] for m in muestras]:
            muestras.append(fila)
    return render_template_string(
        BASE_HTML, cuerpo=render_template_string(
            PANEL_GAL, gal=gal, clientes=clientes, elegidas=elegidas,
            miradas=cobertura(cx, gal["id"]),
            muestras_firma=muestras,
            estado=estado,
            tarifas=tramos(gal), pesos=pesos, precios_default=PRECIOS,
            tipos=TIPOS, tipo_nombre=TIPO_NOMBRE, ediciones=EDICIONES,
            sugeridas=LISTAS_SUGERIDAS,
            entf={c: entrega_a_form(c, gal[c]) for c in ENTREGA_COLS},
            resoluciones=RESOLUCIONES, metodos=METODOS, oculto=OCULTO,
            ed_items=lineas(textos().get("edicion_%s" % gal["ent_edicion"], ""))
            if gal["ent_edicion"] in EDICIONES else [],
            ed_quita=set(lineas(gal["ent_quita"])),
            T=textos(),
            todos_logos=logos_disponibles(), puestos=logos_de(gal),
            max_logos=MAX_LOGOS, combos=combos_logos(),
            posiciones=POSICIONES, firma_pos=FIRMA_POS, firma_tam=FIRMA_TAM,
            bajadas=bajadas, listas_de=por_cliente, marcas=MARCAS,
            total=cuenta["total"], con_raw=cuenta["con_raw"],
            descargables=cuenta["descargables"] or 0,
            calentadas=calentadas,
            corriendo=_calentando(gal["slug"]) or en_notebook(gal["slug"]),
            donde="la notebook" if en_notebook(gal["slug"]) else "la Pi",
            primeras=primeras, hoy=datetime.now(timezone.utc).date().isoformat(),
            nombre=os.path.basename))


# ---------------------------------------------------------------------- HTML

PIXIE = """<style>
/* el hueco de la barra fija lo reserva el CSS, no el JS: cuando lo ponia el
   JS al terminar de cargar, la pagina se movia hacia abajo (CLS 0,133). El JS
   sigue para el caso raro de que la barra mida otra cosa. Esto vive aca y no
   en el estilo compartido: la vidriera no tiene barra fija. */
:root{--barra:91px}
body{padding-top:var(--barra)}
@media (max-width:640px){:root{--barra:73px}}
</style>
<script>
// la barra es position:fixed, asi que no ocupa lugar: el hueco lo ponemos
// nosotros, medido, y se recalcula si cambia de alto (girar el telefono,
// titulo que pasa a dos lineas)
document.addEventListener("DOMContentLoaded", () => {
  const cabFija = document.querySelector("header.pcab");
  if (!cabFija) return;
  const ajustarCab = () => {
    // solo si no coincide con lo que reservo el CSS: asignar siempre
    // provocaba un salto de diseño en cada carga
    const alto = cabFija.offsetHeight;
    if (Math.abs(alto - parseFloat(getComputedStyle(document.body).paddingTop)) > 1)
      document.body.style.paddingTop = alto + "px";
  };
  ajustarCab();
  addEventListener("resize", ajustarCab);
  if (window.ResizeObserver) new ResizeObserver(ajustarCab).observe(cabFija);
});
</script>
<style>
/* Estructura y tipografia tipo Pixieset, sobre la paleta oscura de siempre.
   Va aca y no en BASE_HTML porque ese bloque lo comparte el panel. */
main{padding:0;max-width:none}

header{padding:1.4rem 2.2rem;align-items:center;gap:1.2rem;
  border-bottom:1px solid var(--linea);background:rgba(9,12,10,.92)}
header .tit{display:flex;flex-direction:column;gap:.3rem;min-width:0}
header .tit b{font:400 1.1rem/1 Georgia,"Times New Roman",serif;
  letter-spacing:.22em;text-transform:uppercase;color:#f2f2f2}
header .tit span{font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;
  color:#b4b4b4}
header .tit span a{color:#b4b4b4;text-decoration:none}
header .tit span a:hover{color:#fff;text-decoration:underline}
header .tit span b{font:inherit;letter-spacing:inherit;color:#fff}

#blistas{margin-left:auto;background:transparent;border:1px solid #3a3a3a;
  border-radius:0;padding:.5rem 1.1rem;font-size:.68rem;letter-spacing:.18em;
  text-transform:uppercase}
#plistas{right:2.2rem;border-radius:0}
.celda .ncom{position:absolute;left:.4rem;top:.4rem;background:#000a;
  border-radius:1rem;padding:.1rem .5rem;font-size:.75rem}
/* arriba del visor (z-index 50): con 40 el cajon se abria detras del fondo
   negro y parecia que el boton no hacia nada */
#pcom{position:fixed;left:50%;transform:translateX(-50%);bottom:5.2rem;z-index:60;
  width:min(30rem,92vw);max-height:60vh;overflow:auto;background:#1b1b1b;
  border:1px solid #333;border-radius:8px;padding:.8rem}
#pcom .uno{padding:.45rem 0;border-bottom:1px solid #2a2a2a}
#pcom .uno:last-of-type{border-bottom:0}
#pcom textarea{width:100%;background:#111;color:#eee;border:1px solid #333;
  border-radius:6px;padding:.5rem;font:inherit;resize:vertical;min-height:3.5rem}
#bnueva{border-radius:0;font-size:.68rem;letter-spacing:.16em;
  text-transform:uppercase;padding:.7rem}

/* grilla mas ancha, junta y sin esquinas redondeadas */
.grid{gap:.4rem;padding:.4rem;
  grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.celda{border-radius:0;background:#171717}
#fin{font-size:.7rem;letter-spacing:.16em;text-transform:uppercase}

/* portada a sangre, con la grilla abajo en la misma pagina */
/* z-index:auto es obligatorio: BASE_HTML define .tapa como el fondo fijo del
   muro de email (position:fixed; z-index:-1) y aca solo se pisaba `position`.
   El -1 sobrevivia y pintaba toda la portada DEBAJO del fondo de <main>, asi
   que el boton "Ver galeria" no recibia el click: elementFromPoint sobre el
   devolvia <main>. Se veia bien porque <main> es transparente. */
.tapa{position:relative;height:100vh;height:100svh;min-height:26rem;display:grid;
  place-items:center;text-align:center;color:#fff;overflow:hidden}
.tapa img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.tapa::after{content:"";position:absolute;inset:0;background:rgba(0,0,0,.34)}
.tapa .cen{position:relative;z-index:2;padding:2rem}
.tapa h1{margin:0;font:400 clamp(2rem,6vw,4.4rem)/1.05 Georgia,"Times New Roman",serif;
  letter-spacing:.13em;text-transform:uppercase;
  text-shadow:0 2px 26px rgba(0,0,0,.5)}
.tapa .fecha{margin:1.3rem 0 2.3rem;font-size:.76rem;letter-spacing:.28em;
  text-transform:uppercase;opacity:.9}
.tapa .entrar{display:inline-block;border:1px solid rgba(255,255,255,.8);
  color:#fff;text-decoration:none;padding:.85rem 2.5rem;font-size:.7rem;
  letter-spacing:.24em;text-transform:uppercase;transition:.18s}
.tapa .entrar:hover{background:#fff;color:var(--noche)}
.tapa .marca{position:absolute;bottom:2.2rem;left:0;right:0;z-index:2;
  font-size:.66rem;letter-spacing:.3em;text-transform:uppercase;opacity:.8}

/* el muro de correo, como el modal de Pixieset pero oscuro */
form.gate{max-width:27rem;margin:15vh auto;padding:3rem 2.6rem;background:#181818;
  border:1px solid #2a2a2a;box-shadow:0 18px 50px rgba(0,0,0,.5)}
form.gate h2{margin:0 0 1.1rem;font:400 1.05rem/1.3 Georgia,"Times New Roman",serif;
  letter-spacing:.24em;text-transform:uppercase;color:#f2f2f2}
/* #8d8d8d sobre el #181818 del recuadro daba 4,0 y el minimo es 4,5 */
form.gate p{color:#a9a9a9;font-size:.92rem;margin:0 0 1.6rem}
form.gate input{border:1px solid #333;border-radius:0;background:var(--noche);
  color:#eee;padding:.9rem 1rem}
form.gate input::placeholder{color:#5f5f5f}
form.gate .btn{border-radius:0;width:100%;padding:.9rem;font-weight:400;
  font-size:.7rem;letter-spacing:.22em;text-transform:uppercase}

/* precios del evento, entre la tapa y las fotos */
#abajo{scroll-margin-top:6rem}   /* el header mide 5.4rem: 5 no alcanzaba */
.tarifas{padding:3.6rem 1.5rem;border-bottom:1px solid var(--linea);
  text-align:center}
.tarifas .rot{margin:0 0 1.7rem;font-family:var(--mono);font-size:.68rem;
  letter-spacing:.24em;text-transform:uppercase;color:var(--humo)}
.packs{display:flex;gap:.5rem;justify-content:center;flex-wrap:wrap;margin:0}
.packs div{border:1px solid var(--linea2);padding:1.1rem 1.7rem;min-width:8.6rem}
.packs b{display:block;font:400 1.9rem/1 Georgia,"Times New Roman",serif;color:#fff}
.packs span{display:block;margin:.3rem 0 .75rem;font-family:var(--mono);
  font-size:.6rem;letter-spacing:.18em;text-transform:uppercase;color:var(--humo)}
.packs em{font-style:normal;font-weight:600;font-size:1.05rem;color:var(--cancha)}
.tarifas .nota{margin:1.8rem auto 0;max-width:48ch;color:var(--humo);font-size:.93rem}
.tarifas .nota a{color:var(--cancha);white-space:nowrap}
.tarifas.entrega dd{white-space:pre-line}
.quehace{list-style:none;margin:.5rem 0 0;padding:0;display:grid;gap:.2rem;
  text-align:left}
.quehace li{position:relative;padding-left:.9rem;font-size:.88rem;opacity:.75}
.quehace li::before{content:"\\2014";position:absolute;left:0;opacity:.45}
.ordenar form{display:flex;gap:1rem;justify-content:center;align-items:center;
  flex-wrap:wrap;margin:0}
.ordenar label{display:flex;gap:.5rem;align-items:center;font-family:var(--mono);
  font-size:.68rem;letter-spacing:.18em;text-transform:uppercase;color:var(--humo)}
.ordenar select{background:#141414;border:1px solid var(--linea2);color:var(--cal);
  border-radius:6px;padding:.45rem .7rem;font:inherit;font-size:.8rem;
  letter-spacing:.06em;text-transform:none}
.partidos{display:flex;gap:.45rem;justify-content:center;flex-wrap:wrap;
  margin:1.1rem 0 0}
.partidos a{display:inline-flex;align-items:center;gap:.4rem;text-decoration:none;
  border:1px solid var(--linea2);border-radius:999px;padding:.35rem .8rem;
  font-family:var(--mono);font-size:.7rem;letter-spacing:.1em;color:var(--humo)}
.partidos a:hover{border-color:var(--cancha)}
.partidos a.on{color:#0b0f09;background:var(--cancha);border-color:var(--cancha)}
.partidos a b{font-weight:600;opacity:.65}
.partidos i{width:.65rem;height:.65rem;border-radius:50%;display:inline-block;
  border:1px solid rgba(255,255,255,.25)}
.franja{display:flex;gap:.7rem;align-items:center;flex-wrap:nowrap}
.franja output{font-family:var(--mono);font-size:.78rem;color:var(--cal);
  letter-spacing:.04em;min-width:3.1rem;display:inline-block;text-align:center}
/* un solo riel con dos pivotes: dos range superpuestos, transparentes, y los
   pivotes son lo unico que recibe el mouse */
.dual{position:relative;width:11rem;height:1.5rem;flex:none}
.dual .pista,.dual .activo{position:absolute;top:50%;height:3px;
  transform:translateY(-50%);border-radius:2px}
.dual .pista{left:0;right:0;background:var(--linea2)}
.dual .activo{background:var(--cancha)}
.dual input[type=range]{position:absolute;left:0;top:0;width:100%;height:1.5rem;
  margin:0;background:none;pointer-events:none;-webkit-appearance:none;
  appearance:none}
.dual input[type=range]:focus{outline:none}
.dual input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;
  pointer-events:auto;width:.95rem;height:.95rem;border-radius:50%;
  background:var(--cancha);border:2px solid #0b0f09;cursor:grab}
.dual input[type=range]::-moz-range-thumb{pointer-events:auto;width:.95rem;
  height:.95rem;border-radius:50%;background:var(--cancha);border:2px solid #0b0f09;
  cursor:grab}
.dual input[type=range]::-moz-range-track{background:none}
.tarifas.libre .grande{margin:0 auto;max-width:42ch;color:#fff;
  font:400 clamp(1.15rem,2.6vw,1.6rem)/1.45 Georgia,"Times New Roman",serif}

/* que recibe el cliente: etiqueta a la izquierda en mono, valor a la derecha.
   En telefono la etiqueta se apila encima, que es lo unico que entra. */
.entrega dl{max-width:46rem;margin:0 auto;text-align:left}
.entrega dl div{display:grid;grid-template-columns:13rem 1fr;gap:.4rem 1.5rem;
  padding:.85rem 0;border-top:1px solid var(--linea)}
.entrega dl div:first-child{border-top:0}
.entrega dt{font-family:var(--mono);font-size:.66rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--humo);padding-top:.2rem}
.entrega dd{margin:0;color:#fff;font-size:.97rem}
.entrega dd.sola{grid-column:1/-1;color:var(--humo)}
@media (max-width:640px){
  .entrega dl div{grid-template-columns:1fr;gap:.25rem}
}
</style>"""

BASE_HTML = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{# indexar lo pide solo la vidriera publica (inicio, eventos, sobre mi). El
   default es noindex: galerias, gate, compra y panel no van a Google aunque
   alguien publique el link, y una pagina nueva nace oculta. #}
{% if indexar %}<link rel="canonical" href="{{ canonical }}">
{% else %}<meta name="robots" content="noindex,nofollow">{% endif %}
{# la tapa es el LCP: anunciada aca el navegador la pide antes de parsear el
   <style>, que son ~10 KB que si no van primero #}
{# type=webp: un navegador sin WebP la saltea en vez de bajar algo que no
   va a usar; los demas eligen igual que el <source> de la tapa #}
{% if precarga %}<link rel="preload" as="image" type="image/webp"
  href="{{ precarga }}&amp;f=webp" imagesrcset="{{ srcset(precarga, webp=True) }}"
  imagesizes="100vw" fetchpriority="high">{% endif %}
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
{# lo que muestra WhatsApp (y cualquier chat) al pegar el link. Sin esto solo
   ve el <title> y el dominio, y todos los links se ven iguales. #}
<title>{{ titulo|default('rb.fotos') }}</title>
<meta property="og:title" content="{{ titulo|default('rb.fotos') }}">
<meta property="og:description" content="{{ desc|default('Fotografia deportiva y de eventos') }}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="rb.fotos">
{% if og_img %}<meta property="og:image" content="{{ og_img }}">
<meta name="twitter:card" content="summary_large_image">{% endif %}
<meta name="description" content="{{ desc|default('Fotografia deportiva y de eventos') }}">
<style>
:root{color-scheme:dark;
  /* Renzo fotografia de noche: cancha sintetica bajo focos. El fondo es ese
     negro con verde adentro, el acento es lo que devuelve el pasto cuando le
     pega la luz, y el calido solo vive dentro de los degradados (nunca como
     texto: no llega al contraste). */
  --noche:#090C0A; --panel:#10150F; --linea:#1E241B; --linea2:#303A2C;
  --cal:#F0F3EA; --humo:#8E948A; --cancha:#C6F04A; --foco:#FFCE7B;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"Cascadia Mono","SF Mono","Roboto Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--noche);color:#eee;font:16px/1.6 var(--sans)}
:focus-visible{outline:2px solid var(--cancha);outline-offset:3px}
@media (prefers-reduced-motion:reduce){
  *{transition-duration:.01ms!important;animation-duration:.01ms!important}}
header{padding:1.2rem 1.5rem;border-bottom:1px solid #262626;display:flex;
  align-items:baseline;gap:1rem;flex-wrap:wrap;
  /* fixed y no sticky: sticky se cae en navegadores viejos y en los embebidos
     (el de Instagram). El hueco lo reserva ajustarCab() midiendo la barra. */
  position:sticky;top:0;z-index:40;background:var(--noche);
  backdrop-filter:blur(6px)}
/* la barra de una galeria es fija de verdad (sticky se cae en los navegadores
   embebidos). Solo la llevan las paginas de galeria: en la vidriera el
   <header> es la portada a sangre y no debe moverse de su lugar. */
header.pcab{position:fixed;top:0;left:0;right:0;min-height:var(--barra)}
@media (max-width:640px){
  /* en un telefono la barra se comia 169 de 812 px (un quinto de la pantalla).
     Va con la clase a proposito: el estilo de la pagina publica define
     despues `header .tit` y, con la misma especificidad, gana el ultimo.
     Grilla de 2 filas: titulo + botones arriba, la linea chica abajo. */
  header.pcab{display:grid;grid-template-columns:minmax(0,1fr) auto auto;
    align-items:center;padding:.5rem .8rem;gap:.15rem .5rem}
  header.pcab .tit{display:contents}
  header.pcab .tit b{grid-column:1;grid-row:1;font:700 .95rem/1.3 var(--sans);
    letter-spacing:-.01em;text-transform:none}
  header.pcab .tit span{grid-column:1/-1;grid-row:2;font-size:.7rem;line-height:1.2}
  header.pcab .tit b,header.pcab .tit span{overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;min-width:0}
  #blistas{grid-column:2;grid-row:1;margin-left:0;padding:.35rem .6rem;font-size:.82rem}
  .bcomprar{grid-column:3;grid-row:1;padding:.35rem .7rem;font-size:.82rem}
  #plistas{right:.6rem;left:.6rem;width:auto}
}
}
#blistas{margin-left:auto;background:#1f1f1f;border:1px solid #3a3a3a;color:#eee;
  border-radius:7px;padding:.45rem .9rem;cursor:pointer;font-size:.9rem}
#blistas:hover{background:#2a2a2a}
#plistas{position:absolute;right:1.5rem;top:100%;margin-top:.4rem;width:19rem;
  background:#1a1a1a;border:1px solid #333;border-radius:9px;padding:.6rem;
  box-shadow:0 10px 30px rgba(0,0,0,.55);z-index:41}
#plistas .fila{display:flex;align-items:center;gap:.4rem;padding:.35rem .3rem;
  border-radius:6px}
#plistas .fila:hover{background:#232323}
#plistas .fila .nom{flex:1;display:flex;align-items:center;gap:.5rem;min-width:0;
  font-size:.92rem;text-decoration:none;color:#eee;padding:.15rem 0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#plistas .fila .nom:hover{color:#fff;text-decoration:underline}
#plistas .fila.fija .nom{font-weight:600}
#plistas .sep{height:1px;background:#2b2b2b;margin:.45rem .3rem}
#plistas .fila.abierta .nom{color:#8ab4ff}
#plistas .fila.abierta .nom::before{content:"\\25B8 ";color:#8ab4ff}
#plistas .ayuda{margin:.2rem .35rem .6rem;font-size:.74rem;line-height:1.45;
  color:#8a8a8a}
#plistas .n{opacity:.5;font-size:.78rem;margin-left:auto;padding-left:.5rem;
  font-variant-numeric:tabular-nums}
#plistas .mov{background:transparent;border:0;color:#888;cursor:pointer;
  padding:0 .2rem;font-size:.95rem}
#plistas .mov:disabled{opacity:.2;cursor:default}
#bnueva{width:100%;margin-top:.5rem;background:transparent;border:1px dashed #444;
  color:#8ab4ff;border-radius:7px;padding:.5rem;cursor:pointer;font-size:.9rem}
header b{font-size:1.15rem;letter-spacing:-.02em}
header span{opacity:.55;font-size:.9rem}
main{padding:1.5rem;max-width:1500px;margin:0 auto}
a{color:#8ab4ff}
.grid{display:grid;gap:.55rem;
  grid-template-columns:repeat(auto-fill,minmax(230px,1fr));
  /* filas de 8px: cada celda toma las que necesita su foto, asi una vertical
     se ve vertical y no recortada. El span lo calcula medir() al cargarla. */
  grid-auto-rows:8px;align-items:start}
.celda{position:relative;background:#1c1c1c;border-radius:7px;overflow:hidden;
  /* mientras no cargo, la celda ocupa lo que ocuparia una horizontal */
  aspect-ratio:3/2;grid-row-end:span 10;
  /* fuera de pantalla no se maqueta ni se pinta. Al volver a entrar se
     repinta desde el cache del navegador, sin pedirsela de nuevo a la Pi. */
  content-visibility:auto;contain-intrinsic-size:auto 230px auto 153px}
#fin{grid-column:1/-1;text-align:center;padding:1.4rem;opacity:.45;font-size:.9rem}
.celda.medida{aspect-ratio:auto;align-self:stretch}
.celda img{width:100%;height:100%;object-fit:cover;display:block}
.enls{position:absolute;left:.4rem;top:.4rem;background:rgba(0,0,0,.7);color:#fff;
  font-size:.75rem;padding:.2rem .5rem;border-radius:5px;pointer-events:none}
.aviso{position:fixed;left:50%;bottom:1.4rem;transform:translateX(-50%);z-index:80;
  background:#eee;color:#111;padding:.6rem 1.1rem;border-radius:999px;
  font-size:.95rem;box-shadow:0 6px 20px rgba(0,0,0,.5);max-width:90vw;
  text-align:center;pointer-events:none}
/* el menu de listas de una foto: por encima del visor */
.pop{position:fixed;z-index:70;background:#1c1c1c;border:1px solid #3a3a3a;
  border-radius:9px;padding:.55rem .8rem;min-width:13rem;max-width:90vw;
  box-shadow:0 10px 30px rgba(0,0,0,.6);color:#eee}
.pop .ptit{margin:0 0 .25rem;opacity:.55;font-size:.78rem;letter-spacing:.04em}
.pop label{display:flex;gap:.6rem;align-items:center;padding:.45rem 0;
  font-size:.95rem;cursor:pointer}
.pop input{width:1.15rem;height:1.15rem;accent-color:var(--cancha);margin:0}
.acc{position:absolute;right:.4rem;bottom:.4rem;display:flex;gap:.35rem;
  opacity:0;transition:opacity .15s}
.celda:hover .acc,.celda:focus-within .acc{opacity:1}
.acc a,.acc button{background:rgba(0,0,0,.62);border:0;color:#fff;cursor:pointer;
  width:2.1rem;height:2.1rem;border-radius:50%;font-size:1rem;display:grid;
  place-items:center;text-decoration:none;padding:0}
.acc .fav.on{background:#e0245e}
.acc .fav.on::after{content:"\\2665"}
.acc .fav::after{content:"\\2661"}
.acc .car::after{content:"+";font-size:1.25rem;line-height:1}
.acc .car.on{background:var(--cancha);color:#0b0f09}
.acc .car.on::after{content:"\\2713";font-size:1rem}
form.gate{max-width:26rem;margin:12vh auto;text-align:center}
form.gate input{width:100%;padding:.8rem 1rem;font-size:1rem;border-radius:8px;
  border:1px solid #3a3a3a;background:#1a1a1a;color:#eee;margin:1rem 0 .8rem}
form.gate button{width:100%;padding:.8rem;font-size:1rem;border:0;border-radius:8px;
  background:#eee;color:var(--noche);font-weight:600;cursor:pointer}
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
.btn{display:inline-block;background:#eee;color:var(--noche);border:0;border-radius:8px;
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
/* dejar apretada la flecha avanza solo: sin esto el telefono abre su propio
   menu de "guardar imagen" y se come la repeticion. Va solo en las flechas,
   la foto se sigue pudiendo apretar largo para guardarla */
#visor .ant,#visor .sig{position:absolute;top:50%;transform:translateY(-50%);
  font-size:2.2rem;line-height:1;padding:.2rem .75rem;
  -webkit-touch-callout:none;user-select:none;-webkit-user-select:none;
  touch-action:manipulation}
#visor .ant{left:.4rem}
#visor .sig{right:.4rem}
.vacc{position:absolute;bottom:1.1rem;left:50%;transform:translateX(-50%);
  display:flex;gap:.6rem;align-items:center}
#vlike::after{content:"\\2661"}
#vcar::after{content:"+";font-size:1.35rem;line-height:1}
#vcar.on{background:var(--cancha);color:#0b0f09}
#vcar.on::after{content:"\\2713";font-size:1.05rem}
#vlike.on{background:#e0245e}
#vlike.on::after{content:"\\2665"}
#vraw{font-size:.72rem;font-weight:700;letter-spacing:.04em}
.vpos{position:absolute;top:.9rem;left:1.1rem;opacity:.55;font-size:.9rem}
#visor.cargando .vpos::after{content:" · cargando..."}
/* el fondo del muro de email. Se llama .fondo y no .tapa porque PIXIE usa
   .tapa para la portada a sangre de la galeria: compartiendo el nombre, el
   z-index:-1 de aca se colaba alla y el boton "Ver galeria" no era clickeable. */
.fondo{position:fixed;inset:0;z-index:-1;overflow:hidden}
.fondo img{width:100%;height:100%;object-fit:cover;opacity:.45}
.fondo::after{content:"";position:absolute;inset:0;
  background:linear-gradient(rgba(17,17,17,.3),rgba(17,17,17,.92))}
.bajar{display:flex;gap:.6rem;flex-wrap:wrap;margin:0 0 1.1rem}
.tapas{display:flex;gap:.4rem;flex-wrap:wrap;margin:.5rem 0 1rem}
.tapas label{cursor:pointer;line-height:0}
.tapas input{position:absolute;opacity:0;pointer-events:none}
.tapas img,.tapas .nada{width:98px;height:66px;object-fit:cover;border-radius:7px;
  border:3px solid transparent;display:grid;place-items:center;background:#1c1c1c}
.tapas.logos img{object-fit:contain;background:#1a1a1a;padding:.3rem}
/* donde va la firma, dibujado como la foto: 3x3 y se marca la celda */
.matriz{display:inline-grid;grid-template-columns:repeat(3,1fr);gap:3px;
  width:6.6rem;vertical-align:middle;margin-left:.4rem;padding:3px;
  border:1px solid #2f2f2f;border-radius:5px;background:#141414}
.matriz label{display:block;position:relative;cursor:pointer;line-height:0}
.matriz input{position:absolute;opacity:0;pointer-events:none}
.matriz label > span{display:block;height:1.15rem;border:1px solid #333;
  border-radius:3px;transition:background .12s}
.matriz label:hover > span{border-color:#666}
.matriz input:checked ~ span{background:#eee;border-color:#eee}
.buscaid{display:flex;gap:.3rem;margin:0 .6rem 0 auto}
.buscaid input{background:#141414;border:1px solid #2f2f2f;border-radius:6px;
  color:#eee;font:inherit;font-size:.82rem;padding:.35rem .6rem;
  text-transform:uppercase;letter-spacing:.12em;width:8.5rem}
.buscaid button{background:transparent;border:1px solid #3a3a3a;border-radius:6px;
  color:#eee;font:inherit;font-size:.8rem;padding:.35rem .7rem;cursor:pointer}
.buscaid button:hover{border-color:#eee}
.firmabox{display:flex;gap:1.2rem;flex-wrap:wrap;align-items:flex-start;margin:.4rem 0}
.firmacfg{flex:1 1 21rem;margin:0}
.firmaprev{display:flex;gap:.4rem;flex-wrap:wrap}
.firmaprev img{width:11rem;height:7.4rem;object-fit:contain;background:#0d0d0d;
  border:1px solid #2f2f2f;border-radius:6px;display:block;cursor:zoom-in}
.firmaprev img[hidden]{display:none}
#noms{width:100%;max-width:44rem;background:#141414;border:1px solid #2f2f2f;
  border-radius:7px;color:#eee;font:inherit;font-family:var(--mono);
  font-size:.82rem;line-height:1.6;padding:.7rem .9rem;resize:vertical}
.combos{display:flex;gap:.5rem;flex-wrap:wrap;margin:0 0 .3rem}
.combo{display:flex;gap:.3rem;align-items:center;cursor:pointer;padding:.3rem;
  background:#141414;border:1px solid #2f2f2f;border-radius:7px}
.combo:hover{border-color:#eee}
.combo img{width:52px;height:36px;object-fit:contain;background:#1a1a1a;display:block}
.tapas .nada{font-size:.68rem;line-height:1.15;text-align:center;opacity:.55;color:#eee}
/* la pagina de descargables puede tener miles de miniaturas: lo que no se ve
   ni se maqueta ni se pinta, igual que la grilla del cliente (.celda) */
.tapas.descargables label{content-visibility:auto;
  contain-intrinsic-size:auto 98px auto 66px}
.tapas input:checked ~ img,.tapas input:checked ~ .nada{border-color:#eee}
.aviso{color:#ff8a9b;font-size:.9rem}
.elegir{cursor:pointer}
.elegir input{position:absolute;top:.4rem;left:.4rem;z-index:2;margin:0;
  width:1.15rem;height:1.15rem;accent-color:#ff8a9b}
.elegir input:checked ~ img{opacity:.4;outline:3px solid #ff8a9b;outline-offset:-3px}
.barrasel{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;
  position:sticky;top:0;z-index:30;background:#111;padding:.8rem 0;margin:.4rem 0}
.barrasel button[type=button]{background:transparent;border:1px solid #3a3a3a;
  color:#eee;border-radius:7px;padding:.35rem .8rem;cursor:pointer;font-size:.85rem}
.barrasel .cuantas{opacity:.5;font-size:.85rem;margin-left:auto}
@media (hover:none){.acc{opacity:1}}
/* ========== sitio publico: portada, eventos, sobre mi, calculadora, compra
   Una sola idea: la luz. La barra se cierra con una linea encendida, el hero
   tiene el foco fuera de cuadro arriba a la derecha y la oscuridad sube desde
   abajo, que es donde va el texto. Todo lo demas es plano y de a un pelo de
   borde. Dos voces tipograficas: system-ui para leer, y monoespaciada en
   mayusculas para los datos: la voz del marcador y la de la tira EXIF. */
.viz{max-width:1180px;margin:0 auto;padding:0 1.5rem}
.volver{color:inherit;text-decoration:none}
.rotulo{font-family:var(--mono);font-size:.7rem;letter-spacing:.2em;
  text-transform:uppercase;color:var(--humo);font-weight:500;margin:0 0 1.5rem;
  display:flex;align-items:center;gap:.7rem}
.rotulo::before{content:"";width:.42rem;height:.42rem;background:var(--cancha);
  flex:none}
.luz{height:1px;border:0;margin:7rem 0 0;
  background:linear-gradient(90deg,transparent,rgba(198,240,74,.5),transparent)}

/* -------------------------------------------------------------- barra fija */
.barra{position:sticky;top:0;z-index:60;display:grid;
  grid-template-columns:1fr auto 1fr;align-items:center;gap:1rem;
  padding:.85rem 1.5rem;background:rgba(9,12,10,.88);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--linea)}
.barra::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,rgba(198,240,74,.45),transparent);
  pointer-events:none}
.barra .logo{font:700 1.02rem/1 var(--sans);letter-spacing:-.04em;
  color:var(--cal);text-decoration:none}
.barra .logo span{color:var(--cancha)}
.barra .links{display:flex;gap:1.7rem;justify-content:center;min-width:0;
  overflow-x:auto;scrollbar-width:none}
.barra .links::-webkit-scrollbar{display:none}
.barra .links a{font-family:var(--mono);font-size:.71rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--humo);text-decoration:none;
  white-space:nowrap;padding:.3rem 0;border-bottom:2px solid transparent;
  transition:color .18s}
.barra .links a:hover{color:var(--cal)}
.barra .links a.on{color:var(--cal);border-bottom-color:var(--cancha)}
.barra .wsp{justify-self:end}
@media (max-width:640px){
  .barra{gap:.7rem;padding:.7rem 1rem}
  .barra .links{gap:1.15rem}
  .barra .wsp{display:none}
}

/* ------------------------------------------------------------------ botones */
.wsp{display:inline-flex;align-items:center;gap:.5rem;background:var(--cancha);
  color:var(--noche);text-decoration:none;font-family:var(--mono);
  font-size:.74rem;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  padding:.92rem 1.6rem;border-radius:2px;border:1px solid var(--cancha);
  transition:transform .18s,box-shadow .18s}
.wsp:hover{transform:translateY(-1px);box-shadow:0 8px 26px rgba(198,240,74,.2)}
.wsp.hueco{background:transparent;color:var(--cal);border-color:var(--linea2)}
.wsp.hueco:hover{border-color:var(--cal);box-shadow:none}
.wsp.chico{padding:.5rem 1.05rem;font-size:.66rem;letter-spacing:.15em}
.botones{display:flex;gap:.7rem;flex-wrap:wrap;margin:2.6rem 0 0;align-items:center}

/* ------------------------------------------------------- hero de la portada
   La foto de tapa es la PRIMERA de ~/galeria/portada/ por orden de nombre:
   para cambiarla, renombrala para que ordene antes. El encuadre se corre con
   object-position; 40% deja el horizonte alto, que es donde suele caer el
   sujeto en un plano de cancha. */
main.inicio{padding:0;max-width:none;margin:0}
/* foto() envuelve cada imagen en <picture>; asi no ocupa caja propia y el
   <img> se acomoda como antes (la tapa es absolute dentro de un grid) */
picture{display:contents}
.capa{position:relative;min-height:min(86vh,760px);display:grid;
  /* la columna va acotada: un track `auto` toma el max-content del
     titulo y en el telefono empuja la pagina fuera de la pantalla */
  grid-template-columns:minmax(0,1fr);align-content:end;
  overflow:hidden;background:var(--panel)}
.capa-foto{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  object-position:center 40%}
.capa::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:
    radial-gradient(115% 75% at 80% -8%,rgba(255,206,123,.2),transparent 58%),
    linear-gradient(180deg,rgba(9,12,10,.62) 0%,rgba(9,12,10,.18) 30%,
                    rgba(9,12,10,.9) 82%,var(--noche) 100%)}
.capa-txt{position:relative;z-index:2;align-self:end;width:100%;
  max-width:1180px;margin:0 auto;padding:0 1.5rem 3.6rem}
.capa h1{margin:0;max-width:15ch;font-size:clamp(2.5rem,7.4vw,5.1rem);
  font-weight:600;line-height:.98;letter-spacing:-.045em;color:var(--cal);
  text-shadow:0 2px 40px rgba(0,0,0,.55)}
.bajada{color:var(--cal);opacity:.72;margin:1.5rem 0 0;max-width:46ch;
  font-size:1.03rem}
@media (max-width:640px){
  .capa{min-height:76svh}
  .capa-txt{padding:0 1rem 2.6rem}
}

/* --------------------------------------------------------- tira de trabajos */
.tira{display:grid;grid-template-columns:repeat(6,1fr);gap:.5rem;
  max-width:1240px;margin:0 auto;padding:0 1.5rem}
.tira figure{margin:0;grid-column:span 2;aspect-ratio:3/2;overflow:hidden;
  background:var(--panel)}
.tira figure:first-child{grid-column:span 4;aspect-ratio:2/1}
.tira figure:nth-child(2){aspect-ratio:auto}
.tira img{width:100%;height:100%;object-fit:cover;display:block;
  filter:brightness(.94);transition:transform .55s ease,filter .35s ease}
.tira figure:hover img{transform:scale(1.035);filter:brightness(1.06)}
/* una sola foto a sangre a mitad de pagina: el respiro entre el trabajo y
   como se entrega. Va fuera de .viz a proposito, para que corte de borde a
   borde. */
.banda{position:relative;height:min(58vh,520px);overflow:hidden;
  margin:6.5rem 0 0;background:var(--panel)}
.banda img{width:100%;height:100%;object-fit:cover;display:block;
  object-position:center 76%}
@media (max-width:640px){.banda{height:44svh;margin-top:4.2rem}}
@media (max-width:880px){
  .tira{grid-template-columns:repeat(4,1fr)}
  .tira figure:first-child{grid-column:span 4}
  .tira figure:nth-child(2){aspect-ratio:3/2}
}
@media (max-width:560px){
  .tira{grid-template-columns:1fr;padding:0 1rem}
  .tira figure,.tira figure:first-child{grid-column:auto;aspect-ratio:3/2}
}

/* ------------------------------------------------------------------ bloques */
.bloque{padding:6.5rem 0 0}
.tit{margin:0;font-size:clamp(1.7rem,4.4vw,2.5rem);font-weight:600;
  letter-spacing:-.03em;color:var(--cal)}
@media (max-width:640px){.bloque{padding:4.2rem 0 0}}

.evgrid{display:grid;gap:1rem;margin:2rem 0 0;
  grid-template-columns:repeat(auto-fill,minmax(258px,1fr))}
.evcard{display:block;text-decoration:none;color:var(--cal);background:var(--panel);
  border:1px solid var(--linea);overflow:hidden;transition:border-color .2s}
.evcard:hover{border-color:var(--cancha)}
.evcard img{width:100%;aspect-ratio:3/2;object-fit:cover;display:block;
  background:#141812;filter:brightness(.86);transition:filter .3s}
.evcard:hover img{filter:brightness(1)}
.evcard .txt{padding:1rem 1.1rem 1.15rem;border-top:1px solid var(--linea)}
.evcard .nom{display:block;font-weight:600;font-size:1rem;letter-spacing:-.012em;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.evcard .cuando{display:block;margin-top:.45rem;font-family:var(--mono);
  font-size:.7rem;letter-spacing:.09em;color:var(--humo)}
.tags{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.85rem}
.tags i{font-style:normal;font-family:var(--mono);font-size:.6rem;
  letter-spacing:.14em;text-transform:uppercase;color:var(--cancha);
  border:1px solid rgba(198,240,74,.28);padding:.2rem .55rem}
/* los tramos de precio en la tarjeta del evento */
.tarifa{display:block;margin-top:.9rem;font-family:var(--mono);font-size:.68rem;
  letter-spacing:.05em;color:var(--humo)}
.tarifa span{display:flex;justify-content:space-between;gap:.6rem;
  border-top:1px solid var(--linea);padding:.28rem 0}
.tarifa b{font-weight:600;color:var(--cal)}
.tarifa em{font-style:normal;color:var(--cancha)}
.tarifa.libre{color:var(--cancha);border-top:1px solid var(--linea);padding-top:.5rem}
.mas{display:inline-flex;align-items:center;gap:.5rem;margin-top:1.8rem;
  font-family:var(--mono);font-size:.72rem;letter-spacing:.15em;
  text-transform:uppercase;color:var(--cal);text-decoration:none;
  border-bottom:1px solid var(--linea2);padding-bottom:.3rem}
.mas:hover{border-bottom-color:var(--cancha)}
.vacio{color:var(--humo);padding:2.5rem 0;max-width:46ch}
.sello{display:inline-block;font-family:var(--mono);font-size:.66rem;
  letter-spacing:.18em;text-transform:uppercase;color:var(--cancha);
  border:1px solid rgba(198,240,74,.3);padding:.4rem .9rem}

/* Los tres pasos son una secuencia de verdad y en orden, por eso van
   numerados; el numero usa la mono y el verde de la cancha, que es el mismo
   idioma que un dorsal. */
.como{display:grid;gap:2.8rem 2.2rem;grid-template-columns:repeat(3,1fr);
  counter-reset:paso;margin-top:2.6rem}
.como h2{grid-column:1/-1;margin:0 0 -.9rem;font-size:clamp(1.7rem,4.4vw,2.5rem);
  font-weight:600;letter-spacing:-.03em}
.paso{counter-increment:paso;position:relative;padding-left:3.6rem}
.paso::before{content:counter(paso,decimal-leading-zero);position:absolute;
  left:0;top:-.35rem;font-family:var(--mono);font-size:1.75rem;font-weight:700;
  letter-spacing:-.03em;color:var(--cancha)}
.paso b{display:block;font-size:1.02rem;font-weight:600;color:var(--cal);
  margin-bottom:.5rem;letter-spacing:-.01em}
.paso p{margin:0;color:var(--humo);font-size:.95rem}
@media (max-width:760px){
  .como{grid-template-columns:1fr;gap:2rem}
  .paso{padding-left:3.2rem}
}

.mi{display:grid;gap:2.5rem 3rem;grid-template-columns:minmax(0,17rem) 1fr;
  align-items:start;max-width:58rem;margin-top:2.4rem}
.mi h2{margin:0;font-size:clamp(1.7rem,4.4vw,2.4rem);font-weight:600;
  letter-spacing:-.03em}
.mi p{color:var(--humo);margin:0 0 1.1rem;font-size:1rem}
.mi p:last-child{margin:0}
@media (max-width:760px){.mi{grid-template-columns:1fr;gap:1.5rem}}

.pie{padding:2.4rem 0 4rem;
  display:flex;gap:1.6rem;flex-wrap:wrap;align-items:center;
  font-family:var(--mono);font-size:.68rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--humo)}
.pie a{color:var(--humo);text-decoration:none;border-bottom:1px solid transparent}
.pie a:hover{color:var(--cal);border-bottom-color:var(--cancha)}
.pie .fin{margin-left:auto;text-align:right;letter-spacing:.1em}
@media (max-width:640px){.pie{gap:1rem}.luz{margin-top:4.5rem}
  .pie .fin{margin-left:0}}

/* -------------------------------------------- compra (dentro de la galeria)
   Mismo idioma que el resto del sitio publico; el markup no se toco. */
.bcomprar{background:var(--cancha);color:var(--noche);text-decoration:none;
  border-radius:2px;padding:.5rem 1rem;font-family:var(--mono);font-size:.68rem;
  font-weight:700;letter-spacing:.13em;text-transform:uppercase;white-space:nowrap}
.bcomprar:hover{color:var(--noche)}
.compra2{display:grid;gap:2rem;align-items:start;
  grid-template-columns:minmax(0,1.15fr) minmax(21rem,.85fr);
  padding-top:2.5rem;padding-bottom:5rem;max-width:72rem}
@media (max-width:860px){
  .compra2{grid-template-columns:1fr;gap:1.4rem}
  .compra2 .cofotos{order:2}
}
.compra2 .grid.mini{grid-template-columns:repeat(auto-fill,minmax(112px,1fr));
  gap:.35rem;padding:0}
.compra2 .copedido{position:sticky;top:5rem}
.idcompra{margin:0;font-family:var(--mono);font-size:1.5rem;letter-spacing:.28em;
  color:var(--cancha)}
.compra{max-width:38rem;padding-top:3rem;padding-bottom:5rem}
.quecompro{display:flex;gap:.45rem;flex-wrap:wrap;margin:1.1rem 0 0}
.quecompro a{font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;
  text-transform:uppercase;text-decoration:none;color:var(--humo);
  border:1px solid var(--linea2);padding:.4rem .8rem}
.quecompro a.on{color:#0b0f09;background:var(--cancha);border-color:var(--cancha)}
.compra .grande{font-size:clamp(2rem,6vw,2.8rem);font-weight:600;
  letter-spacing:-.035em;margin:.6rem 0 .35rem;color:var(--cal)}
.compra .grande small{display:block;margin-top:.4rem;font-family:var(--mono);
  font-size:.95rem;font-weight:700;letter-spacing:.04em;color:var(--cancha)}
.cuenta{background:var(--panel);border:1px solid var(--linea);
  padding:1.1rem 1.4rem;margin:1.8rem 0 0}
.cuenta table{margin:0}
.cuenta td{border:0;padding:.35rem 0}
.cuenta td:first-child{width:9.5rem;font-family:var(--mono);font-size:.68rem;
  letter-spacing:.13em;text-transform:uppercase;color:var(--humo)}
.cuenta td:last-child{font-weight:600}
.pasos3{list-style:none;padding:0;margin:1.8rem 0 0;counter-reset:p}
.pasos3 li{counter-increment:p;display:flex;gap:.9rem;padding:.45rem 0;
  color:var(--humo);font-size:.95rem;align-items:flex-start}
.pasos3 li::before{content:counter(p,decimal-leading-zero);flex:none;
  font-family:var(--mono);font-size:.82rem;font-weight:700;color:var(--cancha);
  padding-top:.16rem}
.campo{display:block;margin:.9rem 0}
.campo span{display:block;font-family:var(--mono);font-size:.66rem;
  letter-spacing:.14em;text-transform:uppercase;color:var(--humo);
  margin-bottom:.35rem}
.campo input,.campo textarea{width:100%;background:var(--panel);
  border:1px solid var(--linea2);border-radius:2px;color:#eee;font:inherit;
  font-size:.92rem;padding:.6rem .8rem;resize:vertical}
.campo textarea{line-height:1.5}
</style>{{ cuerpo|safe }}</html>"""

BARRA = """<nav class="barra">
<a class="logo" href="/">rb<span>.</span>fotos</a>
<div class="links">
<a href="/" class="{{ 'on' if seccion == 'inicio' }}">Inicio</a>
<a href="/eventos" class="{{ 'on' if seccion == 'eventos' }}">Eventos</a>
<a href="/calculadora" class="{{ 'on' if seccion == 'calculadora' }}">Calculadora</a>
<a href="/sobre-mi" class="{{ 'on' if seccion == 'sobre-mi' }}">Sobre mi</a>
</div>
{% if wsp %}<a class="wsp chico" href="https://wa.me/{{ wsp }}"
  rel="noopener">WhatsApp</a>{% endif %}
</nav>
"""

PIE = """<hr class="luz">
<footer class="pie"><span>rb.fotos</span>
<a href="/eventos">Eventos</a><a href="/calculadora">Calculadora</a>
<a href="/sobre-mi">Sobre mi</a>
{% if wsp %}<a href="https://wa.me/{{ wsp }}" rel="noopener">WhatsApp</a>{% endif %}
{% if T.pie %}<span class="fin">{{ T.pie }}</span>{% endif %}</footer>"""

# La portada abre con una foto a sangre y el titulo metido en la parte oscura
# del cuadro: es lo que una foto de noche te deja hacer y una de dia no. La
# tapa es fotos[0] y la tira de abajo son las demas, para no repetirla.
# la tarjeta de evento: portada, /eventos y las carpetas. La tarifa solo si
# esta en venta: en una carpeta tambien hay galerias que no se venden.
TARJETAS = """{% for g in eventos %}<a class="evcard" href="/g/{{ g['slug'] }}">
{{ foto(url_tapa(g), "(max-width:640px) 100vw, 290px", loading="lazy", alt="") }}
<div class="txt"><span class="nom">{{ g['nombre'] }}</span>
<span class="cuando">{{ fecha_corta(g['fecha'] or g['creada']) }}
  &middot; {{ g['fotos'] }} fotos</span>
<span class="tags">{% if g['descargas'] %}<i>Descarga</i>{% endif %}
{% if g['clave_hash'] %}<i>Con clave</i>{% endif %}</span>
{% if g['venta'] and not g['descargas'] %}<span class="tarifa">{% for c, p in tramos(g) %}
<span><b>{{ c }}</b> fotos <em>{{ pesos(p) }}</em></span>{% endfor %}</span>{% endif %}
</div></a>
{% endfor %}"""

PORTADA = BARRA + """<main class="inicio"><header class="capa">
{% if fotos %}{{ foto(url_muestra(fotos[0]), "100vw", clase="capa-foto", alt="",
                       fetchpriority="high") }}{% endif %}
<div class="capa-txt">
<h1>{{ T.hero_titulo }}</h1>
<p class="bajada">{{ T.hero_bajada }}</p>
<p class="botones">
{% if wsp and T.hero_boton_wsp %}<a class="wsp" href="https://wa.me/{{ wsp }}"
  rel="noopener">{{ T.hero_boton_wsp }}</a>{% endif %}
{% if eventos and T.hero_boton_ev %}<a class="wsp hueco"
  href="/eventos">{{ T.hero_boton_ev }}</a>{% endif %}
{% if T.hero_boton_mi %}<a class="wsp hueco"
  href="/sobre-mi">{{ T.hero_boton_mi }}</a>{% endif %}
</p>
</div>
</header>

{% if eventos %}<section class="viz bloque" id="eventos">
<p class="rotulo">{{ T.rotulo_eventos }}</p>
<div class="evgrid">
""" + TARJETAS + """</div>
{% if T.rotulo_eventos_mas %}<a class="mas"
  href="/eventos">{{ T.rotulo_eventos_mas }} &rarr;</a>{% endif %}
</section>{% endif %}

{# las fotos se reparten a lo largo de la pagina, no en un solo bloque:
   fotos[0] es la tapa, las 6 siguientes la tira de accion, la que viene
   despues la banda a sangre, y el resto cierra abajo de los pasos. #}
{% set resto = fotos[1:] %}
{% if resto %}<section class="bloque" id="trabajos">
<div class="viz"><p class="rotulo">{{ T.rotulo_trabajos }}</p></div>
<div class="tira">
{% for f in resto[:5] %}<figure>{{ foto(url_muestra(f), '(max-width:880px) 100vw, 792px' if loop.first
            else '(max-width:560px) 100vw, (max-width:880px) 50vw, 392px',
  alt="", loading="lazy", draggable="false") }}</figure>
{% endfor %}</div></section>{% endif %}

{% if resto|length > 5 %}<div class="banda">
{{ foto(url_muestra(resto[5]), "100vw", alt="", loading="lazy",
         draggable="false") }}</div>{% endif %}

{% if T.como_titulo %}<section class="viz bloque">
<div class="como">
<h2>{{ T.como_titulo }}</h2>
{% for t, p in [(T.como1_t, T.como1_p), (T.como2_t, T.como2_p),
                (T.como3_t, T.como3_p)] %}{% if t %}
<div class="paso"><b>{{ t }}</b><p>{{ p }}</p></div>{% endif %}{% endfor %}
</div></section>{% endif %}

{% if resto|length > 6 %}<section class="bloque">
<div class="tira">
{% for f in resto[6:] %}<figure>{{ foto(url_muestra(f), '(max-width:880px) 100vw, 792px' if loop.first
            else '(max-width:560px) 100vw, (max-width:880px) 50vw, 392px',
  alt="", loading="lazy", draggable="false") }}</figure>
{% endfor %}</div></section>{% endif %}

</main>
<div class="viz">""" + PIE + """</div>"""

CALCULADORA = BARRA + """<main class="viz">
<section class="bloque" style="max-width:44rem">
{% if T.calc_sello %}<p style="margin:0 0 1.4rem">
<span class="sello">{{ T.calc_sello }}</span></p>{% endif %}
<h1 class="tit">{{ T.calc_titulo }}</h1>
<p class="bajada">{{ T.calc_texto }}</p>
<p class="botones">
{% if wsp %}<a class="wsp" href="https://wa.me/{{ wsp }}?text={{
  'Hola Renzo, quiero cotizar un evento'|urlencode }}"
  rel="noopener">Pedime la cotizacion</a>{% endif %}
<a class="wsp hueco" href="/eventos">Ver los eventos</a></p>
</section>
""" + PIE + """</main>"""

SOBREMI = BARRA + """<main class="viz">
<section class="bloque">
<div class="mi">
<h2>{{ T.mi_titulo }}</h2>
<div>{% for p in parrafos(T.mi_cuerpo) %}<p>{{ p }}</p>{% endfor %}</div>
</div>
<p class="botones">
{% if wsp and T.mi_boton %}<a class="wsp" href="https://wa.me/{{ wsp }}"
  rel="noopener">{{ T.mi_boton }}</a>{% endif %}
<a class="wsp hueco" href="/eventos">Ver los eventos</a></p>
</section>
""" + PIE + """</main>"""

EVENTOS = BARRA + """<main class="viz">
<section class="bloque">
{% if T.ev_rotulo %}<p class="rotulo">{{ T.ev_rotulo }}</p>{% endif %}
<h1 class="tit">{{ T.ev_titulo }}</h1>
<p class="bajada">{{ T.ev_bajada }}</p>
<div class="evgrid">
""" + TARJETAS + """</div>
{% if not eventos %}<p class="vacio">{{ T.ev_vacio }}</p>{% endif %}
</section>
""" + PIE + """</main>"""

CARPETA = BARRA + """<main class="viz">
<section class="bloque">
<h1 class="tit">{{ carpeta['nombre'] }}</h1>
<div class="evgrid">""" + TARJETAS + """</div>
{% if not eventos %}<p class="vacio">Todavia no hay nada en esta carpeta.</p>{% endif %}
</section>
""" + PIE + """</main>"""

COMPRAR = """<header><b><a class="volver" href="/g/{{ gal['slug'] }}">&larr;
{{ gal['nombre'] }}</a></b> <span>comprar</span></header>
<main class="viz compra2">

<section class="cofotos">
{% if fotos %}<div class="grid mini">{% for f in fotos %}<figure class="celda"
  style="margin:0"><img src="/p/{{ f['id'] }}" loading="lazy" alt=""
  draggable="false"></figure>{% endfor %}</div>
{% if n > fotos|length %}<p class="nota">Y {{ n - fotos|length }} mas.</p>{% endif %}
{% else %}<p class="vacio">Todavia no elegiste ninguna.</p>{% endif %}
</section>

<section class="copedido">
<p class="rotulo" style="margin:0">{{ elegida['nombre'] if elegida else 'Tu seleccion' }}</p>
{% if listas|length > 1 %}<p class="quecompro">{% for l in listas %}<a
  href="?lista={{ l['id'] }}" class="{{ 'on' if elegida and l['id'] == elegida['id'] }}"
  >{{ l['nombre'] }} ({{ l['n'] }})</a>{% endfor %}</p>{% endif %}
{% if n == 0 %}
<p class="grande">Ninguna todavia</p>
<p style="opacity:.6">Volve a la galeria y agrega al carrito las fotos que
queres, o eleji arriba una de tus listas. El total se calcula solo.</p>
<p style="opacity:.5;font-size:.9rem;margin:0">Precios de este evento:
{% for c, p in tarifas %}{{ c }} fotos {{ pesos(p) }}{% if not loop.last %}
&middot; {% endif %}{% endfor %}</p>
<p class="botones"><a class="wsp hueco" href="/g/{{ gal['slug'] }}">Volver a la
galeria</a></p>
{% else %}
<p class="grande">{{ n }} foto{{ '' if n == 1 else 's' }} <small>{{ total }}</small></p>
<p style="opacity:.5;font-size:.9rem;margin:0">Precios de este evento:
{% for c, p in tarifas %}{{ c }} fotos {{ pesos(p) }}{% if not loop.last %}
&middot; {% endif %}{% endfor %}</p>
<div class="cuenta"><p class="idcompra">{{ codigo }}</p></div>
<ol class="pasos3">{% for p in pasos %}{% if p %}<li>{{ p }}</li>{% endif %}{% endfor %}</ol>
<p class="botones">
{% if wsp %}<a class="wsp" href="https://wa.me/{{ wsp }}?text={{ msg|urlencode }}"
  rel="noopener">Escribime por WhatsApp &rarr;</a>{% endif %}
<a class="wsp hueco" href="/g/{{ gal['slug'] }}">Seguir eligiendo</a></p>
<p style="opacity:.35;font-size:.84rem;margin:1.8rem 0 0">Si agregas o sacas
fotos, el total y el ID se actualizan aca.</p>
{% endif %}
</section>
</main>"""

GATE = PIXIE + """<header class="pcab"><span class="tit"><b>{{ gal['nombre'] }}</b>
<span>rb.fotos</span></span></header>
{% if gal['portada_foto_id'] %}<div class="fondo">
{{ foto(url_tapa(gal), "100vw", alt="", fetchpriority="high") }}</div>{% endif %}
<main><form class="gate" method="post" action="/g/{{ gal['slug'] }}/entrar">
<h2 style="margin:0">{{ gal['nombre'] }}</h2>
<p>Dejame tu email y entras a ver la galeria.</p>
{% if error %}<p class="aviso">{{ error }}</p>{% endif %}
<input type="email" name="email" placeholder="tu@email.com" required autofocus>
<input name="nombre" placeholder="Tu nombre (opcional)" maxlength="60">
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

VIDEOS = PIXIE + """<style>
.vid .celda{aspect-ratio:16/9;cursor:pointer}
.vid .acc{opacity:1}
.vpie{position:absolute;left:.5rem;bottom:.5rem;font-size:.78rem;color:#fff;
  background:rgba(0,0,0,.6);padding:.2rem .5rem;border-radius:4px;pointer-events:none}
.vcomida{position:absolute;left:.5rem;top:.5rem;font-size:.75rem;color:#0b0f09;
  background:#c8f04a;padding:.15rem .5rem;border-radius:4px;pointer-events:none}
.vclips{position:absolute;right:.5rem;top:.5rem;font-size:.75rem;color:#fff;
  background:rgba(0,0,0,.6);padding:.15rem .5rem;border-radius:4px;pointer-events:none}
#vvisor .cuenta{position:absolute;top:.6rem;left:.8rem;z-index:1;color:#fff;
  background:rgba(0,0,0,.6);padding:.2rem .6rem;border-radius:4px;font-size:.8rem}
.vfecha{display:flex;gap:.8rem;justify-content:center;align-items:center;margin-top:1rem}
.vfecha input{background:#1a1a1a;color:#eee;border:1px solid #3a3a3a;border-radius:6px;
  padding:.4rem .6rem;color-scheme:dark}
#vvisor{border:0;padding:0;background:#000;max-width:min(96vw,1200px);width:100%}
#vvisor::backdrop{background:rgba(0,0,0,.85)}
#vvisor video{width:100%;display:block;max-height:85vh}
#vvisor button{position:absolute;top:.5rem;right:.5rem;z-index:1;background:rgba(0,0,0,.6);
  color:#fff;border:0;border-radius:50%;width:2.2rem;height:2.2rem;font-size:1.1rem;cursor:pointer}
#vvisor [hidden]{display:none!important}
#vvisor .carga{aspect-ratio:16/9;display:grid;place-content:center;justify-items:center;
  gap:.8rem;color:#ddd;text-align:center;padding:1rem}
#vvisor .carga p{margin:0}
</style>
<header class="pcab"><span class="tit"><b>{{ gal['nombre'] }}</b>
<span>videos{% if dia %} &middot; {{ dia.strftime('%d/%m/%Y') }}{% endif %}</span></span></header>
<main class="vid">
<section class="tarifas ordenar">
<div class="partidos">{% for k, v in filtros %}<a class="{{ 'on' if que == k }}"
  href="?que={{ k }}{% if dia %}&amp;dia={{ dia }}{% endif %}">{{ v }}</a>{% endfor %}</div>
<form method="get" class="vfecha">
<input type="hidden" name="que" value="{{ que }}">
<input type="date" name="dia" value="{{ dia or '' }}" onchange="this.form.submit()"
  aria-label="Dia">
{% if dia %}<a href="?que={{ que }}">Todos los dias</a>{% endif %}
<noscript><button class="btn">Ver</button></noscript>
</form>
<p style="text-align:center;opacity:.5;font-size:.85rem;margin:1rem 0 0">Cada tarjeta
es una visita al plato, con todos sus clips juntos. Las de
{{ comidas|join(', ') }} salen marcadas como comida.<br>
Los videos se borran solos a los 30 dias. Los que marques con &#9825; quedan para
siempre.</p>
</section>
{% if caido %}<p class="aviso" style="text-align:center;padding:3rem">La camara no
responde ahora. Proba de nuevo en un rato.</p>
{% elif not items %}<p style="text-align:center;opacity:.6;padding:3rem">No hay
visitas {{ 'guardadas' if que == 'guardados' else 'para mostrar' }}.</p>{% endif %}
<div class="grid">{% for v in items %}<figure class="celda" style="margin:0"
  data-clips="{{ v.clips|join(',') }}">
<img src="/g/{{ gal['slug'] }}/v/{{ v.portada }}.jpg?limpia=1" loading="lazy"
  decoding="async" alt="{{ v.quien }} {{ v.hora }}">
{% if v.comida %}<span class="vcomida">&#127853; Comida {{ v.comida }}</span>{% endif %}
{% if v.clips|length > 1 %}<span class="vclips">{{ v.clips|length }} clips</span>{% endif %}
<figcaption class="vpie">{{ v.quien }} &middot; {{ v.hora }} &middot; {{ v.dur }}</figcaption>
<div class="acc"><button class="fav {{ 'on' if v.guardado }}" title="Guardar para siempre"
  aria-label="Guardar para siempre"></button></div>
</figure>{% endfor %}</div>
{% if siguiente %}<p style="text-align:center;padding:2rem"><a class="btn"
  href="?que={{ que }}{% if dia %}&amp;dia={{ dia }}{% endif %}&amp;antes={{ siguiente }}">Mas
anteriores &rarr;</a></p>{% endif %}
</main>
<dialog id="vvisor"><button type="button" aria-label="Cerrar">&times;</button>
<span class="cuenta" hidden></span>
<div class="carga"><p>Cargando&hellip;</p></div>
<video controls playsinline hidden></video></dialog>
<script>
const visor = document.getElementById("vvisor"), vid = visor.querySelector("video");
const carga = visor.querySelector(".carga"), txt = carga.querySelector("p");
const cuenta = visor.querySelector(".cuenta");
const base = "/g/{{ gal['slug'] }}/v/";
let cola = [], pos = 0;

// Una visita son varios clips seguidos: se encadenan y el siguiente se va
// pidiendo mientras se mira el actual, para que no haya espera entre uno y otro.
function reproducir() {
  carga.hidden = false; vid.hidden = true;
  txt.textContent = "Cargando…";
  cuenta.hidden = cola.length < 2;
  cuenta.textContent = (pos + 1) + "/" + cola.length;
  vid.src = base + cola[pos] + ".mp4";
  vid.play().catch(() => {});
  if (cola[pos + 1]) {
    fetch(base + cola[pos + 1] + ".mp4", {headers: {Range: "bytes=0-1"}}).catch(() => {});
  }
}
function abrir(clips) {
  cerrar();
  cola = clips; pos = 0;
  visor.showModal();
  reproducir();
}
function cerrar() {
  vid.pause(); vid.removeAttribute("src"); vid.load();
}
vid.addEventListener("ended", () => {
  if (pos + 1 < cola.length) { pos++; reproducir(); }
});
// arranca apenas hay imagen: el resto sigue bajando mientras se mira
vid.addEventListener("loadeddata", () => { carga.hidden = true; vid.hidden = false; });
vid.addEventListener("error", () => {
  if (vid.getAttribute("src")) {
    carga.hidden = false; vid.hidden = true;
    txt.textContent = "No se pudo cargar el video. Proba de nuevo.";
  }
});
document.querySelectorAll(".vid .celda").forEach(c => c.addEventListener("click", async ev => {
  const clips = c.dataset.clips.split(",");
  const b = ev.target.closest(".fav");
  if (b) {
    // el corazon vale para la visita entera: se guardan o se sueltan todos
    const on = !b.classList.contains("on");
    b.disabled = true;
    const rs = await Promise.all(clips.map(id => fetch(base + id + "/guardar",
      {method: "POST", headers: {"Content-Type": "application/json"},
       body: JSON.stringify({on})})));
    b.disabled = false;
    if (rs.every(r => r.ok)) b.classList.toggle("on", on);
    return;
  }
  abrir(clips);
}));
visor.querySelector("button").onclick = () => visor.close();
visor.addEventListener("close", cerrar);
visor.addEventListener("click", ev => { if (ev.target === visor) visor.close(); });
</script>"""

CELDAS = """{% for f in fotos %}<figure class="celda" style="margin:0">
<img src="/p/{{ f['id'] }}" loading="lazy" decoding="async" alt="" draggable="false">
{% if repetidas and f['en_listas'] %}<span class="enls">{{ f['en_listas'] }}</span>{% endif %}
{% if gal['comentarios'] and f['ncom'] %}<span class="ncom" title="comentarios">&#128172; {{ f['ncom'] }}</span>{% endif %}
<div class="acc">
<button class="fav {{ 'on' if f['mia'] }}" data-id="{{ f['id'] }}" title="Me gusta"></button>
<button class="alista" data-id="{{ f['id'] }}" title="Elegir en que listas va">&#9776;</button>
{% if vende %}<button class="car {{ 'on' if f['en_carro'] }}" data-id="{{ f['id'] }}"
  title="Agregar al carrito"></button>{% endif %}
{% if gal['descargas'] %}<a class="dl" href="/d/{{ f['id'] }}{{ llave_d(gal['id']) }}" title="Descargar JPG original">&#8595;</a>
{% if f['tiene_raw'] and gal['raw_descarga'] %}<a class="raw" href="/d/{{ f['id'] }}/raw{{ llave_d(gal['id']) }}" title="Descargar RAW">RAW</a>{% endif %}{% endif %}
</div></figure>{% endfor %}"""

GRILLA = PIXIE + """<header class="pcab">
<span class="tit"><b>{% if lista %}{{ lista['nombre'] }}{% else %}{{ gal['nombre'] }}{% endif %}</b>
<span>{% if lista %}<a href="/g/{{ gal['slug'] }}">&larr; {{ gal['nombre'] }}</a>
&middot; {{ total }} fotos{% else %}{{ marca }} &middot; {{ total }} fotos
&middot; marcaste <b id="cuenta">{{ marcadas }}</b>{% endif %}</span></span>
{% if lista %}<b id="cuenta" hidden>{{ marcadas }}</b>{% endif %}
<button id="blistas">Listas &#9662;</button>
<div id="plistas" hidden><p class="ayuda">Toca el nombre para verla como
galería. El interruptor dice a cuáles van tus me gusta.</p><div id="listas"></div>
<button id="bnueva">+ Crear lista</button></div>
{% if gal['tipo'] == 'seleccion' and not lista %}<button id="bterminar"
  {{ 'disabled' if terminada }} data-ya="{{ 1 if terminada else '' }}"
  style="font-size:.8rem;padding:.25rem .7rem">{{ 'Selección enviada ✓' if terminada else 'Terminé mi selección' }}</button>
<script>
document.getElementById('bterminar').onclick = async function () {
  if (!confirm('¿Terminaste tu selección? Le avisamos a Renzo para que parta con la edición.')) return;
  this.disabled = true;
  const r = await fetch('/api/terminar/{{ gal["slug"] }}', {method: 'POST'});
  if (r.ok) { this.textContent = 'Selección enviada ✓'; }
  else { this.disabled = false; alert('No se pudo avisar, intenta de nuevo en un rato.'); }
};
</script>{% endif %}
{% if gal['comentarios'] %}<a class="btn" href="/g/{{ gal['slug'] }}/comentarios"
  style="font-size:.8rem;padding:.25rem .7rem">Comentarios</a>{% endif %}
{% if vende %}<a class="bcomprar" href="/g/{{ gal['slug'] }}/comprar">Comprar
<b id="ncar">{{ ncarro }}</b></a>{% endif %}</header>
<main>{% if tapa and not lista %}<section class="tapa">
<img src="/portada/{{ gal['slug'] }}?v={{ gal['portada_foto_id'] }}" alt="">
<div class="cen">
<h1>{{ gal['nombre'] }}</h1>
<p class="fecha">{{ gal['creada'][:10] }}</p>
<a class="entrar" href="#abajo">Ver galeria</a>
</div>
<p class="marca">{{ marca }}</p>
</section>{% endif %}<div id="abajo"></div>
{% if repetidas %}<p class="nota" style="margin:0 0 1rem">Estas fotos estan en mas
de una lista. Para sacar una de una lista: en <b>Listas</b> deja prendida solo esa
lista y toca el corazon de la foto.</p>{% endif %}
{% if not lista %}{% if gal['descargas'] and T.gal_gratis_texto %}
<section class="tarifas libre">
{% if T.gal_gratis_tit %}<p class="rot">{{ T.gal_gratis_tit }}</p>{% endif %}
<p class="grande">{{ T.gal_gratis_texto }}</p>
</section>{% elif vende and tarifas %}<section class="tarifas">
{% if T.gal_precios_tit %}<p class="rot">{{ T.gal_precios_tit }}</p>{% endif %}
<div class="packs">{% for c, p in tarifas %}<div><b>{{ c }}</b><span>fotos</span>
<em>{{ pesos(p) }}</em></div>{% endfor %}</div>
{% if T.gal_precios_nota %}<p class="nota">{{ T.gal_precios_nota }}</p>{% endif %}
</section>{% endif %}
{# que recibe: va siempre, venda o no. Un evento con descarga libre tambien
   necesita decir en que resolucion y por que via llegan las fotos. #}
{% if entrega %}<section class="tarifas entrega">
{% if T.gal_entrega_tit %}<p class="rot">{{ T.gal_entrega_tit }}</p>{% endif %}
<dl>{% for etiqueta, valor, items in entrega %}
<div><dt>{{ etiqueta }}</dt><dd>{{ valor }}
{% if items %}<ul class="quehace">{% for i in items %}<li>{{ i }}</li>{% endfor %}</ul>
{% endif %}</dd></div>{% endfor %}</dl>
</section>{% endif %}
<section class="tarifas ordenar">
<form method="get" id="fordenar">
{% if lista %}<input type="hidden" name="lista" value="{{ lista['id'] }}">{% endif %}
<label>Ordenar por
<select name="por" onchange="this.form.submit()">
{% for k, v in ordenes.items() %}<option value="{{ k }}"
  {{ 'selected' if k == por }}>{{ v[0] }}</option>{% endfor %}
</select></label>
<label>De
<select name="dir" onchange="this.form.submit()">
<option value="asc" {{ 'selected' if dire == 'ASC' }}>Ascendente</option>
<option value="desc" {{ 'selected' if dire == 'DESC' }}>Descendente</option>
</select></label>
{% if rango %}<span class="franja">
<output id="odesde"></output>
<span class="dual">
<span class="pista"></span><span class="activo" id="pistaAct"></span>
<input type="range" name="desde" min="0" max="{{ largo }}" value="{{ vdesde }}"
  aria-label="Desde">
<input type="range" name="hasta" min="0" max="{{ largo }}" value="{{ vhasta }}"
  aria-label="Hasta">
</span>
<output id="ohasta"></output>
</span>{% endif %}
<noscript><button class="btn">Ordenar</button></noscript>
</form>
{% if grupos %}<div class="partidos">
<a href="?{{ 'por=' ~ por ~ '&dir=' ~ ('desc' if dire == 'DESC' else 'asc') }}"
  class="{{ 'on' if not grupo }}">Todos</a>
{% for g in grupos %}<a class="{{ 'on' if grupo == g['grupo'] }}"
  href="?grupo={{ g['grupo']|urlencode }}&amp;por={{ por }}&amp;dir={{
    'desc' if dire == 'DESC' else 'asc' }}">
{% for c in (g['col'] or '').split(',') %}{% if c %}<i style="background:{{ c }}"></i>{% endif %}{% endfor %}
{{ g['grupo'] }} <b>{{ g['n'] }}</b></a>{% endfor %}
</div>{% endif %}
{% if rango and (vdesde > 0 or vhasta < largo) %}
<p class="nota">Mostrando {{ total }} fotos de esa franja.
<a href="?por={{ por }}&amp;dir={{ 'desc' if dire == 'DESC' else 'asc' }}">ver
todas</a></p>{% endif %}
</section>{% endif %}<div class="grid" id="grid">""" + CELDAS + """<div id="fin"></div>
</div></main>

<div id="pop" class="pop" hidden></div>
{% if gal['comentarios'] %}<div id="pcom" hidden></div>{% endif %}
<div id="aviso" class="aviso" hidden role="status"></div>
<div id="visor" hidden>
  <span class="vpos" id="vpos"></span>
  <button class="cerrar" id="vcerrar" title="Cerrar (Esc)">&times;</button>
  <button class="ant" id="vant" title="Anterior">&lsaquo;</button>
  <img id="vimg" alt="" draggable="false">
  <button class="sig" id="vsig" title="Siguiente">&rsaquo;</button>
  <div class="vacc">
    <button id="vlike" title="Me gusta (z)"></button>
    <button id="vlistas" title="Elegir en que listas va (l)">&#9776;</button>
    {% if gal['comentarios'] %}<button id="vcom" title="Comentarios (k)">&#128172;</button>{% endif %}
    {% if vende %}<button id="vcar" title="Agregar al carrito (c)"></button>{% endif %}
    {% if gal['descargas'] %}<a id="vdl" title="Descargar JPG">&#8595;</a>
    {% if gal['raw_descarga'] %}<a id="vraw" title="Descargar RAW">RAW</a>{% endif %}{% endif %}
  </div>
</div>

<script>
const cuenta = document.getElementById("cuenta");
const grid = document.getElementById("grid");
const fin = document.getElementById("fin");
const boton = f => f.querySelector(".acc .fav");
const carro = f => f.querySelector(".acc .car");
const ncar = document.getElementById("ncar");
const vcar = document.getElementById("vcar");
function pintarCarrito(n) { if (ncar) ncar.textContent = n; }

async function alCarrito(id) {
  const r = await fetch("/api/carrito", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({foto_id: +id})});
  if (!r.ok) return null;
  const d = await r.json();
  pintarCarrito(d.carrito);
  return d.en;
}
let figs = [];

async function marcar(id) {
  const r = await fetch("/api/megusta", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({foto_id: +id})});
  if (!r.ok) return null;
  const d = await r.json();
  // los numeros de Listas se repintan al tiro: si quedaban quietos hasta
  // reabrir el menu, el cliente creia que no habia entrado y tocaba de nuevo
  pintarListas(d);
  avisar((d.mia ? "Agregada a " : "Sacada de ") + d.en.join(" y "));
  return d.mia;
}

// aviso corto abajo: que hizo el ultimo toque
let tAviso = null;
function avisar(txt) {
  const a = document.getElementById("aviso");
  a.textContent = txt;
  a.hidden = false;
  clearTimeout(tAviso);
  tAviso = setTimeout(() => a.hidden = true, 2200);
}

// ------------------------------------------- alto real de cada foto
// La celda ocupa N filas de FILA px (mas el gap que se traga entre ellas).
// Se mide con naturalWidth/Height, asi que hay que esperar a que cargue.
const FILA = 8;
// el gap se lee del CSS: escrito a mano (8.8) daba celdas 14% mas bajas que la
// foto, y object-fit:cover se comia esa diferencia recortando igual que antes
const gapFilas = () => parseFloat(getComputedStyle(grid).rowGap) || 0;
function medir(fig, gap) {
  const img = fig.querySelector("img");
  if (!img.naturalWidth) return;
  const ancho = fig.getBoundingClientRect().width;
  if (!ancho) return;                       // todavia no esta maquetada
  if (gap === undefined) gap = gapFilas();
  const alto = ancho * img.naturalHeight / img.naturalWidth;
  fig.style.gridRowEnd = "span " + Math.max(1, Math.round((alto + gap) / (FILA + gap)));
  fig.classList.add("medida");
}

// al cambiar el ancho cambia la columna, y con ella el alto de cada foto
let tMedir = null;
addEventListener("resize", () => {
  clearTimeout(tMedir);
  tMedir = setTimeout(() => { const g = gapFilas(); figs.forEach(f => medir(f, g)); }, 150);
});

// se vuelve a llamar por cada trozo que llega: los nuevos se enganchan igual
function enganchar() {
  figs = [...grid.querySelectorAll(".celda")];
  figs.forEach(fig => {
    if (fig.dataset.listo) return;
    fig.dataset.listo = "1";
    const b = boton(fig);
    b.onclick = async e => {
      e.stopPropagation();
      const m = await marcar(b.dataset.id);
      if (m !== null) b.classList.toggle("on", m);
    };
    const c = carro(fig);
    if (c) c.onclick = async e => {
      e.stopPropagation();
      const en = await alCarrito(c.dataset.id);
      if (en !== null) c.classList.toggle("on", en);
    };
    const al = fig.querySelector(".alista");
    if (al) al.onclick = e => { e.stopPropagation(); abrirPop(al.dataset.id, al); };
    const im = fig.querySelector("img");
    im.onclick = () => abrir(figs.indexOf(fig));
    if (im.complete) medir(fig);
    else im.addEventListener("load", () => medir(fig), {once: true});
  });
}
enganchar();

// ------------------------------------------------------- scroll infinito
// `cargando` guarda la promesa del trozo en vuelo, no un booleano: quien
// llegue mientras viaja espera ESA carga en vez de seguir de largo. Sin eso
// el visor daba la vuelta a la 1 justo cuando el scroll ya estaba pidiendo.
let pagina = 1, cargando = null, hayMas = {{ 'true' if paginas > 1 else 'false' }};
function masFotos() {
  if (cargando) return cargando;
  if (!hayMas) return Promise.resolve();
  cargando = (async () => {
  fin.textContent = "cargando...";
  try {
    // los parametros se arman con URL, no concatenando: Jinja escapa el "&"
    // a "&amp;" y la tanda siguiente llegaba con "amp;lista" en vez de
    // "lista", asi que el scroll dentro de una lista pegaba fotos de toda
    // la galeria
    const u = new URL("/g/{{ gal['slug'] }}", location.origin);
    u.searchParams.set("trozo", pagina + 1);
    if (LISTA_ABIERTA) u.searchParams.set("lista", LISTA_ABIERTA);
    if (EN_REPETIDAS) u.searchParams.set("repetidas", "1");
    if (EN_BAJADAS) u.searchParams.set("bajadas", "1");
    u.searchParams.set("por", POR);
    u.searchParams.set("dir", DIR);
    // sin esto la segunda tanda trae fotos de los otros partidos
    if (GRUPO) u.searchParams.set("grupo", GRUPO);
    // sin esto la segunda tanda llega sin filtrar y mete fotos de otras horas
    if (DESDE !== "0" || HASTA !== LARGO) {
      u.searchParams.set("desde", DESDE);
      u.searchParams.set("hasta", HASTA);
    }
    const r = await fetch(u);
    const html = (await r.text()).trim();
    if (!r.ok || !html) hayMas = false;
    else {
      pagina++;
      fin.insertAdjacentHTML("beforebegin", html);
      enganchar();
    }
  } catch (e) { /* se reintenta solo en el proximo scroll */ }
  cargando = null;
  fin.textContent = hayMas ? "" : "no hay mas fotos";
  })();
  return cargando;
}
new IntersectionObserver(e => { if (e[0].isIntersecting) masFotos(); },
                         {rootMargin: "1200px"}).observe(fin);

// ------------------------------------------------------- listas
const SLUG = "{{ gal['slug'] }}";
const LISTA_ABIERTA = "{{ lista['id'] if lista else '' }}";
const EN_REPETIDAS = {{ 'true' if repetidas else 'false' }};
const EN_BAJADAS = {{ 'true' if bajadas else 'false' }};
// los sliders mandan minutos desde la primera foto; la etiqueta los pasa a
// hora del reloj para que el cliente vea "20:15" y no "113"
const T0 = {{ t0 }};
const rDesde = document.querySelector('input[name="desde"]');
const rHasta = document.querySelector('input[name="hasta"]');
if (rDesde) {
  const reloj = m => {
    const t = (T0 + (+m)) % 1440;
    return String(Math.floor(t / 60)).padStart(2, "0") + ":"
         + String(t % 60).padStart(2, "0");
  };
  const LARGO_N = +rDesde.max || 1;
  const pintar = () => {
    // los pivotes no se cruzan: el que empuja al otro se queda en el borde
    if (+rDesde.value > +rHasta.value) {
      if (document.activeElement === rDesde) rDesde.value = rHasta.value;
      else rHasta.value = rDesde.value;
    }
    odesde.value = reloj(rDesde.value);
    ohasta.value = reloj(rHasta.value);
    const a = (+rDesde.value) * 100 / LARGO_N;
    const b = (+rHasta.value) * 100 / LARGO_N;
    pistaAct.style.left = a + "%";
    pistaAct.style.width = (b - a) + "%";
  };
  [rDesde, rHasta].forEach(r => {
    r.addEventListener("input", pintar);
    // recargar al soltar, no en cada pixel del arrastre
    r.addEventListener("change", () => r.form.submit());
  });
  pintar();
}

const DESDE = "{{ vdesde }}";
const HASTA = "{{ vhasta }}";
const LARGO = "{{ largo }}";
const GRUPO = "{{ grupo }}";
const POR = "{{ por }}";
const DIR = "{{ 'desc' if dire == 'DESC' else 'asc' }}";
const VENDE = {{ 'true' if vende else 'false' }};
const plistas = document.getElementById("plistas");
const listasDiv = document.getElementById("listas");
let listas = [];

const esc = t => { const d = document.createElement("span"); d.textContent = t; return d.innerHTML; };

function pintarListas(d) {
  listas = d.listas;
  cuenta.textContent = d.marcadas;
  // dos filas fijas arriba: volver a la galeria entera y el carrito. No son
  // listas que se administren, por eso no llevan interruptor ni flechas.
  listasDiv.innerHTML = `
    <div class="fila fija${LISTA_ABIERTA ? "" : " abierta"}">
      <a class="nom" href="/g/${SLUG}">Galería completa</a>
    </div>` + (VENDE ? `
    <div class="fila fija${String(d.carrito_id) === LISTA_ABIERTA ? " abierta" : ""}">
      <a class="nom" href="?lista=${d.carrito_id}"
        >Carrito<span class="n">${d.carrito}</span></a>
    </div>` : "") + `<div class="sep"></div>` + listas.map((l, i) => `
    <div class="fila${String(l.id) === LISTA_ABIERTA ? ' abierta' : ''}" data-id="${l.id}">
      <input type="checkbox" ${l.activa ? "checked" : ""}
        title="Los me-gusta van a las listas prendidas">
      <a class="nom" href="?lista=${l.id}" title="Ver esta lista como galeria"
        >${esc(l.nombre)}<span class="n">${l.n}</span></a>
      <button class="mov" data-d="-1" ${i ? "" : "disabled"} title="Subir">&uarr;</button>
      <button class="mov" data-d="1" ${i === listas.length - 1 ? "disabled" : ""}
        title="Bajar">&darr;</button>
    </div>`).join("") + (d.bajadas ? `
    <div class="fila fija${EN_BAJADAS ? " abierta" : ""}">
      <a class="nom" href="?bajadas=1" title="Las que te bajaste. Se llena sola"
        >Descargadas<span class="n">${d.bajadas}</span></a>
    </div>` : "") + (d.repetidas ? `
    <div class="fila fija${EN_REPETIDAS ? " abierta" : ""}">
      <a class="nom" href="?repetidas=1" style="color:#ff9b9b"
        >Repetidas entre listas<span class="n">${d.repetidas}</span></a>
    </div>` : "") + `<p class="ayuda">${d.distintas} fotos distintas entre todas tus listas</p>`;

  pintarCarrito(d.carrito);
  listasDiv.querySelectorAll(".fila:not(.fija)").forEach(fila => {
    const id = fila.dataset.id;
    fila.querySelector("input").onchange = async e => {
      const r = await fetch(`/api/listas/${SLUG}/${id}/activa`, {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({activa: e.target.checked})});
      if (r.ok) { pintarListas(await r.json()); refrescarCorazones(); }
    };
    fila.querySelectorAll(".mov").forEach(b => b.onclick = async () => {
      const i = listas.findIndex(l => String(l.id) === id), j = i + (+b.dataset.d);
      if (j < 0 || j >= listas.length) return;
      const ids = listas.map(l => l.id);
      [ids[i], ids[j]] = [ids[j], ids[i]];
      const r = await fetch(`/api/listas/${SLUG}/orden`, {method: "POST",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify({ids})});
      if (r.ok) pintarListas(await r.json());
    });
  });
}

// las listas activas cambian que corazones van llenos: se repintan sin
// recargar, para no perder todo lo que el scroll infinito ya trajo
async function refrescarCorazones() {
  const r = await fetch(`/api/marcadas/${SLUG}`);
  if (!r.ok) return;
  const puestas = new Set((await r.json()).ids);
  figs.forEach(f => boton(f).classList.toggle("on", puestas.has(+boton(f).dataset.id)));
  if (!visor.hidden && figs[idx])
    vlike.classList.toggle("on", boton(figs[idx]).classList.contains("on"));
}

document.getElementById("blistas").onclick = async e => {
  e.stopPropagation();
  plistas.hidden = !plistas.hidden;
  if (!plistas.hidden) {
    const r = await fetch(`/api/listas/${SLUG}`);
    if (r.ok) pintarListas(await r.json());
  }
};
plistas.onclick = e => e.stopPropagation();
document.addEventListener("click", () => plistas.hidden = true);

document.getElementById("bnueva").onclick = async () => {
  const nombre = prompt("Como se va a llamar la lista?");
  if (!nombre || !nombre.trim()) return;
  const r = await fetch(`/api/listas/${SLUG}`, {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({nombre: nombre.trim()})});
  if (r.ok) pintarListas(await r.json());
};

// ------------------------------------------------------- visor
let idx = -1;
// vCargando y no `cargando`: ese nombre ya lo usa el scroll infinito, y dos
// `let` iguales en el mismo scope rompen el <script> entero
let vCargando = false, tCarga = null;
let rumbo = 1, precargas = [];
function vListo() {
  vCargando = false;
  clearTimeout(tCarga);
  visor.classList.remove("cargando");
}
vimg.onload = vimg.onerror = vListo;

let tVisto = null;
function abrir(i) {
  if (!figs.length) return;
  idx = (i + figs.length) % figs.length;
  const fig = figs[idx], id = boton(fig).dataset.id;
  vCargando = true;
  visor.classList.add("cargando");
  // red de seguridad: si la foto nunca llega, el visor no queda trabado
  clearTimeout(tCarga);
  tCarga = setTimeout(vListo, 6000);
  vimg.src = "/p/" + id + "?g=1";
  if (pcom) pcom.hidden = true;
  // "la vio" es que haya quedado en pantalla, no que el navegador la haya
  // bajado: pasando en seco con la flecha apretada no cuenta ninguna
  clearTimeout(tVisto);
  tVisto = setTimeout(() => navigator.sendBeacon("/api/visto/" + id), 500);
  const vdl = document.getElementById("vdl");
  if (vdl) {
    const dl = fig.querySelector("a.dl");
    vdl.href = dl ? dl.href : "/d/" + id;
    const raw = fig.querySelector("a.raw"), vraw = document.getElementById("vraw");
    if (vraw) {
      vraw.hidden = !raw;
      if (raw) vraw.href = raw.href;
    }
  }
  vlike.classList.toggle("on", boton(fig).classList.contains("on"));
  if (vcar) vcar.classList.toggle("on", carro(fig).classList.contains("on"));
  vpos.textContent = (idx + 1) + " / " + figs.length;
  visor.hidden = false;
  document.body.style.overflow = "hidden";
  // precarga 3 hacia donde vas y 1 para atras: al avanzar la foto ya esta en
  // el navegador. Se guardan las Image: sin referencia la descarga se cancela.
  precargas = [1, 2, 3, -1].map(k => {
    const v = figs[(idx + k * rumbo + figs.length) % figs.length];
    if (!v || v === fig) return null;
    const im = new Image();
    im.src = "/p/" + boton(v).dataset.id + "?g=1";
    return im;
  });
}
function cerrar() {
  clearTimeout(tVisto);
  if (pcom) pcom.hidden = true;
  visor.hidden = true;
  vimg.src = "";
  document.body.style.overflow = "";
}

// Dos frenos distintos, para dos problemas distintos:
// - `cargando`: no se pasa a la siguiente hasta que la actual haya llegado.
//   Sin esto, con el cache frio el contador avanzaba y la imagen no, y
//   parecian todas la misma foto.
// - PASO_MIN: aunque esten todas en cache (~18 ms), manteniendo la flecha el
//   teclado repite ~30 veces por segundo y no se alcanzaria a ver nada.
let ultimoPaso = 0;
const PASO_MIN = 60;
async function navegar(d) {
  if (vCargando) return;
  pop.hidden = true;
  const t = performance.now();
  if (t - ultimoPaso < PASO_MIN) return;
  ultimoPaso = t;
  rumbo = d < 0 ? -1 : 1;
  // el visor solo conoce las fotos que la grilla ya bajo: al salirse del
  // final hay que traer la tanda siguiente, si no la 300 volvia a la 1
  while (idx + d >= figs.length && hayMas) {
    const antes = figs.length;
    await masFotos();
    // si la tanda no trajo nada (red caida), dar la vuelta como antes en vez
    // de quedar pidiendo para siempre
    if (figs.length === antes) break;
  }
  abrir(idx + d);
  // el proximo trozo se pide 20 antes del final: asi el salto de tanda no se
  // siente, y si igual lo alcanzas el while de arriba lo espera
  if (idx > figs.length - 20 && hayMas) masFotos();
}

// -------------------------------------------------- comentarios de la foto
const pcom = document.getElementById("pcom");
const vcom = document.getElementById("vcom");
// el mismo globito que pone la plantilla, por codigo: la plantilla vive dentro
// de un string de Python y un \\u ahi lo rompe
const EMOJI_COM = String.fromCodePoint(0x1F4AC);
if (pcom) {
  const pintar = d => {
    pcom.innerHTML = d.comentarios.map(c => `<div class="uno">
      <p style="margin:0"><b>${esc(c.nombre)}</b>
      <span style="opacity:.5;font-size:.8rem">${esc(c.hora)}</span>
      ${c.mio ? `<button class="btn" data-borrar="${c.id}"
        style="font-size:.7rem;padding:.1rem .5rem;float:right">borrar</button>` : ""}</p>
      <p style="margin:.2rem 0 0;white-space:pre-wrap">${esc(c.texto)}</p></div>`).join("")
      || `<p style="opacity:.55;margin:.2rem 0">Nadie comento esta foto todavia.</p>`;
    pcom.innerHTML += `<textarea id="ctxt" maxlength="500"
      placeholder="Escribi un comentario"></textarea>
      <p style="margin:.4rem 0 0;text-align:right">
      <button class="btn" id="cenviar">Comentar</button></p>`;
    document.getElementById("cenviar").onclick = enviar;
    pcom.querySelectorAll("[data-borrar]").forEach(b => b.onclick = async () => {
      const r = await fetch("/api/comentario/" + b.dataset.borrar + "/borrar",
                            {method: "POST"});
      if (r.ok) { pintar(await r.json()); avisar("Comentario borrado"); }
    });
  };
  const enviar = async () => {
    const t = document.getElementById("ctxt");
    const texto = t.value.trim();
    if (!texto) return;
    const id = boton(figs[idx]).dataset.id;
    let r = await fetch("/api/comentario", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({foto_id: +id, texto})});
    if (r.status === 409) {                    // falta el nombre: se pide aca
      const n = (prompt("Como te llamas? Es para firmar tu comentario") || "").trim();
      if (!n) return;
      const rn = await fetch("/api/nombre/" + SLUG, {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({nombre: n})});
      if (!rn.ok) { avisar("No pude guardar tu nombre"); return; }
      r = await fetch("/api/comentario", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({foto_id: +id, texto})});
    }
    if (!r.ok) { avisar("No se pudo comentar"); return; }
    pintar(await r.json());
    // el numerito de la celda, para no tener que recargar la grilla
    const fig = figs[idx];
    let n = fig.querySelector(".ncom");
    if (!n) {
      n = document.createElement("span");
      n.className = "ncom";
      fig.appendChild(n);
    }
    n.textContent = EMOJI_COM + " " +
      (+(n.textContent.replace(/[^0-9]/g, "") || 0) + 1);
  };
  const abrirCom = async () => {
    if (!pcom.hidden) { pcom.hidden = true; return; }
    const r = await fetch("/api/comentarios/" + boton(figs[idx]).dataset.id);
    if (!r.ok) return;
    pintar(await r.json());
    pcom.hidden = false;
  };
  if (vcom) vcom.onclick = abrirCom;
  document.addEventListener("keydown", e => {
    if (!visor.hidden && e.key === "k" && e.target.tagName !== "TEXTAREA") abrirCom();
  });
}

vcerrar.onclick = cerrar;
// mantener apretada la flecha en el telefono tiene que hacer lo mismo que
// tener apretada la del teclado en el compu: repetir sola. El teclado repite
// solo despues de una pausa, si no un toque corto avanzaria de a dos.
function repetir(btn, d) {
  let espera = null, repite = null;
  const parar = () => {
    clearTimeout(espera); clearInterval(repite); espera = repite = null;
  };
  btn.addEventListener("pointerdown", e => {
    e.preventDefault();          // sin esto el toque tambien dispara un click
    navegar(d);
    espera = setTimeout(() => repite = setInterval(() => navegar(d),
                                                   PASO_MIN), 400);
  });
  // soltar, cancelar o correr el dedo fuera del boton: todo frena
  ["pointerup", "pointercancel", "pointerleave"].forEach(
    ev => btn.addEventListener(ev, parar));
  // Android abre igual su menu aunque el pointerdown este frenado
  btn.addEventListener("contextmenu", e => e.preventDefault());
}
repetir(vant, -1);
repetir(vsig, 1);
visor.onclick = e => { if (e.target === visor) cerrar(); };
// --------------------------------------------- en que listas va ESTA foto
const pop = document.getElementById("pop");
const vlistas = document.getElementById("vlistas");
async function abrirPop(fid, ancla) {
  const r = await fetch(`/api/listas/${SLUG}/foto/${fid}`);
  if (!r.ok) return;
  const d = await r.json();
  pop.innerHTML = `<p class="ptit">Esta foto va en:</p>` + d.listas.map(l =>
    `<label><input type="checkbox" data-l="${l.id}" ${l.en ? "checked" : ""}>
      ${esc(l.nombre)}</label>`).join("");
  pop.hidden = false;
  // arriba del boton si entra, si no abajo; nunca fuera de la pantalla
  const b = ancla.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(innerWidth - pop.offsetWidth - 8,
                                        b.right - pop.offsetWidth)) + "px";
  const y = b.top - pop.offsetHeight - 8 > 8 ? b.top - pop.offsetHeight - 8 : b.bottom + 8;
  pop.style.top = Math.max(8, Math.min(y, innerHeight - pop.offsetHeight - 8)) + "px";
  pop.querySelectorAll("input").forEach(i => i.onchange = async () => {
    const r2 = await fetch(`/api/listas/${SLUG}/${i.dataset.l}/foto`, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({foto_id: +fid, en: i.checked})});
    if (!r2.ok) { i.checked = !i.checked; return; }
    pintarListas(await r2.json());
    refrescarCorazones();
    avisar((i.checked ? "Agregada a " : "Sacada de ") + i.parentNode.textContent.trim());
  });
}
document.addEventListener("click", e => {
  if (!pop.hidden && !pop.contains(e.target) && !e.target.closest(".alista, #vlistas"))
    pop.hidden = true;
});
vlistas.onclick = e => {
  e.stopPropagation();
  abrirPop(boton(figs[idx]).dataset.id, vlistas);
};

vlike.onclick = async () => {
  const b = boton(figs[idx]);
  const m = await marcar(b.dataset.id);
  if (m === null) return;
  b.classList.toggle("on", m);
  vlike.classList.toggle("on", m);
};
if (vcar) vcar.onclick = async () => {
  const c = carro(figs[idx]);
  const en = await alCarrito(c.dataset.id);
  if (en === null) return;
  c.classList.toggle("on", en);
  vcar.classList.toggle("on", en);
};

document.addEventListener("keydown", e => {
  if (visor.hidden) return;
  if (e.key === "Escape") cerrar();
  else if (e.key === "ArrowLeft") navegar(-1);
  else if (e.key === "ArrowRight") navegar(1);
  else if (e.key === "z" || e.key === "Z") { e.preventDefault(); vlike.onclick(); }
  else if (e.key === "l" || e.key === "L") { e.preventDefault(); vlistas.click(); }
  else if ((e.key === "c" || e.key === "C") && vcar) { e.preventDefault(); vcar.onclick(); }
  else if (e.key === " ") { e.preventDefault(); vlike.onclick(); }
});

// deslizar para pasar de foto, pero NO mientras se hace zoom: con dos dedos,
// o con la pagina ya agrandada, el gesto es para mirar la foto y pasaba de
// largo sin querer. Tambien se descarta el arrastre mas vertical que horizontal.
let x0 = null, y0 = null, multi = false;
const agrandada = () => (visualViewport ? visualViewport.scale : 1) > 1.05;
visor.addEventListener("touchstart", e => {
  if (e.touches.length > 1) { multi = true; x0 = null; return; }
  multi = false;
  x0 = e.touches[0].clientX;
  y0 = e.touches[0].clientY;
}, {passive: true});
visor.addEventListener("touchmove", e => { if (e.touches.length > 1) { multi = true; x0 = null; } },
                       {passive: true});
visor.addEventListener("touchend", e => {
  if (x0 === null || multi || e.touches.length || agrandada()) { x0 = null; return; }
  const dx = e.changedTouches[0].clientX - x0;
  const dy = e.changedTouches[0].clientY - y0;
  x0 = null;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) navegar(dx < 0 ? 1 : -1);
}, {passive: true});
</script>"""

PANEL_INDICE = PANEL_CSS + PANEL_BARRA + """<main>
<div class="cab"><h1 class="tit">Galerias</h1>
<a class="btn" href="/panel/nueva">+ Nueva galeria</a></div>
{# las tres vias de alta existian pero no se explicaban en ningun lado #}
<p class="pista">Hay tres formas de armar una:
<a href="/panel/nueva">elegir una carpeta</a> que ya este en el disco,
<a href="/panel/subir">subir fotos</a> desde este aparato, o
<a href="/panel/eventos">publicar un evento</a> del curador.</p>

{% if carpetas %}<h2 class="grupo">Carpetas <a href="/panel/carpetas">editar</a></h2>
<ul class="gals">{% for c in carpetas %}<li class="gal" style="grid-template-columns:1fr auto">
<a class="nom" href="/panel/carpetas#c-{{ c['slug'] }}">{{ c['nombre'] }}</a>
<span class="cifras" style="grid-column:2">{{ c['n'] }} galeria{{ '' if c['n'] == 1 else 's' }}</span>
</li>{% endfor %}</ul>{% endif %}

{% for titulo, lista in grupos %}<h2 class="grupo">{{ titulo }}</h2>
<ul class="gals">
{% for g in lista %}<li class="gal">
<a class="mini" href="/panel/{{ g['slug'] }}">{% if g['tapa'] %}<img
  src="/panel/p/{{ g['tapa'] }}" loading="lazy" alt="">{% endif %}</a>
<a class="nom" href="/panel/{{ g['slug'] }}">{{ g['nombre'] }}</a>
<span class="cifras">{{ g['fotos'] }} fotos &middot;
  {{ g['clientes'] }} client{{ 'e' if g['clientes'] == 1 else 'es' }}</span>
<span class="chips">{{ estado(g, hoy) }}</span>
</li>{% endfor %}
</ul>
{% else %}<ul class="gals"><li class="gal" style="grid-template-columns:1fr">
<span class="nom" style="color:var(--humo)">Todavia no publicaste ninguna.</span>
</li></ul>{% endfor %}
</main>"""

PANEL_CARPETAS = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Carpetas</h1>
<p class="pista">Una carpeta junta varias galerias bajo un solo link. Quien lo
abre las ve como en la pagina de eventos y entra a cada una como siempre.
Borrar una carpeta no borra sus galerias.</p>
{% macro elegir(ids) %}<div class="chkgal">{% for titulo, lista in grupos %}
<p class="grupo-mini">{{ titulo }}</p>
<ul class="gals">{% for g in lista %}<li><label class="gal elige">
<span class="mini">{% if g['tapa'] %}<img src="/panel/p/{{ g['tapa'] }}"
  loading="lazy" alt="">{% endif %}</span>
<span class="nom">{{ g['nombre'] }}</span>
<span class="cifras">{{ g['fotos'] }} fotos</span>
<input type="checkbox" name="g" value="{{ g['id'] }}" {{ 'checked' if g['id'] in ids }}>
</label></li>{% endfor %}</ul>{% endfor %}</div>{% endmacro %}

{% for c in carpetas %}<form class="carpeta" id="c-{{ c['slug'] }}"
  method="post" action="/panel/carpetas">
<input type="hidden" name="slug" value="{{ c['slug'] }}">
<input name="nombre" value="{{ c['nombre'] }}" required maxlength="80">
<div class="link"><input id="l-{{ c['slug'] }}" readonly
  value="{{ request.url_root }}c/{{ c['slug'] }}">
<button class="btn" type="button" onclick="
  const i=document.getElementById('l-{{ c['slug'] }}'); i.select();
  navigator.clipboard.writeText(i.value).then(()=>this.textContent='Copiado');
">Copiar</button>
<a class="btn hueco" href="/c/{{ c['slug'] }}" target="_blank" rel="noopener">Ver</a></div>
{{ elegir(c['ids']) }}
<p><button class="btn">Guardar</button>
<button class="btn hueco" formaction="/panel/carpetas/{{ c['slug'] }}/borrar"
  formnovalidate onclick="return confirm('Borrar la carpeta? Las galerias quedan.')"
  >Borrar carpeta</button></p>
</form>{% endfor %}

<form class="carpeta" method="post" action="/panel/carpetas">
<p class="grupo-mini" style="margin-top:0">Nueva carpeta</p>
<input name="nombre" placeholder="Nombre, ej. Uandes" required maxlength="80">
{{ elegir([]) }}
<p><button class="btn">Crear carpeta</button></p>
</form>
</main>"""

PANEL_NUEVA = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Nueva galeria</h1>
{% set aa = ('&a=' ~ dest['slug']) if dest else '' %}
{% if dest %}<p class="migas"><a href="/panel/{{ dest['slug'] }}/fotos">&larr;
{{ dest['nombre'] }}</a> &middot; elegi la carpeta que le queres agregar</p>{% endif %}
<p class="migas"><a href="/panel/nueva?dir={{ aa }}">/mnt/fotos</a>
{% for nom, ruta in migas %} / <a
  href="/panel/nueva?dir={{ ruta|urlencode }}{{ aa }}">{{ nom }}</a>{% endfor %}</p>

{% if hay_sub %}<p class="nota">{% if hondo %}Contando tambien las
subcarpetas. <a href="/panel/nueva?dir={{ rel|urlencode }}{{
  ('&a=' ~ dest['slug']) if dest else '' }}">solo esta carpeta</a>
{% else %}Solo las fotos sueltas de esta carpeta.
<a href="/panel/nueva?dir={{ rel|urlencode }}&amp;sub=1{{
  ('&a=' ~ dest['slug']) if dest else '' }}">incluir las subcarpetas</a>{% endif %}</p>
{% endif %}
{% if resuelto and not fuentes %}<p class="nota">Esta carpeta sigue la convencion
RAW / Jpeg / Export: se van a publicar las fotos de <b>{{ resuelto }}</b>.</p>
{% endif %}

{% if cuantas and dest %}
<form class="publicar" method="post" action="/panel/{{ dest['slug'] }}/agregar">
<input type="hidden" name="dir" value="{{ rel }}">
<input type="hidden" name="sub" value="{{ '1' if hondo else '' }}">
<button class="btn">Agregar {{ cuantas }} fotos a {{ dest['nombre'] }}</button>
<span style="opacity:.5;font-size:.85rem">Las que ya esten en la galeria se
saltean.</span>
</form>
{% elif cuantas %}
<form class="publicar" method="post" action="/panel/nueva">
<input type="hidden" name="dir" value="{{ rel }}">
<input type="hidden" name="sub" value="{{ '1' if hondo else '' }}">
<input name="nombre" placeholder="Nombre de la galeria" required autofocus>
{% if fuentes %}<span style="font-size:.85rem;opacity:.7">Fotos de</span>
{% for f, n in fuentes %}<label style="font-size:.85rem;opacity:{{ 1 if n else 0.4 }}">
<input type="radio" name="fuente" value="{{ f }}" {{ 'disabled' if not n }}
  {{ 'checked' if f == por_defecto }}> {{ f }} ({{ n }})</label>{% endfor %}{% endif %}
<label style="font-size:.85rem;opacity:.7">Tipo
<select name="tipo">
<option value="">sin rotulo</option>
{% for t in tipos %}<option value="{{ t }}"
  {{ 'selected' if t == 'seleccion' and por_defecto|lower == 'jpeg' }}>{{ tipo_nombre[t] }}</option>{% endfor %}
</select></label>
<label style="font-size:.85rem;opacity:.7">
<input type="checkbox" name="sin_marca" value="1"> sin marca de agua</label>
<label style="font-size:.85rem;opacity:.7">Marca
<select name="marca_texto">
{% for m in marcas %}<option value="{{ m }}">{{ m }}</option>{% endfor %}
</select></label>
<label style="font-size:.85rem;opacity:.7">
<input type="checkbox" name="descargas" value="1"> permitir descargas</label>
<button class="btn">{{ 'Publicar' if fuentes else 'Publicar %d fotos' % cuantas }}</button>
</form>
{% else %}
<p style="opacity:.55">No hay fotos {{ '' if hondo else 'sueltas ' }}en esta
carpeta. Entra a alguna de abajo{{ ', o inclui las subcarpetas' if hay_sub and not hondo }}.</p>
{% endif %}

<ul class="dirs">{% for d in subdirs %}
<li><a href="/panel/nueva?dir={{ (rel ~ '/' ~ d).lstrip('/')|urlencode }}{{ aa }}"
  >{{ d }}/</a></li>
{% else %}<li style="opacity:.55;padding:.65rem .8rem">Sin subcarpetas.</li>{% endfor %}</ul>
</main>"""

PANEL_DESCARGABLES = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Que se puede descargar</h1>
<p class="migas"><a href="/panel/{{ gal['slug'] }}">&larr; {{ gal['nombre'] }}</a></p>
<p class="nota">Marcadas: se pueden descargar. Sin marcar, el cliente las ve
igual en la galeria, pero no las puede bajar aunque la galeria tenga
descargas permitidas.</p>
<form method="post" action="/panel/{{ gal['slug'] }}/descargables">
<div class="tapas descargables">
{% for f in fotos %}<label><input type="checkbox" name="si" value="{{ f['id'] }}"
  {{ 'checked' if f['descargable'] }}><img src="/panel/p/{{ f['id'] }}"
  loading="lazy" alt=""></label>{% endfor %}
</div>
<p class="guardar"><button class="btn">Guardar</button></p>
</form>
</main>"""

SIN_CATALOGO = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Eventos</h1><p>Todavia no hay catalogo en la Pi.</p>
<p style="opacity:.55">Se genera en la notebook, desde photos_v2.db, y se manda por
Tailscale:</p>
<p><code>python exportar_catalogo.py --enviar</code></p>
<p><a href="/panel">Volver al panel</a></p></main>"""

PANEL_REVISION_INDICE = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Revision para Instagram</h1>
<p class="migas">Las selecciones que arma Claude cada noche. Tu decides que pasa a edicion:
lo que pasa queda en <code>_para_publicar/&lt;trabajo&gt;/aprobadas</code> del NAS.</p>
{% if not trabajos %}<p>No hay selecciones todavia.</p>{% endif %}
<style>
.revcat li{display:flex;align-items:center;gap:.5rem;border-bottom:1px solid var(--linea)}
.revcat li a{flex:1;min-width:0;border-bottom:0}
.revcat form input{width:8.5rem;font-size:.75rem;padding:.25rem .4rem;background:transparent;
  color:inherit;border:1px solid var(--linea);border-radius:4px}
@media (max-width:700px){.revcat form{display:none}}
</style>
{% for cat, ts in secciones %}
<h3>{{ cat }} &middot; {{ ts|length }}</h3>
<ul class="evs revcat">
{% for t in ts %}<li><a href="/panel/revision/{{ t.url }}">
<span class="fec">{{ t.fecha }}</span>
<span class="nom">{{ t.nombre }}</span>
<span class="n">{% if t.faltan %}<b>{{ t.faltan }} por revisar</b> &middot; {% endif %}{{ t.pasa }} pasan &middot; {% if t.buenas %}{{ t.buenas }} buenas &middot; {% endif %}{{ t.no }} no</span>
</a>
<form method="post" action="/panel/revision-categoria"><input type="hidden" name="t" value="{{ t.nombre }}">
<input name="c" list="cats" placeholder="Mover a..." aria-label="Categoria de {{ t.nombre }}" onchange="this.form.submit()"></form>
</li>{% endfor %}
</ul>
{% endfor %}
<datalist id="cats">{% for cat, _ in secciones %}<option value="{{ cat }}">{% endfor %}</datalist>
</main>"""

PANEL_REVISION = PANEL_CSS + PANEL_BARRA + """<style>
/* Una foto bajo la otra, y a la derecha la barra con las que pasan: lo que se va a subir de esta
   fecha, a la vista mientras se revisa (pedido de Renzo, 7/10/2026). En el telefono es una tira abajo. */
.lay{display:grid;grid-template-columns:minmax(0,1fr) 230px;gap:1.6rem;align-items:start}
.rev{display:flex;flex-direction:column;gap:1.6rem;max-width:1150px}
.rev figure{margin:0;background:rgba(127,127,127,.08);border-radius:8px;overflow:hidden;border:3px solid transparent}
.rev figure.si{border-color:#2e9e4f}.rev figure.no{border-color:#c0392b;opacity:.55}
.rev figure.buena{border-color:#d4a017}
.rev img{width:100%;display:block;cursor:zoom-in}
.rev figcaption{padding:.6rem .8rem;font-size:.9rem;line-height:1.35}
.rev .cap{font-style:italic;opacity:.85;margin:.3rem 0}
.rev .botones{display:flex;gap:.5rem;margin-top:.5rem}
.rev .botones button{flex:1;padding:.6rem;border:0;border-radius:6px;font-size:1rem;cursor:pointer}
.rev .bsi{background:#2e9e4f;color:#fff}.rev .bno{background:#c0392b;color:#fff}
.rev .bbuena{background:#d4a017;color:#1a1a1a}
.apunte{white-space:pre-wrap;font-size:.9rem;opacity:.8}
#barra{position:sticky;top:1rem;max-height:calc(100vh - 2rem);overflow-y:auto}
#barra h3{margin-top:0}
#barra a{display:block;border:0;margin-bottom:.5rem}
#barra img{width:100%;display:block;border-radius:4px}
#barra .vacio{font-size:.85rem;opacity:.6}
@media (max-width:800px){
  .lay{display:block}
  #barra{position:fixed;left:0;right:0;bottom:0;top:auto;max-height:none;overflow-x:auto;overflow-y:hidden;
    display:flex;align-items:center;gap:.4rem;padding:.4rem .6rem;background:var(--noche);
    border-top:1px solid var(--linea);z-index:20}
  #barra h3{margin:0;white-space:nowrap;font-size:.65rem}
  #barra #lista{display:flex;gap:.4rem}
  #barra a{margin:0;flex:none}
  #barra img{height:56px;width:auto}
  main{padding-bottom:6rem}
}
</style><main>
<h1 class="tit">{{ nombre }}</h1>
<p class="migas"><a href="/panel/revision">&larr; revision</a> &middot;
<span id="resumen">{{ pasan }} pasan &middot; {{ faltan ~ ' por revisar' if faltan else 'revision completa' }}</span>{% if fotos_r %} &middot; <a href="#renato">top de Renato</a>{% endif %}</p>
{% macro tarjeta(f, s) %}
<figure id="{{ 'r' if s else 'f' }}{{ f.n }}" class="{{ f.veredicto }}"
  data-orig="{{ f.original }}" data-img="/panel/revision-foto/{{ url }}/{{ f.n }}{{ '?s=' ~ s if s }}">
<a href="/panel/revision-foto/{{ url }}/{{ f.n }}?full=1{{ '&s=' ~ s if s }}" target="_blank" rel="noopener">
<img loading="lazy" src="/panel/revision-foto/{{ url }}/{{ f.n }}{{ '?s=' ~ s if s }}" alt="{{ f.archivo }}"></a>
<figcaption><b>{{ f.n }}.</b> {{ f.vista or f.motivo }}
{% if f.caption %}<div class="cap">&ldquo;{{ f.caption }}&rdquo;</div>{% endif %}
<div style="opacity:.6;font-size:.8rem">{{ f.archivo }}</div>
<div class="botones">
<button class="bsi" onclick="votar('{{ s }}', {{ f.n }}, 'pasa')">Pasa</button>
<button class="bbuena" onclick="votar('{{ s }}', {{ f.n }}, 'buena')" title="Buena foto, pero no la subiria">Buena, no la subo</button>
<button class="bno" onclick="votar('{{ s }}', {{ f.n }}, 'no')">No pasa</button>
</div></figcaption></figure>
{% endmacro %}
<div class="lay"><div>
{% if fotos %}<h3>Seleccion de Claude &middot; {{ fotos|length }} del carrete</h3>
{% if apunte %}<p class="apunte">{{ apunte }}</p>{% endif %}
<div class="rev">{% for f in fotos %}{{ tarjeta(f, '') }}{% endfor %}</div>{% endif %}
{% if fotos_r %}<h3 id="renato">Top {{ fotos_r|length }} de la seleccion de Renato{% if de_r %} &middot; de sus {{ de_r }}{% endif %}</h3>
<div class="rev">{% for f in fotos_r %}{{ tarjeta(f, 'renato') }}{% endfor %}</div>{% endif %}
</div>
<aside id="barra"><h3>Para subir &middot; <span id="nsub">0</span></h3><div id="lista"></div></aside>
</div></main>
<script>
function pintar() {
  // las que pasan, sin repetir: una misma foto puede estar en las dos secciones
  const vistas = new Set(), lista = document.getElementById('lista');
  lista.innerHTML = '';
  document.querySelectorAll('.rev figure.si').forEach(f => {
    if (vistas.has(f.dataset.orig)) return;
    vistas.add(f.dataset.orig);
    const a = document.createElement('a'), im = document.createElement('img');
    a.href = '#' + f.id; im.src = f.dataset.img; im.loading = 'lazy'; im.alt = '';
    a.appendChild(im); lista.appendChild(a);
  });
  document.getElementById('nsub').textContent = vistas.size;
  if (!vistas.size) lista.innerHTML = '<p class="vacio">Lo que marques Pasa aparece aca.</p>';
}
function votar(s, n, v) {
  fetch('/panel/revision/{{ url }}/' + n + (s ? '?s=' + s : ''), {method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'veredicto=' + v})
  .then(r => r.json()).then(j => {
    document.getElementById((s ? 'r' : 'f') + n).className = j.clase;
    document.getElementById('resumen').textContent =
      j.pasan + ' pasan' + (j.faltan ? ' · ' + j.faltan + ' por revisar' : ' · revision completa');
    pintar();
  });
}
pintar();
</script>"""

PANEL_EVENTOS = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Eventos del curador</h1>
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

PANEL_EVENTO = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">{{ ev['nombre'] }}</h1>
<p class="migas"><a href="/panel/eventos">&larr; eventos</a>
{% if ev['coleccion'] %} / {{ ev['coleccion'] }}{% endif %}
&middot; {{ (ev['inicio'] or '')[:10] }} &middot; {{ ev['fotos'] }} fotos</p>

<form class="publicar" method="post" action="/panel/eventos/{{ ev['id'] }}/publicar">
<input name="nombre" value="{{ ev['nombre'] or '' }}" placeholder="Nombre de la galeria">
<button class="btn">Publicar las {{ ev['fotos'] }} como galeria nueva</button>
</form>

{% if gals %}<form class="publicar" style="margin-top:.6rem" method="post"
  action="" id="fsumar">
<input type="hidden" name="evento" value="{{ ev['id'] }}">
<label style="font-size:.85rem;opacity:.7">o sumarlas a una que ya existe
<select id="destino">
{% for g in gals %}<option value="{{ g['slug'] }}">{{ g['nombre'] }}</option>{% endfor %}
</select></label>
<button class="btn hueco"
  onclick="document.getElementById('fsumar').action =
    '/panel/' + document.getElementById('destino').value + '/agregar'"
  >Agregar las {{ ev['fotos'] }} fotos</button>
<span style="opacity:.5;font-size:.85rem">Las repetidas se saltean.</span>
</form>{% endif %}

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

PANEL_TEXTOS = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Textos de las paginas</h1>
<p style="opacity:.55;font-size:.9rem;max-width:60ch">Esto reemplaza el texto
del sitio. Un campo vacio esconde ese bloque entero. Los precios no se escriben
aca: van por evento, en los ajustes de cada galeria.</p>
<p><a class="btn" href="/">Ver la portada</a>
<a class="btn hueco" href="/eventos">Ver eventos</a>
<a class="btn hueco" href="/sobre-mi">Ver sobre mi</a></p>
<form method="post" action="/panel/textos">
{% for grupo, campos in CAMPOS %}<h3>{{ grupo }}</h3>
{% for clave, etiqueta, filas, defecto in campos %}
<label class="campo"><span>{{ etiqueta }}</span>
{% if filas > 1 %}<textarea name="{{ clave }}" rows="{{ filas }}">{{ T[clave] }}</textarea>
{% else %}<input name="{{ clave }}" value="{{ T[clave] }}">{% endif %}</label>
{% endfor %}{% endfor %}
<p style="position:sticky;bottom:1rem;margin:2rem 0 0">
<button class="btn">Guardar los textos</button></p>
</form>
<form method="post" action="/panel/textos/restaurar" style="margin:2.5rem 0 1rem"
  onsubmit="return confirm('Volver TODOS los textos a los originales?')">
<button class="btn mal">Volver a los textos originales</button></form>
</main>"""

PANEL_LOGOS = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Logos de marca de agua</h1>
<p style="opacity:.55;font-size:.9rem;max-width:62ch">Los logos se suben aca una
vez y despues se eligen por galeria, hasta {{ max_logos }}. Van debajo del texto
de la marca, en la misma diagonal. Un PNG con fondo transparente es lo que mejor
queda: el fondo blanco de un JPG se ve como un cuadrado.</p>
{% if error %}<p class="aviso">{{ error }}</p>{% endif %}
<form method="post" action="/panel/logos" enctype="multipart/form-data"
  class="publicar">
<input type="file" name="logo" accept="image/*" required>
<button class="btn">Subir logo</button>
</form>
{% if logos %}<div class="tapas logos" style="margin:1.4rem 0">
{% for n in logos %}<figure style="margin:0;text-align:center">
<img src="/panel/logo/{{ n|urlencode }}" alt="{{ n }}"
  style="width:98px;height:66px;object-fit:contain;background:#1a1a1a;
  padding:.3rem;border:1px solid #2f2f2f;display:block">
<figcaption style="font-size:.7rem;opacity:.5;margin:.3rem 0;max-width:98px;
  overflow:hidden;text-overflow:ellipsis">{{ n }}</figcaption>
<form method="post" action="/panel/logos/borrar"
  onsubmit="return confirm('Borrar {{ n }}? Las galerias que lo usan quedan sin el.')">
<input type="hidden" name="nombre" value="{{ n }}">
<button class="btn mal" style="font-size:.62rem;padding:.25rem .6rem"
  >borrar</button></form>
</figure>{% endfor %}</div>
{% else %}<p style="opacity:.4">Todavia no subiste ninguno.</p>{% endif %}
</main>"""

PANEL_SUBIR = PANEL_CSS + PANEL_BARRA + """<main>
<h1 class="tit">Subir fotos</h1>
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
<a class="btn hueco"
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

PANEL_GAL = PANEL_CSS + PANEL_BARRA + """<main>
<div class="cab"><h1 class="tit">{{ gal['nombre'] }}</h1>
<span class="chips">{{ estado(gal, hoy) }}</span></div>
<p class="migas"><a href="/panel">&larr; galerias</a> &middot; {{ total }} fotos
&middot; {{ con_raw }} con RAW al lado
{% if con_raw < total %}<form method="post" action="/panel/{{ gal['slug'] }}/raw"
  style="display:inline;margin-left:.5rem"><button class="btn hueco"
  onclick="this.textContent='Buscando...'">Buscar RAW de nuevo</button></form>{% endif %}</p>
{% if request.args.get('raw_de') %}<p class="nota">Revise {{ request.args.get('raw_de') }}
fotos sin RAW: {{ request.args.get('raw') }} lo encontraron ahora.</p>{% endif %}

{% if gal['expira'] and gal['expira'] < hoy %}
<p class="aviso">Vencio el {{ gal['expira'] }}: el cliente ya no la ve.
Cambiale la fecha aca abajo para reabrirla.</p>{% endif %}
{% if request.args.get('limpiadas') %}
<p class="nota">Cambio la marca: borre {{ request.args.get('limpiadas') }}
miniaturas viejas del cache. Las nuevas se generan solas.</p>{% endif %}

{# lo que se hace todos los dias, junto y arriba: copiar el link, tocar las
   fotos, y mirarla como la ve el cliente #}
<div class="acciones">
<div class="link">
<input id="lnk" readonly value="{{ request.url_root }}g/{{ gal['slug'] }}">
<button class="btn" onclick="
  const i=document.getElementById('lnk'); i.select();
  navigator.clipboard.writeText(i.value).then(()=>this.textContent='Copiado');
">Copiar</button>
</div>
<a class="btn hueco" href="/panel/{{ gal['slug'] }}/fotos">Fotos ({{ total }})</a>
<a class="btn hueco" href="/g/{{ gal['slug'] }}">Ver como cliente</a>
{% if gal['venta'] %}<a class="btn hueco" href="/eventos">Verla en /eventos</a>{% endif %}
</div>

{% if total %}<p class="nota">Calentado (miniaturas del panel):
<b>{{ calentadas }}/{{ total }}</b>
{% if corriendo %} &middot; calentando en {{ donde }}, esto se actualiza solo...
<script>setTimeout(function(){location.reload()}, 8000)</script>
{% elif calentadas < total %} &middot;
<form style="display:inline" method="post" action="/panel/{{ gal['slug'] }}/calentar">
<button class="btn hueco" style="padding:.2rem .65rem;font-size:.82rem">Calentar ahora</button>
</form>{% endif %}
&middot; <a href="/panel/{{ gal['slug'] }}/descargables">Que se puede
descargar ({{ descargables }}/{{ total }})</a></p>{% endif %}

<form method="post" action="/panel/{{ gal['slug'] }}/ajustes">

<h3>Nombre</h3>
<div class="campos">
<label class="campo ancho" style="max-width:32rem"><span>Como se llama la galeria
&mdash; lo ve el cliente arriba y en /eventos</span>
<input name="nombre" value="{{ gal['nombre'] }}" maxlength="120"></label>
</div>

<h3>Acceso</h3>
<div class="campos">
<label class="campo"><span>Contrasena</span>
<input name="clave" type="text" autocomplete="off" placeholder="{{
  'Tiene una — escribi otra para cambiarla' if gal['clave_hash']
  else 'Sin contrasena — escribi una para ponerla' }}"></label>
{% if gal['clave_hash'] %}<label class="chk">
<input type="checkbox" name="sacar_clave" value="1"> Sacarle la contrasena</label>{% endif %}
<label class="campo"><span>Vence el (vacio = sin limite)</span>
<input type="date" name="expira" value="{{ gal['expira'] or '' }}"></label>
</div>

<h3>Entrega</h3>
<div class="campos">
<label class="campo"><span>Texto de la marca de agua</span>
<select name="marca_texto">
{% for m in marcas %}<option value="{{ m }}"
  {{ 'selected' if m == gal['marca_texto'] }}>{{ m }}</option>{% endfor %}
<option value="" {{ 'selected' if gal['marca_texto'] == '' }}>(sin texto, solo
el logo)</option>
</select></label>
<label class="chk"><input type="checkbox" name="sin_marca" value="1"
  {{ 'checked' if gal['marca'] == 0 }}> Sin marca de agua</label>
<label class="chk"><input type="checkbox" name="descargas" value="1"
  {{ 'checked' if gal['descargas'] }}> Permitir descargas</label>
<label class="chk"><input type="checkbox" name="comentarios" value="1"
  {{ 'checked' if gal['comentarios'] }}> Permitir comentarios</label>
<label class="chk"><input type="checkbox" name="raw_descarga" value="1"
  {{ 'checked' if gal['raw_descarga'] }}> Permitir descargar RAW</label>
<label style="font-size:.85rem;opacity:.7">Tipo
<select name="tipo">
<option value="">sin rotulo</option>
{% for t in tipos %}<option value="{{ t }}"
  {{ 'selected' if t == gal['tipo'] }}>{{ tipo_nombre[t] }}</option>{% endfor %}
</select></label>
{% set la = (gal['listas_auto'] or '').splitlines() %}
<div style="flex-basis:100%;font-size:.85rem"><span style="opacity:.7">Listas que se
le crean al cliente:</span>
{% for n in sugeridas %}<label class="chk" style="display:inline-flex;margin-right:.8rem">
<input type="checkbox" name="lista_auto" value="{{ n }}" {{ 'checked' if n in la }}>
{{ n }}</label>{% endfor %}
<input name="lista_auto_otras" placeholder="otras, separadas por coma"
  value="{{ la|reject('in', sugeridas)|join(', ') }}" style="min-width:14rem"></div>
<label style="font-size:.85rem;opacity:.7">Edicion
<select name="ent_edicion">
<option value="">no la menciono</option>
{% for e in ediciones %}<option value="{{ e }}"
  {{ 'selected' if e == gal['ent_edicion'] }}>{{ T['edicion_' ~ e ~ '_nom'] }}</option>
{% endfor %}
</select></label>
{% if gal['ent_edicion'] %}<input type="hidden" name="ent_edicion_previa"
  value="{{ gal['ent_edicion'] }}">
<div class="campo edlineas"><span>Que dice esa edicion</span>
<div class="edwrap">{% for l in ed_items %}<label class="chk">
<input type="checkbox" name="ent_deja" value="{{ l }}"
  {{ 'checked' if l not in ed_quita }}> <span>{{ l }}</span></label>{% endfor %}</div>
<p class="nota" style="margin:.35rem 0 0">Lo que destildes se esconde solo en
esta galeria. El preset general no se toca.</p></div>{% endif %}
{% set m, x = entf['ent_resolucion'] %}
<div class="campo entop" style="max-width:19rem"><span>Resolucion</span>
<select name="ent_resolucion_modo">
<option value="" {{ 'selected' if m == '' }}>General: {{ T['ent_resolucion'] or '(vacia)' }}</option>
{% for r in resoluciones %}<option {{ 'selected' if m == r }}>{{ r }}</option>{% endfor %}
<option value="otro" {{ 'selected' if m == 'otro' }}>Personalizada</option>
<option value="{{ oculto }}" {{ 'selected' if m == oculto }}>No mostrar</option>
</select>
<div class="eo eo-otro"><input name="ent_resolucion_otro"
  value="{{ x if m == 'otro' else '' }}" placeholder="vacio = no aparece"></div>
</div>
{% set m, x = entf['ent_metodo'] %}
<div class="campo entop" style="max-width:19rem"><span>Metodos</span>
<select name="ent_metodo_modo">
<option value="" {{ 'selected' if m == '' }}>General: {{ T['ent_metodo'] or '(vacio)' }}</option>
<option value="elegir" {{ 'selected' if m == 'elegir' }}>Elegir</option>
<option value="otro" {{ 'selected' if m == 'otro' }}>Personalizado</option>
<option value="{{ oculto }}" {{ 'selected' if m == oculto }}>No mostrar</option>
</select>
<div class="eo eo-elegir">{% for mt in metodos %}<label class="chk"><input
  type="checkbox" name="ent_metodo_op" value="{{ mt }}"
  {{ 'checked' if m == 'elegir' and mt in x }}> {{ mt }}</label>{% endfor %}</div>
<div class="eo eo-otro"><input name="ent_metodo_otro"
  value="{{ x if m == 'otro' else '' }}" placeholder="vacio = no aparece"></div>
</div>
{% set m, x = entf['ent_plazo'] %}
<div class="campo entop" style="max-width:19rem"><span>Plazos</span>
<select name="ent_plazo_modo">
<option value="" {{ 'selected' if m == '' }}>General: {{ T['ent_plazo'] or '(vacio)' }}</option>
<option value="dias" {{ 'selected' if m == 'dias' }}>Por dias</option>
<option value="otro" {{ 'selected' if m == 'otro' }}>Personalizado</option>
<option value="{{ oculto }}" {{ 'selected' if m == oculto }}>No mostrar</option>
</select>
<div class="eo eo-dias"><input type="number" min="1" name="ent_plazo_dias"
  value="{{ x if m == 'dias' else '' }}"> dias</div>
<div class="eo eo-otro"><input name="ent_plazo_otro"
  value="{{ x if m == 'otro' else '' }}" placeholder="vacio = no aparece"></div>
</div>
<label class="campo ancho" style="max-width:44rem"><span>Detalles &mdash; texto
libre para esta galeria. Vacio = los generales, y si los dos estan vacios la
fila no aparece.</span>
<textarea name="ent_detalles" rows="3"
  placeholder="{{ T['ent_detalles'] }}">{{ gal['ent_detalles'] or '' }}</textarea></label>
</div>
<p class="nota">Logos de la marca &mdash; hasta {{ max_logos }}, van debajo del
texto &middot; <a href="/panel/logos">subir o borrar</a></p>
{% if todos_logos %}<div class="tapas logos">
{% for n in todos_logos %}<label><input type="checkbox" name="logos" value="{{ n }}"
  {{ 'checked' if n in puestos }}><img src="/panel/logo/{{ n|urlencode }}"
  loading="lazy" alt="{{ n }}"></label>{% endfor %}
</div>
{% if combos %}<p class="nota">Combinaciones que ya usaste &mdash; toca una para
ponerla:</p>
<div class="combos">
{% for c in combos %}<button type="button" class="combo"
  data-logos="{{ c|join('|') }}" title="{{ c|join(' + ') }}">
{% for n in c %}<img src="/panel/logo/{{ n|urlencode }}" loading="lazy" alt="">{% endfor %}
</button>{% endfor %}</div>{% endif %}
{% else %}<p class="nota">Todavia no subiste ninguno.
<a href="/panel/logos">Subir uno</a>.</p>{% endif %}

<h3>Firma de entrega</h3>
<p class="nota">El logo que se estampa en la foto que se <b>entrega</b>: a color
y opaco, una sola vez en una esquina. Es otra cosa que la marca de agua, que
solo protege lo que se ve sin pagar.</p>
{% if todos_logos %}<div class="firmabox">
<div class="publicar firmacfg" id="firmacfg">
<label style="font-size:.85rem;opacity:.7">Logo
<select name="firma">
<option value="">sin firma: se entrega el archivo tal cual</option>
{% for n in todos_logos %}<option value="{{ n }}"
  {{ 'selected' if n == gal['firma'] }}>{{ n }}</option>{% endfor %}
</select></label>
<span style="font-size:.85rem;opacity:.7">Donde
<span class="matriz">{% for pp in posiciones %}<label title="{{ pp }}">
<input type="radio" name="firma_pos" value="{{ pp }}"
  {{ 'checked' if pp == (gal['firma_pos'] or firma_pos) }}><span></span></label>
{% endfor %}</span></span>
<label style="font-size:.85rem;opacity:.7">Tamano
<input type="number" name="firma_tam" min="5" max="40" style="width:4.5rem"
  value="{{ gal['firma_tam'] or firma_tam }}"> % del ancho</label>
</div>
<div class="firmaprev">{% for m in muestras_firma %}<img alt=""
  data-base="/panel/{{ gal['slug'] }}/firma?foto={{ m['id'] }}"
  data-ver="asi se entrega &middot; {{ nombre(m['ruta']) }}">{% endfor %}</div>
</div>
<p class="nota">Las muestras se actualizan al mover los controles, sin guardar.
Toca una para verla grande; con las flechas pasas a las otras.</p>
{% else %}<p class="nota">Subi un logo primero en
<a href="/panel/logos">logos</a>.</p>{% endif %}

<h3>Vidriera y venta</h3>
<div class="campos">
<label class="campo"><span>Fecha del evento</span>
<input type="date" name="fecha" value="{{ gal['fecha'] or '' }}"></label>
<label class="chk"><input type="checkbox" name="venta" value="1"
  {{ 'checked' if gal['venta'] }}> En venta &mdash; sale en /eventos y se puede
comprar</label>
<label class="campo ancho" style="max-width:26rem">
<span>Precios de este evento &mdash; una linea por tramo: cuantas fotos y cuanto
salen. Vacio = los de siempre.</span>
<textarea name="precios" rows="4"
  placeholder="{{ precios_default }}">{{ gal['precios'] or '' }}</textarea></label>
</div>
<p class="nota" style="margin-top:-1.2rem">Queda:
{% for c, p in tarifas %}{{ c }} fotos {{ pesos(p) }}{% if not loop.last %}
&middot; {% endif %}{% endfor %}</p>

<p class="nota">Portada &mdash; la tapa que ve el cliente y la que sale en
/eventos</p>
<div class="tapas">
<label><input type="radio" name="portada" value=""
  {{ 'checked' if not gal['portada_foto_id'] }}><span class="nada">sin<br>portada</span></label>
{% for f in primeras %}<label><input type="radio" name="portada" value="{{ f['id'] }}"
  {{ 'checked' if f['id'] == gal['portada_foto_id'] }}><img src="/panel/p/{{ f['id'] }}"
  loading="lazy" alt=""></label>{% endfor %}
</div>

<p class="guardar"><button class="btn">Guardar los ajustes</button></p>
</form>

{% if gal['comentarios'] %}<p style="margin:1.5rem 0 0"><a class="btn"
  href="/panel/{{ gal['slug'] }}/comentarios">Ver los comentarios</a></p>{% endif %}
<h3>Quien entro</h3>
<table><tr><th>Email</th><th>Ultima visita</th><th>Marcadas</th>
<th>Bajadas</th><th>Listas</th></tr>
{% for c in clientes %}<tr><td><a href="/panel/{{ gal['slug'] }}/cliente/{{ c['id'] }}"
  style="color:#eee">{{ c['email'] }}</a></td><td>{{ c['visto'] }}</td>
<td>{{ c['marcadas'] }}{% if c['repetidas'] %}
  <span class="chip no" title="fotos que estan en mas de una lista">{{ c['repetidas'] }}
  repetida{{ '' if c['repetidas'] == 1 else 's' }}</span>{% endif %}</td>
<td>{{ c['bajadas'] }}{% if c['bajadas_fotos'] != c['bajadas'] %}
  <span style="opacity:.45">({{ c['bajadas_fotos'] }} fotos)</span>{% endif %}</td>
<td>{% if c['bajadas_fotos'] %}<a class="btn"
  style="font-size:.75rem;padding:.2rem .6rem;margin:.1rem .25rem .1rem 0"
  href="/panel/{{ gal['slug'] }}/cliente/{{ c['id'] }}/descargadas"
  title="Se llena sola con lo que bajo">Descargadas ({{ c['bajadas_fotos'] }})</a>{% endif %}
{% for l in listas_de.get(c['id'], []) %}<a class="btn"
  style="font-size:.75rem;padding:.2rem .6rem;margin:.1rem .25rem .1rem 0"
  href="/panel/{{ gal['slug'] }}/lista/{{ l['id'] }}">{{ l['nombre'] }} ({{ l['n'] }})</a>{% else
  %}<span style="opacity:.4">-</span>{% endfor %}</td></tr>
{% else %}<tr><td colspan="5">Nadie todavia.</td></tr>
{% endfor %}</table>

{% if miradas %}<h3>Que miro cada uno</h3>
<p class="nota">Cuenta por lo bajo: la foto que el navegador ya tiene guardada
no la vuelve a pedir, asi que pudo ver mas de lo que dice, nunca menos.</p>
<table><tr><th>Email</th><th>Grupo</th><th>Fotos</th><th>Vio en la grilla</th>
<th>Abrio en grande</th><th>Ultima</th></tr>
{% for m in miradas %}<tr><td>{{ m['quien'] }}</td>
<td>{{ m['grupo'] or '-' }}</td><td>{{ m['total'] }}</td>
<td>{{ m['grilla'] }}{% if m['total'] %} <span style="opacity:.45">({{
  100 * m['grilla'] // m['total'] }}%)</span>{% endif %}</td>
<td>{{ m['visor'] }}{% if m['total'] %} <span style="opacity:.45">({{
  100 * m['visor'] // m['total'] }}%)</span>{% endif %}</td>
<td>{{ m['fin'] }}</td></tr>{% endfor %}</table>{% endif %}

<h3>Fotos descargadas <span style="opacity:.4;font-size:.8rem;font-weight:400">
una fila por bajada</span></h3>
<table><tr><th></th><th>Archivo</th><th>Quien</th><th>Cuando</th></tr>
{% for f in bajadas %}<tr>
<td style="width:6.5rem"><img src="/panel/p/{{ f['id'] }}" loading="lazy" alt=""
  data-ver="{{ nombre(f['ruta']) }} · {{ f['quien'] }}"
  style="width:6rem;aspect-ratio:3/2;object-fit:cover;display:block;border-radius:4px"></td>
<td><code>{{ nombre(f['ruta']) }}</code>{% if f['cual'] == 'raw'
  %} <span style="opacity:.5">RAW</span>{% endif %}</td>
<td>{{ f['quien'] }}</td>
<td style="opacity:.6">{{ f['cuando'][:16].replace('T',' ') }}</td></tr>
{% else %}<tr><td colspan="4">Todavia no bajaron ninguna.</td></tr>{% endfor %}
</table>
<h3>Fotos elegidas
<a class="btn" style="font-size:.78rem;padding:.28rem .7rem;margin-left:.5rem"
  href="/panel/{{ gal['slug'] }}/seleccion.csv">Bajar CSV</a></h3>
<table><tr><th></th><th>Archivo</th><th>Votos</th><th>Quien</th><th>Lista</th></tr>
{% for f in elegidas %}<tr>
<td style="width:6.5rem"><img src="/panel/p/{{ f['id'] }}" loading="lazy" alt=""
  data-ver="{{ nombre(f['ruta']) }}"
  style="width:6rem;aspect-ratio:3/2;object-fit:cover;display:block;border-radius:4px"></td>
<td><code>{{ nombre(f['ruta']) }}</code></td>
<td>{{ f['votos'] }}</td><td>{{ f['quienes'] }}</td>
<td style="opacity:.6">{{ f['listas'] }}</td></tr>
{% else %}<tr><td colspan="5">Sin marcas todavia.</td></tr>{% endfor %}
</table>
<script>
// las muestras de la firma se rearman con lo que dicen los controles, asi se
// prueba una posicion sin tener que guardar y volver a mirar
const fcfg = document.getElementById("firmacfg");
if (fcfg) {
  const previas = [...document.querySelectorAll(".firmaprev img")];
  const refrescarFirma = () => {
    const logo = fcfg.querySelector('[name="firma"]').value;
    const pos = fcfg.querySelector('[name="firma_pos"]:checked');
    const tam = fcfg.querySelector('[name="firma_tam"]').value;
    previas.forEach(im => {
      im.hidden = !logo;
      if (!logo) return;
      const u = new URL(im.dataset.base, location.origin);
      u.searchParams.set("logo", logo);
      if (pos) u.searchParams.set("pos", pos.value);
      u.searchParams.set("tam", tam);
      im.src = u;
    });
  };
  fcfg.addEventListener("change", refrescarFirma);
  refrescarFirma();
}

document.querySelectorAll(".combo").forEach(b => b.onclick = () => {
  const puestos = new Set(b.dataset.logos.split("|"));
  document.querySelectorAll('input[name="logos"]')
    .forEach(i => i.checked = puestos.has(i.value));
});
</script>
<form method="post" action="/panel/{{ gal['slug'] }}/borrar" style="margin:2.5rem 0 1rem"
  onsubmit="return confirm('Borrar la galeria y los me-gusta de los clientes?\\nLas fotos del disco no se tocan.')">
<button class="btn mal">Borrar esta galeria</button>
</form></main>""" + PANEL_VISOR


# ------------------------------------------------------------------------ CLI

def agregar_fotos(cx, gid, rutas, verificar=True):
    """Mete rutas en una galeria, saltando las que ya estan.

    Lo usan el alta y el "agregar fotos" del panel: si el bucle estuviera
    duplicado, arreglar un caso dejaria el otro roto. El orden sigue desde la
    ultima foto, asi lo que agregas despues queda al final y no se mezcla.

    verificar=False lo usa la publicacion desde el catalogo: esas rutas ya
    pasaron por el pipeline y vienen filtradas por danado=0, y abrir 7 mil
    JPEG para revalidarlos son minutos de disco al pedo.
    """
    ya = {r["ruta"] for r in cx.execute(
        "SELECT ruta FROM foto WHERE galeria_id = ?", (gid,))}
    orden = cx.execute("SELECT COALESCE(MAX(orden), -1) o FROM foto "
                       "WHERE galeria_id = ?", (gid,)).fetchone()["o"] + 1
    puestas, faltan, repes = 0, [], 0
    with tanda_raw():
        for r in rutas:
            ruta = traducir_ruta(r)
            if ruta in ya:
                repes += 1
                continue
            if not dentro_de_raiz(ruta) or not os.path.exists(ruta):
                faltan.append(r)
                continue
            if verificar and not abrible(ruta):
                faltan.append(r)
                continue
            cx.execute("INSERT INTO foto (galeria_id, ruta, raw, orden, tomada) "
                       "VALUES (?, ?, ?, ?, ?)",
                       (gid, ruta, buscar_raw(ruta) if verificar else None, orden,
                        fecha_exif(ruta)))
            ya.add(ruta)
            orden += 1
            puestas += 1
    return puestas, faltan, repes


def crear_galeria(cx, nombre, rutas, verificar=True):
    """Alta de galeria. La usan igual la consola y el panel web."""
    slug = secrets.token_urlsafe(12)
    cx.execute("INSERT INTO galeria (slug, nombre, creada) VALUES (?, ?, ?)",
               (slug, nombre, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    gid = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()["id"]
    puestas, faltan, _repes = agregar_fotos(cx, gid, rutas, verificar)
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


def partidos(slug, csv_path):
    """Marca cada foto con su partido, leyendo el CSV de separar_partidos.py.

    Empareja por NOMBRE de archivo y no por ruta: el CSV lo escribe Windows
    con rutas de la notebook, y aca las fotos viven en otra carpeta. Los
    nombres de camara son unicos dentro del evento, asi que alcanza.

    Las filas con partido -1 quedan sin grupo: aparecen en "todas" y en
    ningun partido, que es lo que son.
    """
    cx = conectar()
    gal = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        sys.exit("No existe la galeria %s" % slug)

    porNombre = {}
    for r in cx.execute("SELECT id, ruta FROM foto WHERE galeria_id = ?", (gal["id"],)):
        porNombre.setdefault(os.path.basename(r["ruta"]), r["id"])

    puestas, sin_foto, sueltas = 0, 0, 0
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for fila in csv.DictReader(fh):
            nombre = os.path.basename((fila.get("archivo") or "").replace("\\", "/"))
            fid = porNombre.get(nombre)
            if fid is None:
                sin_foto += 1
                continue
            p = (fila.get("partido") or "").strip()
            if not p.lstrip("-").isdigit() or int(p) < 0:
                sueltas += 1
                cx.execute("UPDATE foto SET grupo = NULL, grupo_col = NULL "
                           "WHERE id = ?", (fid,))
                continue
            # el CSV numera desde 0; para el cliente el primero es el 1
            cx.execute("UPDATE foto SET grupo = ?, grupo_col = ? WHERE id = ?",
                       ("Partido %d" % (int(p) + 1),
                        (fila.get("colores") or "").replace("/", ","), fid))
            puestas += 1
    cx.commit()
    for g in cx.execute(
            "SELECT grupo, COUNT(*) n FROM foto WHERE galeria_id = ? AND grupo "
            "IS NOT NULL GROUP BY grupo ORDER BY grupo", (gal["id"],)):
        print("  %-12s %5d fotos" % (g["grupo"], g["n"]))
    print("%d fotos agrupadas, %d sin partido, %d filas del CSV que no estan "
          "en la galeria" % (puestas, sueltas, sin_foto))
    cx.close()


def fechas(slug):
    """Llena la hora de captura de una galeria que ya existe.

    Al agregar fotos se guarda sola; esto es para las galerias de antes de que
    la columna existiera. Solo lee la cabecera EXIF, asi que son milisegundos
    por foto y no toca los archivos.
    """
    cx = conectar()
    gal = cx.execute("SELECT id FROM galeria WHERE slug = ?", (slug,)).fetchone()
    if gal is None:
        sys.exit(f"No existe la galeria {slug}")
    fotos = cx.execute("SELECT id, ruta FROM foto WHERE galeria_id = ?",
                       (gal["id"],)).fetchall()
    t0, con = time.perf_counter(), 0
    for i, f in enumerate(fotos, 1):
        d = fecha_exif(f["ruta"])
        if d:
            cx.execute("UPDATE foto SET tomada = ? WHERE id = ?", (d, f["id"]))
            con += 1
        if i % 200 == 0:
            cx.commit()
            print("\r  %d/%d  %.0fs"
                  % (i, len(fotos), time.perf_counter() - t0),
                  end="", flush=True)
    cx.commit()
    cx.close()
    print("\n%d de %d con fecha, en %.0fs"
          % (con, len(fotos), time.perf_counter() - t0))


def calentadas_de(cx, gid):
    """(cuantas estan listas para el cliente, total de la galeria).

    Lista = tiene la miniatura de la grilla Y la del visor. Contar solo la de
    la grilla decia "787/787" mientras el visor seguia frio, que es justo el
    que duele: sin su copia, abrir una foto le cuesta ~1,7 s a la Pi.
    Toma `cx` en vez de usar db(): esto se llama tambien desde el self-check,
    fuera de un contexto de request de Flask.
    """
    gal = cx.execute("SELECT * FROM galeria WHERE id = ?", (gid,)).fetchone()
    if gal is None:
        return 0, 0
    lg, fm = tuple(logos_de(gal)), firma_de(gal)   # una vez, no una por foto
    total = listas = 0
    for r in cx.execute("SELECT ruta FROM foto WHERE galeria_id = ?", (gid,)):
        total += 1
        try:
            if (clave_preview_de(gal, r["ruta"], None, lg, fm).exists()
                    and clave_preview_de(gal, r["ruta"], ANCHO_VISOR, lg, fm).exists()):
                listas += 1
        except OSError:
            pass   # el archivo ya no esta en el disco: no cuenta como calentada
    return listas, total


def _lock_calentando(slug):
    return CACHE / ("calentando-%s.pid" % slug)


def _calentando(slug):
    """True si YA hay un calentado corriendo para esta galeria en este momento."""
    p = _lock_calentando(slug)
    if not p.exists():
        return False
    try:
        pid = int(p.read_text().strip())
    except (ValueError, OSError):
        return False
    return Path("/proc/%d" % pid).exists()


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
            preview_de(gal, f["id"], f["ruta"])
            # la del VISOR, que es otra medida (ANCHO_VISOR) y es la cara:
            # sin esto, abrir cada foto por primera vez le costaba ~1,7 s
            preview_de(gal, f["id"], f["ruta"], ANCHO_VISOR)
            # el panel pide SIEMPRE sin_marca, tenga o no marca la galeria
            # (ver panel_preview): sin esto "calentar" no calienta el panel
            sin_marca(f["ruta"])
        except Exception as e:
            fallaron += 1
            print(f"\n  falla en {f['ruta']}: {e}")
        print(f"\r  {i}/{len(fotos)}  {time.perf_counter() - t0:.0f}s", end="", flush=True)
    print(f"\nListo en {time.perf_counter() - t0:.0f}s"
          + (f", {fallaron} fallaron" if fallaron else ""))


def _declaraciones_top(js):
    """Los nombres declarados con let/const al ras del <script>.

    Son los que comparten scope global: dos iguales son un SyntaxError que no
    rompe una funcion sino el script entero. Se miran solo las lineas sin
    indentar a proposito — las de adentro de una funcion pueden repetirse sin
    problema, y contarlas daria falsos positivos.
    """
    nombres = []
    for linea in js.split("\n"):
        if linea.startswith(("let ", "const ")):
            for parte in linea.split(None, 1)[1].split(";")[0].split(","):
                nom = parte.strip().split("=")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", nom):
                    nombres.append(nom)
    return nombres


def test():
    """ponytail: un solo check que falla si se rompe algo que importa."""
    import tempfile
    global DB, CACHE, RAIZ_FOTOS, RAIZ_DERIVADAS, LOGOS, MUESTRAS
    tmp = Path(tempfile.mkdtemp())
    DB, CACHE = tmp / "t.db", tmp / "cache"
    MUESTRAS = tmp / "portada"     # las fotos de la vidriera de verdad no se tocan
    # las raices apuntan al tmp desde el principio: si apuntaran al disco de
    # verdad, tenerlo desconectado cuelga el self-check entero (su punto de
    # automontaje no falla, espera)
    RAIZ_FOTOS = tmp / "fotos"
    RAIZ_DERIVADAS = tmp / "derivadas"
    (RAIZ_FOTOS / "Eventos").mkdir(parents=True)
    (RAIZ_DERIVADAS / "P").mkdir(parents=True)
    F, D = str(RAIZ_FOTOS), str(RAIZ_DERIVADAS)

    # traduccion de rutas: los tres prefijos que aparecen en la practica
    assert traducir_ruta(r"K:\Eventos\a.jpg") == F + "/Eventos/a.jpg"
    assert traducir_ruta("k:/Eventos/a.jpg") == F + "/Eventos/a.jpg"
    assert traducir_ruta(F + "/Eventos/a.jpg") == F + "/Eventos/a.jpg"
    assert traducir_ruta("Eventos/a.jpg") == F + "/Eventos/a.jpg"
    # una derivada no se toca: sin esto crear_galeria las descartaba todas
    assert traducir_ruta(D + "/P/a.jpg") == D + "/P/a.jpg"

    # nadie sale de las dos raices, ni con `..` ni por vecindad de nombre
    assert dentro_de_raiz(F + "/x.jpg")
    assert dentro_de_raiz(D + "/P/a.jpg")
    assert not dentro_de_raiz("/etc/shadow")
    assert not dentro_de_raiz(F + "/../etc/shadow")
    assert not dentro_de_raiz(D + "/../../etc/shadow")
    assert not dentro_de_raiz(str(tmp / "otra" / "a.jpg"))

    # marca de agua: sale un JPEG y los pixeles cambian de verdad
    orig = tmp / "orig.jpg"
    Image.new("RGB", (2400, 1600), (90, 110, 130)).save(orig)
    dest = tmp / "m.jpg"
    marcar(orig, dest)
    assert dest.exists()
    im = Image.open(dest)
    assert im.width == ANCHO_PREVIEW, im.size
    assert len(im.convert("RGB").getcolors(maxcolors=100000)) > 1, "no marco nada"

    # y el interruptor: la misma foto plana, sin marca, sale plana. Si algun
    # dia sin_marca() empieza a marcar, o /p/ deja de elegirla, esto falla.
    # el texto entra en la clave del cache. Sin esto, cambiar la marca de una
    # galeria devolveria la preview vieja con la marca de antes, en silencio.
    a, b = preview(1, str(orig), MARCAS[0]), preview(1, str(orig), MARCAS[1])
    assert a != b, "el cache no distingue la marca: serviria la vieja"
    assert Image.open(a).tobytes() != Image.open(b).tobytes(), "marco lo mismo"

    # ------------------------------------------------ logos en la marca
    LOGOS = tmp / "logos"
    LOGOS.mkdir()
    Image.new("RGBA", (400, 200), (255, 0, 0, 180)).save(LOGOS / "liga.png")
    Image.new("RGBA", (100, 100), (0, 0, 255, 180)).save(LOGOS / "rb.png")
    (LOGOS / "roto.png").write_bytes(b"no soy un png")
    assert logos_disponibles() == ["liga.png", "rb.png", "roto.png"]

    tira = _tira_logos(("liga.png", "rb.png"), 50)
    assert tira.height == 50 and tira.width > 100, tira.size

    # el padding transparente se recorta antes de escalar: sin esto un PNG
    # exportado en un lienzo cuadrado dibujaba el logo a un cuarto de tamaño
    con_borde = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    con_borde.paste(Image.open(LOGOS / "liga.png"), (80, 280))
    con_borde.save(LOGOS / "conborde.png")
    assert (_tira_logos(("conborde.png",), 50).size
            == _tira_logos(("liga.png",), 50).size), "no recorto el lienzo vacio"

    # un logo oscuro se vuelve claro; uno que ya era blanco no se toca
    assert max(_blanquear(Image.new("RGBA", (9, 9), (20, 20, 20, 255)))
               .getchannel("A").getdata()) < 40, "un logo negro seguiria invisible"
    assert max(_blanquear(Image.new("RGBA", (9, 9), (255, 255, 255, 255)))
               .getchannel("A").getdata()) == 255, "degrado un logo ya blanco"

    # con texto vacio la marca es solo el logo, y sin nada de nada no queda
    # una estampa invisible
    assert _estampa("", 40, ("liga.png",)).height > 40
    assert _estampa("", 40, ()).width > 0
    # un archivo roto se saltea, no tumba la marca de la galeria entera
    assert _tira_logos(("roto.png", "rb.png"), 50) is not None
    assert _tira_logos(("roto.png",), 50) is None
    assert _tira_logos((), 50) is None
    # y no se sale de la carpeta de logos
    assert _tira_logos(("../orig.jpg",), 50) is None

    # la marca tiene que llegar a la opacidad pedida. Sin esto no se notaba
    # que pegarla con paste() la dejaba al 6% en vez de al 24%: seguia
    # "dibujando algo", solo que invisible.
    for ls in ((), ("liga.png",)):
        cm = capa_marca(500, 340, "RB.Deportes_", 0.042, ls)
        pico = max(cm.getchannel("A").getdata())
        assert pico >= ALFA_MARCA - 6, ("marca casi invisible", ls, pico)

    # con logo la marca dibuja algo distinto que sin logo, y el cache lo sabe
    a = preview(1, str(orig), MARCAS[0])
    b = preview(1, str(orig), MARCAS[0], logos=("liga.png",))
    assert a != b, "el cache no distingue los logos: serviria la vieja"
    assert Image.open(a).tobytes() != Image.open(b).tobytes(), "el logo no se dibujo"

    # la firma se ve TAMBIEN en la preview: el comprador tiene que ver la
    # foto como la va a recibir, no una distinta
    sinf = preview(1, str(orig), MARCAS[0], logos=("liga.png",))
    conf = preview(1, str(orig), MARCAS[0], logos=("liga.png",),
                   firma=("rb.png", "arriba-cen", 20))
    assert sinf != conf, "el cache no distingue la firma de la preview"
    ia, ib = Image.open(sinf), Image.open(conf)
    assert ia.size == ib.size
    arr = (ia.width // 3, 0, ia.width * 2 // 3, ia.height // 4)
    aba = (0, ia.height * 3 // 4, ia.width // 3, ia.height)
    assert ia.crop(arr).tobytes() != ib.crop(arr).tobytes(), "no dibujo la firma"
    assert ia.crop(aba).tobytes() == ib.crop(aba).tobytes(), "la firma quedo mal puesta"

    # el visor pide la misma foto mas grande, y con la marca igual de puesta
    chica = preview(1, str(orig), MARCAS[0])
    grande = preview(1, str(orig), MARCAS[0], ANCHO_VISOR)
    assert chica != grande, "el cache no distingue el tamano del visor"
    ig, ic = Image.open(grande), Image.open(chica)
    assert ig.width == ANCHO_VISOR and ic.width == ANCHO_PREVIEW, (ig.size, ic.size)
    # y la grande sigue marcada: seria justo la que conviene robar
    assert len(ig.convert("RGB").getcolors(maxcolors=200000)) > 1, "salio sin marca"

    limpia = Image.open(sin_marca(str(orig)))
    assert limpia.width == ANCHO_MUESTRA, limpia.size
    assert len(limpia.convert("RGB").getcolors(maxcolors=100000)) < 5, "marco de mas"

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
        # el prefijo de derivadas tampoco es una puerta de atras
        assert c.get("/panel/nueva?dir=derivadas/../../etc").status_code == 404
        assert c.get("/panel/nueva?dir=derivadas/../..").status_code == 404

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

    # convencion RAW/Jpeg/Export: publicar RESUELVE sola, no bloquea
    ev = tmp / "Evento1"
    for sub_ in ("RAW", "Jpeg", "Export"):
        (ev / sub_).mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(ev / "Jpeg" / "sinusar.jpg")
    Image.new("RGB", (10, 10)).save(ev / "Export" / "usar.jpg")
    ev_raw = ev / "RAW" / "usar.ARW"
    ev_raw.write_bytes(b"x")

    def _de_export(resp):
        assert resp.status_code == 302, resp.status_code
        slug = resp.headers["Location"].rsplit("/", 1)[-1]
        cx_ = conectar()
        fs = cx_.execute(
            "SELECT ruta, raw FROM foto WHERE galeria_id = "
            "(SELECT id FROM galeria WHERE slug = ?)", (slug,)).fetchall()
        cx_.execute("DELETE FROM galeria WHERE slug = ?", (slug,))
        cx_.commit()
        cx_.close()
        return fs

    with app.test_client() as c:
        # entraste directo a Jpeg: igual publica lo de Export (y encuentra
        # el RAW hermano, en su carpeta separada — la convencion nueva)
        fs = _de_export(c.post("/panel/nueva",
                               data={"nombre": "A", "dir": "Evento1/Jpeg"}))
        assert [Path(f["ruta"]).name for f in fs] == ["usar.jpg"], fs
        assert fs[0]["raw"] == str(ev_raw), "no encontro el RAW hermano"

        # elegiste el evento entero: tambien resuelve a Export, no mezcla Jpeg
        fs = _de_export(c.post("/panel/nueva",
                               data={"nombre": "B", "dir": "Evento1", "sub": "1"}))
        assert [Path(f["ruta"]).name for f in fs] == ["usar.jpg"], \
            "el evento entero tendria que resolver a Export, no mezclar Jpeg"

        # ya estando en Export, ninguna sorpresa
        fs = _de_export(c.post("/panel/nueva",
                               data={"nombre": "C", "dir": "Evento1/Export"}))
        assert [Path(f["ruta"]).name for f in fs] == ["usar.jpg"], fs

        # un evento viejo, sin la convencion, sigue publicando igual que antes
        (tmp / "EventoViejo").mkdir()
        Image.new("RGB", (10, 10)).save(tmp / "EventoViejo" / "a.jpg")
        fs = _de_export(c.post("/panel/nueva",
                               data={"nombre": "Viejo", "dir": "EventoViejo"}))
        assert len(fs) == 1, "un evento sin Export no deberia bloquearse"

    # export vacia: gana jpeg, tanto eligiendo el evento como entrando a jpeg/
    ev3 = tmp / "Evento3"
    for sub_ in ("RAW", "export", "jpeg"):
        (ev3 / sub_).mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(ev3 / "jpeg" / "cam.jpg")
    assert _resolver_export(ev3) == ev3 / "jpeg", "export vacia dejo el evento sin fotos"
    assert _resolver_export(ev3 / "jpeg") == ev3 / "jpeg", "jpeg/ se fue a la export vacia"
    assert _resolver_export(ev3 / "RAW") == ev3 / "jpeg"
    # y en cuanto Export tiene algo, vuelve a ganar
    Image.new("RGB", (10, 10)).save(ev3 / "export" / "ed.jpg")
    assert _resolver_export(ev3) == ev3 / "export"
    assert _resolver_export(ev3 / "jpeg") == ev3 / "export"

    # elegir la fuente a mano: jpeg aunque export tenga fotos, y tipo seleccion
    with app.test_client() as c:
        r = c.post("/panel/nueva", data={"nombre": "Sel", "dir": "Evento3",
                                          "fuente": "jpeg", "tipo": "seleccion"})
        assert r.status_code == 302, r.status_code
        slug_ = r.headers["Location"].rsplit("/", 1)[-1]
        cx_ = conectar()
        g_ = cx_.execute("SELECT id, tipo FROM galeria WHERE slug = ?", (slug_,)).fetchone()
        rs = [Path(x["ruta"]).name for x in cx_.execute(
            "SELECT ruta FROM foto WHERE galeria_id = ?", (g_["id"],))]
        assert rs == ["cam.jpg"] and g_["tipo"] == "seleccion", (rs, g_["tipo"])
        # una fuente inventada no escapa: cae en el resolutor (export)
        r = c.post("/panel/nueva", data={"nombre": "X", "dir": "Evento3",
                                          "fuente": "../../etc"})
        slug2 = r.headers["Location"].rsplit("/", 1)[-1]
        rs2 = [Path(x["ruta"]).name for x in cx_.execute(
            "SELECT ruta FROM foto WHERE galeria_id = "
            "(SELECT id FROM galeria WHERE slug = ?)", (slug2,))]
        assert rs2 == ["ed.jpg"], rs2
        cx_.execute("DELETE FROM galeria WHERE slug IN (?, ?)", (slug_, slug2))
        cx_.commit()
        cx_.close()

    # la tarjeta copiada tal cual: jpeg/100MSDCF/... tambien cuenta como fuente
    ev4 = tmp / "Evento4"
    for sub_ in ("RAW", "export", "jpeg/100MSDCF"):
        (ev4 / sub_).mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(ev4 / "jpeg" / "100MSDCF" / "a.jpg")
    assert _resolver_export(ev4) == ev4 / "jpeg", "no vio las fotos en 100MSDCF"
    assert [(c.name, n) for c, n in fuentes_de(ev4)] == [("export", 0), ("jpeg", 1)]
    with app.test_client() as c:
        html = c.get("/panel/nueva?dir=Evento4").get_data(as_text=True)
        assert 'name="fuente" value="jpeg"' in html, "no ofrecio elegir la fuente"
        r = c.post("/panel/nueva", data={"nombre": "Tarjeta", "dir": "Evento4"})
        assert r.status_code == 302, r.status_code
        cx_ = conectar()
        cx_.execute("DELETE FROM galeria WHERE nombre = 'Tarjeta'")
        cx_.commit()
        cx_.close()

    # buscar_raw() solo: al lado gana, y sin match no inventa nada
    assert buscar_raw(str(ev / "Export" / "usar.jpg")) == str(ev_raw)
    assert buscar_raw(str(ev / "Export" / "sinusar.jpg")) is None

    # jpeg/100MSDCF/x.JPG con RAW/100MSDCF/x.ARW: dos niveles arriba (F1 ExScuola)
    ev6 = tmp / "Evento6"
    for sub_ in ("jpeg/100MSDCF", "RAW/100MSDCF"):
        (ev6 / sub_).mkdir(parents=True)
    (ev6 / "RAW/100MSDCF/DSC03761.ARW").write_bytes(b"x")
    assert buscar_raw(ev6 / "jpeg/100MSDCF/DSC03761.JPG") == str(ev6 / "RAW/100MSDCF/DSC03761.ARW"), \
        "no subio hasta la carpeta RAW del evento"
    # un RAW copiado DESPUES de una busqueda se ve igual: fuera de una tanda
    # no queda nada cacheado (el server corre dias)
    assert buscar_raw(ev6 / "jpeg/100MSDCF/DSC00009.JPG") is None
    (ev6 / "RAW/100MSDCF/DSC00009.ARW").write_bytes(b"x")
    assert buscar_raw(ev6 / "jpeg/100MSDCF/DSC00009.JPG") is not None, "cache viejo"
    assert not _CACHE_RAW, "quedo cache fuera de una tanda"

    # tarjetas copiadas tal cual adentro de RAW/, con el contador reiniciado:
    # el mismo DSC00001 en dos tarjetas se desempata por la fecha EXIF
    def _con_fecha(ruta, fecha, fmt):
        ex = Image.Exif()
        ex[306] = fecha
        Image.new("RGB", (4, 4)).save(ruta, fmt, exif=ex)

    ev5 = tmp / "Evento5"
    for sub_ in ("RAW/100MSDCF", "RAW/101MSDCF", "Export"):
        (ev5 / sub_).mkdir(parents=True)
    _con_fecha(ev5 / "RAW/100MSDCF/DSC00001.ARW", "2026:08:23 20:00:00", "TIFF")
    _con_fecha(ev5 / "RAW/101MSDCF/DSC00001.ARW", "2026:08:23 22:00:00", "TIFF")
    (ev5 / "RAW/101MSDCF/DSC00002.ARW").write_bytes(b"x")
    _con_fecha(ev5 / "Export/DSC00001.jpg", "2026:08:23 22:00:00", "JPEG")
    assert buscar_raw(ev5 / "Export/DSC00001.jpg") == str(ev5 / "RAW/101MSDCF/DSC00001.ARW"), \
        "nombre repetido: no eligio el RAW de la misma hora"
    assert buscar_raw(ev5 / "Export/DSC00002.jpg") == str(ev5 / "RAW/101MSDCF/DSC00002.ARW"), \
        "no busco adentro de la subcarpeta de la tarjeta"
    assert buscar_raw(ev5 / "Export/DSC00003.jpg") is None
    # repetido y sin fecha para desempatar: nada, antes que el equivocado
    Image.new("RGB", (4, 4)).save(tmp / "DSC00001.jpg")
    assert buscar_raw(tmp / "DSC00001.jpg", ev5 / "RAW") is None, "adivino entre dos RAW"
    # derivada sin RAW al lado: con la carpeta explicita lo encuentra
    (tmp / "deriv").mkdir()
    _con_fecha(tmp / "deriv/DSC00001.JPG", "2026:08:23 20:00:00", "JPEG")
    assert buscar_raw(tmp / "deriv/DSC00001.JPG") is None
    assert buscar_raw(tmp / "deriv/DSC00001.JPG", ev5 / "RAW") == \
        str(ev5 / "RAW/100MSDCF/DSC00001.ARW")

    # compras: los dos DSC00001 de tarjetas distintas en un mismo pedido
    r1, r2 = ev5 / "RAW/100MSDCF/DSC00001.ARW", ev5 / "RAW/101MSDCF/DSC00001.ARW"
    fotos_c = [{"raw": str(r1)}, {"raw": str(r2)}, {"raw": None}]
    ped_, ent_ = ev5 / "Compras/pedidos/ABC123", ev5 / "Compras/entregados/ABC123"
    c0 = estado_compra(fotos_c, "ABC123")
    assert c0["estado"] == "nueva" and c0["sin_raw"] == 1
    assert not ped_.exists() and not ent_.exists(), "mirar la pagina toco el disco"
    solicitar_compra(fotos_c, "ABC123")
    assert (ent_ / "RAW").is_dir() and (ent_ / "Export").is_dir()
    assert sorted(a.name for a in ped_.iterdir()) == ["DSC00001-2.ARW", "DSC00001.ARW"], \
        "el mismo nombre de otra tarjeta piso al primero"
    solicitar_compra(fotos_c, "ABC123")
    c1 = estado_compra(fotos_c, "ABC123")
    assert c1["estado"] == "pedida" and c1["raws"] == 2, "pedirla de nuevo copio de mas"
    Image.new("RGB", (4, 4)).save(ent_ / "Export/DSC00001.jpg")   # el export editado
    entregar_compra(fotos_c, "ABC123")
    assert not ped_.exists()
    assert sorted(a.name for a in (ent_ / "RAW").iterdir()) == ["DSC00001-2.ARW", "DSC00001.ARW"]
    assert [a.name for a in (ent_ / "Export").iterdir()] == ["DSC00001.jpg"]
    assert estado_compra(fotos_c, "ABC123")["estado"] == "entregada"
    solicitar_compra(fotos_c, "ABC123")
    assert not ped_.exists(), "entregada se volvio a pedir"
    assert estado_compra([{"raw": None}], "ZZZ999")["estado"] == "sin_raw"
    # un pedido de antes del 7/10 (carpeta solo <ID>) sigue siendo ese, no se pide de nuevo
    assert estado_compra(fotos_c, "ABC123-Favoritos")["estado"] == "entregada"
    assert carpetas_compra(fotos_c, "ABC123-Favoritos")[1] == ent_
    assert carpetas_compra(fotos_c, "QQQ111-Favoritos")[0].name == "QQQ111-Favoritos"

    # ---- ficha del cliente: los hechos sueltos se agrupan en visitas
    with app.test_client() as c:
        cx_ = conectar()
        cx_.execute("INSERT INTO galeria (slug, nombre, creada) VALUES ('cfic','Fic','2026-01-01')")
        gf = cx_.execute("SELECT id FROM galeria WHERE slug='cfic'").fetchone()["id"]
        cx_.execute("INSERT INTO cliente (galeria_id, email, visto) VALUES (?, 'f@f.com', '2026-01-01')", (gf,))
        cf = cx_.execute("SELECT id FROM cliente WHERE galeria_id=?", (gf,)).fetchone()["id"]
        cx_.execute("INSERT INTO lista (cliente_id, nombre, orden, activa) VALUES (?, 'Favoritos', 0, 1)", (cf,))
        lf = cx_.execute("SELECT id FROM lista WHERE cliente_id=?", (cf,)).fetchone()["id"]
        ff = []
        for n in ("a.jpg", "b.jpg"):
            cx_.execute("INSERT INTO foto (galeria_id, ruta) VALUES (?, ?)", (gf, str(ev5 / "Export" / n)))
            ff.append(cx_.execute("SELECT id FROM foto WHERE galeria_id=? ORDER BY id DESC", (gf,)).fetchone()["id"])
        d = "2026-09-01T"
        # primer rato: mira la a en la grilla y la abre en grande
        cx_.execute("INSERT INTO vista (cliente_id, foto_id, visor, primera, ultima, veces) "
                    "VALUES (?, ?, 0, ?, ?, 3)", (cf, ff[0], d + "10:00:00+00:00", d + "10:05:00+00:00"))
        cx_.execute("INSERT INTO vista (cliente_id, foto_id, visor, primera, ultima, veces) "
                    "VALUES (?, ?, 1, ?, ?, 1)", (cf, ff[0], d + "10:02:00+00:00", d + "10:02:00+00:00"))
        # dos horas despues vuelve: es OTRA visita, no la misma
        cx_.execute("INSERT INTO vista (cliente_id, foto_id, visor, primera, ultima, veces) "
                    "VALUES (?, ?, 0, ?, ?, 1)", (cf, ff[1], d + "12:00:00+00:00", d + "12:10:00+00:00"))
        cx_.execute("INSERT INTO descarga (cliente_id, foto_id, cual, cuando) "
                    "VALUES (?, ?, 'original', ?)", (cf, ff[1], d + "12:05:00+00:00"))
        cx_.execute("INSERT INTO lista_foto (lista_id, foto_id, cuando) VALUES (?, ?, ?)",
                    (lf, ff[1], d + "12:06:00+00:00"))
        # marca vieja, de antes de que se guardara la hora: no inventa una visita
        cx_.execute("INSERT INTO lista_foto (lista_id, foto_id) VALUES (?, ?)", (lf, ff[0]))
        cx_.commit()

        vs = visitas_cliente(cx_, cf)
        assert len(vs) == 2, "los dos ratos quedaron en %d visita(s)" % len(vs)
        assert vs[0]["desde"].endswith("10:00:00+00:00") and vs[0]["hasta"].endswith("10:05:00+00:00"), vs[0]
        assert vs[0]["visor"] == 1 and vs[0]["bajadas"] == 0, vs[0]
        assert vs[1]["bajadas"] == 1 and vs[1]["marcas"] == 1, vs[1]
        assert sum(v["fotos"] for v in vs) == 2, vs

        html = c.get(f"/panel/cfic/cliente/{cf}").get_data(as_text=True)
        assert "f@f.com" in html and "2 visitas" in html, "la ficha no resume"
        assert "/panel/cfic/cliente/%d" % cf in c.get("/panel/cfic").get_data(as_text=True),             "el panel no linkea la ficha"
        assert c.get(f"/panel/ccc/cliente/{cf}").status_code == 404, "FUGA: ficha de otra galeria"
        cx_.execute("DELETE FROM galeria WHERE slug='cfic'")
        cx_.commit()
        cx_.close()

    # ---- comentarios: apagados no existen, prendidos los ve cualquiera que entro
    cx_ = conectar()
    cx_.execute("INSERT INTO galeria (slug, nombre, creada) VALUES ('ccom','Com','2026-01-01')")
    gco = cx_.execute("SELECT id FROM galeria WHERE slug='ccom'").fetchone()["id"]
    cx_.execute("INSERT INTO foto (galeria_id, ruta) VALUES (?, ?)",
                (gco, str(ev5 / "Export/DSC00001.jpg")))
    fco = cx_.execute("SELECT id FROM foto WHERE galeria_id=?", (gco,)).fetchone()["id"]
    cx_.commit()
    cx_.close()
    c1, c2 = app.test_client(), app.test_client()   # dos clientes distintos
    if True:
        c1.post("/g/ccom/entrar", data={"email": "uno@x.com", "nombre": "Uno"})
        c2.post("/g/ccom/entrar", data={"email": "dos@x.com"})      # entra sin nombre
        # con la casilla apagada no alcanza con esconder la caja: la ruta corta
        assert c1.post("/api/comentario", json={"foto_id": fco, "texto": "hola"}
                       ).status_code == 404, "FUGA: comento con los comentarios apagados"
        cx_ = conectar()
        cx_.execute("UPDATE galeria SET comentarios = 1 WHERE slug='ccom'")
        cx_.commit()
        cx_.close()
        assert c1.post("/api/comentario", json={"foto_id": fco, "texto": "  que linda  "}
                       ).status_code == 201
        # el que entro sin nombre: se le pide antes de comentar, no se le pierde el texto
        # ojo: no reusar nombres de mas arriba (r1/r2 son rutas de RAW)
        sin_nombre = c2.post("/api/comentario", json={"foto_id": fco, "texto": "yo tambien"})
        assert sin_nombre.status_code == 409 and sin_nombre.get_json()["falta_nombre"],             sin_nombre.status_code
        assert c2.post("/api/nombre/ccom", json={"nombre": " Dos "}).status_code == 200
        assert c2.post("/api/comentario", json={"foto_id": fco, "texto": "yo tambien"}
                       ).status_code == 201
        d = c1.get(f"/api/comentarios/{fco}").get_json()["comentarios"]
        assert [x["nombre"] for x in d] == ["Uno", "Dos"], d
        assert d[0]["texto"] == "que linda", d[0]           # recortado
        assert d[0]["mio"] and not d[1]["mio"], d
        # vacio y larguisimo no entran
        assert c1.post("/api/comentario", json={"foto_id": fco, "texto": "   "}
                       ).status_code == 400
        assert c1.post("/api/comentario", json={"foto_id": fco, "texto": "x" * 2000}
                       ).status_code == 400
        # un comentario con HTML adentro se muestra, no se ejecuta
        c1.post("/api/comentario", json={"foto_id": fco, "texto": "<b>hola</b>"})
        pag = c1.get("/g/ccom/comentarios").get_data(as_text=True)
        assert "&lt;b&gt;hola&lt;/b&gt;" in pag and "<b>hola</b>" not in pag, "comentario sin escapar"
        # borrar: el dueno si, el de al lado no
        assert c2.post(f"/api/comentario/{d[0]['id']}/borrar").status_code == 404,             "FUGA: borro el comentario de otro"
        assert c1.post(f"/api/comentario/{d[0]['id']}/borrar").status_code == 200
        quedan = c1.get(f"/api/comentarios/{fco}").get_json()["comentarios"]
        assert [x["nombre"] for x in quedan] == ["Dos", "Uno"], quedan
        # el panel los junta y puede borrar cualquiera, no solo los propios
        ajeno = [x for x in quedan if x["nombre"] == "Dos"][0]["id"]
        assert "Dos" in c1.get("/panel/ccom/comentarios").get_data(as_text=True)
        assert c1.post(f"/panel/ccom/comentario/{ajeno}/borrar").status_code == 302
        assert not [x for x in c1.get(f"/api/comentarios/{fco}").get_json()["comentarios"]
                    if x["nombre"] == "Dos"], "el panel no borro"
        # borrar la galeria se lleva sus comentarios, no los deja colgando
        cx_ = conectar()
        cx_.execute("DELETE FROM galeria WHERE slug='ccom'")
        cx_.commit()
        assert cx_.execute("SELECT COUNT(*) FROM comentario WHERE foto_id = ?",
                           (fco,)).fetchone()[0] == 0, "quedaron comentarios huerfanos"
        cx_.commit()
        cx_.close()
    # por la web, en una lista cualquiera (no carrito): GET no copia, POST si
    with app.test_client() as c:
        cx_ = conectar()
        cx_.execute("INSERT INTO galeria (slug, nombre, creada) VALUES ('ccomp','Comp','2026-01-01')")
        gid_ = cx_.execute("SELECT id FROM galeria WHERE slug='ccomp'").fetchone()["id"]
        cx_.execute("INSERT INTO cliente (galeria_id, email, visto) VALUES (?, 'c@c.com', '2026-01-01')", (gid_,))
        cid_ = cx_.execute("SELECT id FROM cliente WHERE galeria_id=?", (gid_,)).fetchone()["id"]
        cx_.execute("INSERT INTO lista (cliente_id, nombre, orden, activa) VALUES (?, 'Favoritos', 0, 1)", (cid_,))
        lid_ = cx_.execute("SELECT id FROM lista WHERE cliente_id=?", (cid_,)).fetchone()["id"]
        cx_.execute("INSERT INTO foto (galeria_id, ruta, raw) VALUES (?, ?, ?)",
                    (gid_, str(ev5 / "Export/DSC00001.jpg"), str(r2)))
        fid_ = cx_.execute("SELECT id FROM foto WHERE galeria_id=?", (gid_,)).fetchone()["id"]
        cx_.execute("INSERT INTO lista_foto (lista_id, foto_id) VALUES (?, ?)", (lid_, fid_))
        cx_.commit()
        cod_ = codigo_lista(lid_) + "-Favoritos"     # la carpeta lleva el nombre de la lista
        assert carpeta_pedido(lid_, 'EDICION: "completa"/ IG.') == codigo_lista(lid_) + "-EDICION completa IG"
        html = c.get(f"/panel/ccomp/lista/{lid_}").get_data(as_text=True)
        assert "Lista solicitada" in html and not (ev5 / "Compras/pedidos" / cod_).exists()
        assert c.post(f"/panel/ccomp/lista/{lid_}/solicitar").status_code == 302
        assert [a.name for a in (ev5 / "Compras/pedidos" / cod_).iterdir()] == ["DSC00001.ARW"]
        assert "Marcar entregada" in c.get(f"/panel/ccomp/lista/{lid_}").get_data(as_text=True)
        assert c.post(f"/panel/ccomp/lista/{lid_}/entregar").status_code == 302
        assert "Entregada" in c.get(f"/panel/ccomp/lista/{lid_}").get_data(as_text=True)
        assert c.post(f"/panel/ccomp/lista/{lid_}/borrar").status_code in (404, 405)
        cx_.execute("DELETE FROM galeria WHERE slug='ccomp'")
        cx_.commit()
        cx_.close()
    assert carpeta_evento(r1) == ev5 and carpeta_evento(tmp / "x.ARW") is None

    # calentadas_de / _calentando, contra archivos reales
    cx = conectar()
    cx.execute("INSERT INTO galeria (slug, nombre, creada) VALUES ('ccal','Cal','2026-01-01')")
    gid_cal = cx.execute("SELECT id FROM galeria WHERE slug='ccal'").fetchone()["id"]
    foto_a, foto_b = tmp / "cal_a.jpg", tmp / "cal_b.jpg"
    Image.new("RGB", (10, 10)).save(foto_a)
    Image.new("RGB", (10, 10)).save(foto_b)
    cx.execute("INSERT INTO foto (galeria_id, ruta) VALUES (?, ?)", (gid_cal, str(foto_a)))
    cx.execute("INSERT INTO foto (galeria_id, ruta) VALUES (?, ?)", (gid_cal, str(foto_b)))
    cx.commit()
    gal_cal = cx.execute("SELECT * FROM galeria WHERE slug = 'ccal'").fetchone()
    assert calentadas_de(cx, gid_cal) == (0, 2), "nada calentado todavia"
    # se genera por el mismo camino que usa calentar(), no a mano: asi el test
    # falla si calentar() y el contador dejan de mirar la misma cache
    preview_de(gal_cal, 1, str(foto_a))                  # solo la de la grilla
    assert calentadas_de(cx, gid_cal) == (0, 2),         "la grilla sola no alcanza: sin la del visor la foto no esta lista"
    preview_de(gal_cal, 1, str(foto_a), ANCHO_VISOR)     # ahora si, las dos
    assert calentadas_de(cx, gid_cal) == (1, 2), "no conto la que ya calente"
    # pedido a la notebook: con latido fresco se anota y no se calienta aca;
    # con el latido viejo el pedido no cuenta (la notebook se fue)
    latido = CACHE / "notebook-vivo"
    assert not notebook_viva() and not en_notebook("ccal")
    latido.write_text("x")
    with app.test_client() as c:
        assert c.post("/panel/ccal/calentar").status_code == 302
    assert (CACHE / "pedido-ccal").exists(), "no dejo el pedido para la notebook"
    assert not _lock_calentando("ccal").exists(), "calento en la Pi teniendo notebook"
    assert en_notebook("ccal")
    os.utime(latido, (time.time() - 600, time.time() - 600))
    assert not notebook_viva() and not en_notebook("ccal"), "latido viejo sigue contando"
    latido.unlink()
    (CACHE / "pedido-ccal").unlink()

    assert _calentando("ccal") is False
    _lock_calentando("ccal").write_text(str(os.getpid()))   # este proceso, seguro vivo
    assert _calentando("ccal") is True
    _lock_calentando("ccal").write_text("999999999")        # pid que no existe
    assert _calentando("ccal") is False
    _lock_calentando("ccal").unlink()
    cx.execute("DELETE FROM galeria WHERE slug = 'ccal'")
    cx.commit()
    cx.close()

    reales = []
    for n in ("uno.jpg", "dos.jpg", "con,coma.jpg"):
        f = tmp / n
        Image.new("RGB", (800, 600), (40, 80, 120)).save(f)
        reales.append(str(f))

    cx = conectar()
    cx.execute("INSERT INTO galeria (slug, nombre, creada, clave_hash, descargas) "
               "VALUES (?,?,?,?,1)", ("ccc", "C", "2026-01-01", h))
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
        # con clave puesta, la equivocada no abre nada. La sonda es /p/, que
        # depende de haber entrado; el zip ya no sirve para esto porque da 404
        # siempre y la asercion pasaria aun con el control de clave roto.
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "mala"})
        assert c.get(f"/p/{id_uno}").status_code == 404, "FUGA: entro con la clave equivocada"
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "secreta"})
        assert c.get(f"/p/{id_uno}").status_code == 200, "con la clave correcta no entro"

        # cada foto pedida deja su fila: es lo que despues contesta si el
        # cliente recorrio el evento entero o abandono en el primer partido.
        # La miniatura la anota la propia foto; "la abrio en grande" NO, porque
        # el visor precarga 3 adelante y esas nunca se vieron: eso lo avisa el
        # visor aparte, recien cuando la foto estuvo medio segundo en pantalla.
        cx = conectar()
        cx.execute("DELETE FROM vista WHERE foto_id = ?", (id_uno,))
        cx.commit()
        cx.close()
        c.get(f"/p/{id_uno}")
        c.get(f"/p/{id_uno}")
        c.get(f"/p/{id_uno}?g=1")
        c.get(f"/p/{id_uno}?g=1")
        cx = conectar()
        v = [tuple(r) for r in cx.execute(
            "SELECT vista.visor, vista.veces FROM vista "
            "JOIN cliente ON cliente.id = vista.cliente_id "
            "WHERE vista.foto_id = ? AND cliente.email = ? ORDER BY visor",
            (id_uno, "x@y.com"))]
        cx.close()
        assert v == [(0, 2)], "pedir la foto grande no puede contar como mirada: %r" % v
        assert c.post(f"/api/visto/{id_uno}").status_code == 204
        assert c.post(f"/api/visto/{id_uno}").status_code == 204
        assert c.post(f"/api/visto/{id_venc}").status_code == 404, "FUGA: vista de otra galeria"
        cx = conectar()
        v = [tuple(r) for r in cx.execute(
            "SELECT vista.visor, vista.veces FROM vista "
            "JOIN cliente ON cliente.id = vista.cliente_id "
            "WHERE vista.foto_id = ? AND cliente.email = ? ORDER BY visor",
            (id_uno, "x@y.com"))]
        cx.close()
        assert v == [(0, 2), (1, 2)], v

        # ---- Descargadas: se llena sola con lo que bajo y no se puede editar
        assert c.get(f"/d/{id_uno}").status_code == 200
        d = c.get("/api/listas/ccc").get_json()
        assert d["bajadas"] == 1, d["bajadas"]
        vista_baj = c.get("/g/ccc?bajadas=1").get_data(as_text=True)
        assert "Descargadas" in vista_baj, "no se ve la lista de descargadas"
        # es una vista, no una lista: no hay id que marcar ni de donde sacarla
        assert not [r for r in conectar().execute(
            "SELECT nombre FROM lista WHERE nombre = 'Descargadas'")],             "Descargadas no puede ser una lista de verdad"
        # y desde el panel se ve como una lista mas, pero sin nada que editar
        cid_x = conectar().execute(
            "SELECT cliente.id FROM cliente JOIN galeria ON galeria.id = cliente.galeria_id "
            "WHERE galeria.slug = 'ccc' AND cliente.email = 'x@y.com'").fetchone()["id"]
        pd = c.get(f"/panel/ccc/cliente/{cid_x}/descargadas").get_data(as_text=True)
        assert "Descargadas" in pd and "Los nombres de archivo" in pd, "la pagina no salio"
        assert "/solicitar" not in pd, "la pagina de descargadas trae botones de lista"
        assert c.get("/panel/ccc").get_data(as_text=True).count(
            f"/panel/ccc/cliente/{cid_x}/descargadas") == 1, "falta el chip en el panel"
        cx = conectar()      # la bajada de prueba no le suma filas al test de abajo
        cx.execute("DELETE FROM descarga WHERE foto_id = ?", (id_uno,))
        cx.commit()
        cx.close()

        # ---- listas automaticas: apagar la casilla se lleva las que quedaron vacias
        cx = conectar()
        cx.execute("UPDATE galeria SET listas_auto = 'Edicion completa' WHERE slug = 'ccc'")
        cx.commit()
        cx.close()
        c.get("/api/listas/ccc")           # abrir el panel de listas la crea
        cx = conectar()
        cid_ccc = cx.execute(
            "SELECT cliente.id FROM cliente JOIN galeria ON galeria.id = cliente.galeria_id "
            "WHERE galeria.slug = 'ccc' AND cliente.email = 'x@y.com'").fetchone()["id"]
        auto = cx.execute("SELECT id, auto FROM lista WHERE cliente_id = ? AND nombre = ?",
                          (cid_ccc, "Edicion completa")).fetchone()
        assert auto and auto["auto"] == 1, "no se creo marcada como automatica"
        # con una foto adentro NO se toca, aunque se apague la casilla
        cx.execute("INSERT INTO lista_foto (lista_id, foto_id) VALUES (?, ?)",
                   (auto["id"], id_coma))
        cx.execute("UPDATE galeria SET listas_auto = NULL WHERE slug = 'ccc'")
        cx.commit()
        cx.close()
        c.get("/api/listas/ccc")
        cx = conectar()
        assert cx.execute("SELECT COUNT(*) FROM lista WHERE id = ?",
                          (auto["id"],)).fetchone()[0] == 1,             "se borro una lista automatica que tenia fotos"
        cx.execute("DELETE FROM lista_foto WHERE lista_id = ?", (auto["id"],))
        cx.commit()
        cx.close()
        c.get("/api/listas/ccc")           # ahora esta vacia y ya no se pide
        cx = conectar()
        assert cx.execute("SELECT COUNT(*) FROM lista WHERE id = ?",
                          (auto["id"],)).fetchone()[0] == 0,             "la lista automatica vacia quedo ahi despues de apagar la casilla"
        # ---- renglones de la Edicion quitados en ESTA galeria
        cx = conectar()
        cx.execute("UPDATE galeria SET ent_edicion = 'completa', ent_quita = NULL "
                   "WHERE slug = 'ccc'")
        cx.commit()
        g_ccc = cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone()
        todas = edicion_de(g_ccc)[1]
        assert len(todas) > 1, todas
        cx.execute("UPDATE galeria SET ent_quita = ? WHERE slug = 'ccc'", (todas[0],))
        cx.commit()
        g_ccc = cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone()
        assert edicion_de(g_ccc)[1] == todas[1:], "no saco el renglon quitado"
        # el preset general sigue entero: la omision es solo de esta galeria
        assert lineas(textos()["edicion_completa"]) == todas, "toco el preset general"
        # cambiar de preset no arrastra las omisiones del anterior. El form de
        # ajustes guarda TODO, asi que se restaura la galeria despues
        antes = dict(cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone())
        assert c.post("/panel/ccc/ajustes", data={"ent_edicion": "basica",
                                                  "ent_edicion_previa": "completa",
                                                  "marca_texto": g_ccc["marca_texto"] or ""}
                      ).status_code == 302
        cx = conectar()
        assert cx.execute("SELECT ent_quita FROM galeria WHERE slug = 'ccc'"
                          ).fetchone()["ent_quita"] is None,             "las omisiones de la edicion vieja siguieron puestas"
        cols = [k for k in antes if k != "id"]
        cx.execute("UPDATE galeria SET %s WHERE id = ?" % ", ".join(k + " = ?" for k in cols),
                   [antes[k] for k in cols] + [antes["id"]])
        cx.commit()
        # ojo: no cerrar cx aca, lo que sigue lo usa

        # la de Favoritos es del cliente, no automatica: vacia igual se queda
        assert cx.execute(
            "SELECT COUNT(*) FROM lista WHERE cliente_id = ? AND nombre = 'Favoritos'",
            (cid_ccc,)).fetchone()[0] == 1, "borro Favoritos"
        cx.close()
        # y el panel lo muestra
        pan = c.get("/panel/ccc").get_data(as_text=True)
        assert "Que miro cada uno" in pan, "el panel no muestra la cobertura"

        # se baja de a una, nunca en bloque
        assert c.get(f"/d/{id_uno}").status_code == 200, "no se puede bajar de a una"
        assert c.get(f"/d/{id_uno}").data == Path(reales[0]).read_bytes(), \
            "la descarga altero el original"
        # cada bajada deja su fila, con el cliente que la pidio
        cx = conectar()
        d = cx.execute("SELECT cliente.email, descarga.foto_id, descarga.cual "
                       "FROM descarga JOIN cliente ON cliente.id = descarga.cliente_id "
                       "WHERE descarga.foto_id = ?", (id_uno,)).fetchall()
        cx.close()
        assert len(d) == 2, "se bajo dos veces y quedaron %d filas" % len(d)
        assert d[0]["email"] == "x@y.com" and d[0]["cual"] == "original", tuple(d[0])

        # Instagram le pasa el link a Chrome sin la cookie: el firmado baja igual
        m = re.search(r'href="(/d/%d\?[^"]+)"' % id_uno,
                      c.get("/g/ccc").get_data(as_text=True))
        assert m, "la grilla no firma el link de descarga"
        firmado = m.group(1).replace("&amp;", "&")
        suelto = app.test_client()
        assert suelto.get(firmado).status_code == 200, "el link firmado no baja sin cookie"
        assert suelto.get(f"/d/{id_uno}").status_code == 404, "FUGA: bajo sin cookie ni firma"
        assert suelto.get(re.sub(r"k=\w+", "k=" + "0" * 20, firmado)).status_code == 404, \
            "FUGA: bajo con la firma adulterada"
        assert suelto.get("/p/%d%s" % (id_uno, firmado[firmado.index("?"):])
                          ).status_code == 404, \
            "FUGA: la firma de descarga abre las demas rutas"

        assert c.get("/g/ccc/zip").status_code == 404, "FUGA: bajo el zip entero"
        assert c.get("/g/ccc/zip/elegidas").status_code == 404, "FUGA: bajo el zip de elegidas"

        # raw_descarga=0 saca el boton RAW y la ruta; el JPG sigue bajando
        cx = conectar()
        cx.execute("UPDATE foto SET raw = ? WHERE id = ?", (reales[0], id_uno))
        cx.commit()
        assert c.get(f"/d/{id_uno}/raw").status_code == 200, "RAW prendido no baja"
        assert "Descargar RAW" in c.get("/g/ccc").get_data(as_text=True)
        cx.execute("UPDATE galeria SET raw_descarga = 0 WHERE slug = 'ccc'")
        cx.commit()
        assert c.get(f"/d/{id_uno}/raw").status_code == 404, "FUGA: bajo RAW apagado"
        assert "Descargar RAW" not in c.get("/g/ccc").get_data(as_text=True), \
            "boton RAW visible con raw_descarga=0"
        assert c.get(f"/d/{id_uno}").status_code == 200, "apagar RAW corto el JPG"
        cx.execute("UPDATE galeria SET raw_descarga = 1 WHERE slug = 'ccc'")
        cx.execute("UPDATE foto SET raw = NULL WHERE id = ?", (id_uno,))
        cx.execute("DELETE FROM descarga WHERE foto_id = ?", (id_uno,))
        cx.commit()
        cx.close()

        # descargable=0 en una foto puntual la saca, aunque la galeria entera
        # tenga las descargas permitidas
        cx = conectar()
        cx.execute("UPDATE foto SET descargable = 0 WHERE id = ?", (id_uno,))
        cx.commit()
        cx.close()
        assert c.get(f"/d/{id_uno}").status_code == 404, \
            "FUGA: bajo una foto marcada como no-descargable"
        assert c.get(f"/d/{id_coma}").status_code == 200, \
            "afecto a otra foto que no toque"

        # la pagina de descargables: lo que no se tilda queda en 0
        r = c.get("/panel/ccc/descargables")
        assert r.status_code == 200 and "Que se puede descargar" in r.get_data(as_text=True)
        r = c.post("/panel/ccc/descargables", data={"si": str(id_coma)})
        assert r.status_code == 302
        cx = conectar()
        vals = {row["id"]: row["descargable"] for row in cx.execute(
            "SELECT id, descargable FROM foto WHERE galeria_id = "
            "(SELECT id FROM galeria WHERE slug = 'ccc')")}
        assert vals[id_coma] == 1 and vals[id_uno] == 0, vals
        # se deja como estaba, para no ensuciar el resto del self-check
        cx.execute("UPDATE foto SET descargable = 1 WHERE galeria_id = "
                   "(SELECT id FROM galeria WHERE slug = 'ccc')")
        cx.commit()
        cx.close()

        c.post("/api/megusta", json={"foto_id": id_coma})

        # el csv sale con el encabezado exacto y escapa la coma de la ruta
        cs = c.get("/panel/ccc/seleccion.csv").get_data(as_text=True).splitlines()
        assert cs[0] == "lista,ruta,nombre,votos,emails", cs[0]
        # se parsea de verdad en vez de mirar la comilla: lo que importa es
        # que la coma de la ruta no parta el campo en dos
        fila = next(csv.reader([cs[1]]))
        assert len(fila) == 5, f"la coma partio la fila: {fila}"
        assert fila[0] == "Favoritos", fila
        assert fila[2] == "con,coma.jpg", fila

        # descargas apagadas: no alcanza con esconder los botones, las dos
        # direcciones se adivinan y tienen que cerrarse en el servidor
        assert c.get(f"/d/{id_uno}").status_code == 200
        cx = conectar()
        cx.execute("UPDATE galeria SET descargas = 0 WHERE slug = 'ccc'")
        cx.commit()
        cx.close()
        assert c.get(f"/d/{id_uno}").status_code == 404, "FUGA: bajo el original"
        assert c.get("/g/ccc/zip").status_code == 404, "FUGA: bajo el zip"
        assert c.get("/g/ccc/zip/elegidas").status_code == 404, "FUGA: zip de elegidas"
        # la seleccion, que es para lo que existe la galeria, sigue andando
        assert c.get("/g/ccc").status_code == 200
        assert c.post("/api/megusta", json={"foto_id": id_uno}).status_code == 200

        # ---------------------------------------------------------- listas
        # Estado explicito: sin esto el bloque depende de lo que hayan dejado
        # las pruebas de arriba y una asercion puede pasar por casualidad.
        cx = conectar()
        # hay un x@y.com por galeria: sin acotar a ccc se borran las listas de otro
        cli = cx.execute(
            "SELECT cliente.id FROM cliente "
            "JOIN galeria ON galeria.id = cliente.galeria_id "
            "WHERE galeria.slug = 'ccc'").fetchone()["id"]
        cx.execute("DELETE FROM lista WHERE cliente_id = ?", (cli,))
        cx.commit()
        cx.close()

        d = c.get("/api/listas/ccc").get_json()
        assert [l["nombre"] for l in d["listas"]] == ["Favoritos"], d
        assert d["listas"][0]["n"] == 0 and d["listas"][0]["activa"] == 1, d

        d = c.post("/api/listas/ccc", json={"nombre": "Edicion"}).get_json()
        ed = [l for l in d["listas"] if l["nombre"] == "Edicion"][0]
        assert ed["activa"] == 0, "una lista nueva no arranca activa"

        # un like con una sola activa entra solo ahi
        assert c.post("/api/megusta", json={"foto_id": id_uno}).get_json()["mia"] is True
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 1, "Edicion": 0}, d

        # con las dos activas, entra y sale de las DOS a la vez
        c.post(f"/api/listas/ccc/{ed['id']}/activa", json={"activa": True})
        assert c.post("/api/megusta", json={"foto_id": id_coma}).get_json()["mia"] is True
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 2, "Edicion": 1}, d
        assert c.post("/api/megusta", json={"foto_id": id_coma}).get_json()["mia"] is False
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 1, "Edicion": 0}, d

        # pasar favoritas a otra lista (Renato): con las dos prendidas, una foto
        # que esta solo en Favoritos tiene el corazon VACIO, y tocarlo la suma
        # a Edicion sin sacarla de Favoritos
        assert c.post("/api/megusta", json={"foto_id": id_uno}).get_json()["mia"] is True
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 1, "Edicion": 1}, \
            "tocar una favorita con Edicion prendida la saco de Favoritos"
        # y otra vez: ahora esta en las dos, sale de las dos
        c.post("/api/megusta", json={"foto_id": id_uno})
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 0, "Edicion": 0}, d
        c.post(f"/api/listas/ccc/{ed['id']}/activa", json={"activa": False})
        c.post("/api/megusta", json={"foto_id": id_uno})     # vuelve a Favoritos sola
        c.post(f"/api/listas/ccc/{ed['id']}/activa", json={"activa": True})
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 1, "Edicion": 0}, d

        # el corazon y el contador miran SOLO las activas
        fav = [l for l in d["listas"] if l["nombre"] == "Favoritos"][0]
        c.post(f"/api/listas/ccc/{fav['id']}/activa", json={"activa": False})
        assert c.get("/api/marcadas/ccc").get_json()["ids"] == []
        assert c.get("/api/listas/ccc").get_json()["marcadas"] == 0

        # sin ninguna activa el like no se pierde: prende Favoritos y cae ahi
        c.post(f"/api/listas/ccc/{ed['id']}/activa", json={"activa": False})
        assert c.post("/api/megusta", json={"foto_id": id_coma}).get_json()["mia"] is True
        d = c.get("/api/listas/ccc").get_json()
        assert [l for l in d["listas"] if l["nombre"] == "Favoritos"][0]["activa"] == 1, d
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 2, "Edicion": 0}, d

        # ver una lista sola: solo sus fotos, y la de otro cliente no existe
        html = c.get(f"/g/ccc?lista={ed['id']}").get_data(as_text=True)
        assert html.count('class="celda"') == 0, "Edicion quedo vacia, no deberia pintar nada"
        html = c.get(f"/g/ccc?lista={fav['id']}").get_data(as_text=True)
        assert html.count('class="celda"') == 2, html.count('class="celda"')
        assert c.get("/g/ccc?lista=99999").status_code == 404, "FUGA: lista ajena"

        # repetidas: una foto en Favoritos y en Edicion cuenta 1 repetida y
        # las distintas no la cuentan dos veces
        c.post(f"/api/listas/ccc/{fav['id']}/activa", json={"activa": False})
        c.post(f"/api/listas/ccc/{ed['id']}/activa", json={"activa": True})
        c.post("/api/megusta", json={"foto_id": id_uno})      # a Edicion sola
        d = c.get("/api/listas/ccc").get_json()
        assert d["repetidas"] == 1 and d["distintas"] == 2, d
        html = c.get("/g/ccc?repetidas=1").get_data(as_text=True)
        assert html.count('class="celda"') == 1, "la vista de repetidas no filtro"
        assert 'class="enls"' in html and "Edicion" in html, "sin rotulo de listas"
        c.post("/api/megusta", json={"foto_id": id_uno})      # sale de Edicion
        assert c.get("/api/listas/ccc").get_json()["repetidas"] == 0
        c.post(f"/api/listas/ccc/{ed['id']}/activa", json={"activa": False})
        c.post(f"/api/listas/ccc/{fav['id']}/activa", json={"activa": True})

        # listas automaticas: se crean al pedir las listas, sin duplicar una que
        # ya existe con otras mayusculas o sin tilde ("Edicion" == "EDICIÓN")
        cx_ = conectar()
        cx_.execute("UPDATE galeria SET listas_auto = ? WHERE slug = 'ccc'",
                    ("Edición completa\nEDICIÓN",))
        cx_.commit()
        nombres = [l["nombre"] for l in c.get("/api/listas/ccc").get_json()["listas"]]
        assert "Edición completa" in nombres, nombres
        assert sum(_norm(n) == "edicion" for n in nombres) == 1, "duplico Edicion"
        cx_.execute("UPDATE galeria SET listas_auto = NULL WHERE slug = 'ccc'")
        cx_.execute("DELETE FROM lista WHERE nombre = 'Edición completa'")
        cx_.commit()
        cx_.close()

        # elegir la lista de UNA foto: va a esa y a ninguna otra, este prendida o no
        r = c.post(f"/api/listas/ccc/{ed['id']}/foto", json={"foto_id": id_coma, "en": True})
        d = r.get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 2, "Edicion": 1}, d
        en = {l["nombre"]: l["en"] for l in
              c.get(f"/api/listas/ccc/foto/{id_coma}").get_json()["listas"]}
        assert en == {"Favoritos": 1, "Edicion": 1}, en
        c.post(f"/api/listas/ccc/{fav['id']}/foto", json={"foto_id": id_coma, "en": False})
        c.post(f"/api/listas/ccc/{ed['id']}/foto", json={"foto_id": id_coma, "en": False})
        d = c.get("/api/listas/ccc").get_json()
        assert {l["nombre"]: l["n"] for l in d["listas"]} == {"Favoritos": 1, "Edicion": 0}, d
        c.post(f"/api/listas/ccc/{fav['id']}/foto", json={"foto_id": id_coma, "en": True})
        assert c.post("/api/listas/ccc/99999/foto",
                      json={"foto_id": id_coma, "en": True}).status_code == 404, "lista ajena"
        car_id = c.get("/api/listas/ccc").get_json()["carrito_id"]
        assert c.post(f"/api/listas/ccc/{car_id}/foto",
                      json={"foto_id": id_coma, "en": True}).status_code == 400

        # el corazon devuelve las listas ya contadas y a cuales fue (para el aviso)
        d = c.post("/api/megusta", json={"foto_id": id_coma}).get_json()
        assert "listas" in d and d["en"] == ["Favoritos"] and d["mia"] is False, d
        assert {l["nombre"]: l["n"] for l in d["listas"]}["Favoritos"] == 1, d
        c.post("/api/megusta", json={"foto_id": id_coma})

        # dar vuelta la galeria: mismas fotos, orden inverso
        asc = c.get("/g/ccc").get_data(as_text=True)
        des = c.get("/g/ccc?dir=desc").get_data(as_text=True)
        pa = re.findall(r'/p/(\d+)"', asc)
        pd = re.findall(r'/p/(\d+)"', des)
        assert pa and pd == pa[::-1], (pa, pd)
        # y el scroll infinito tiene que pedir la tanda siguiente en ESE orden:
        # sin esto la segunda tanda vuelve del derecho y se mezcla con la primera
        assert 'const DIR = "desc"' in des, "el scroll perderia el orden"
        assert 'const DIR = "asc"' in asc
        # el control esta y ofrece los cuatro criterios
        assert 'name="por"' in asc and 'name="dir"' in asc, "sin control de orden"
        for k in ORDENES:
            assert 'value="%s"' % k in asc, k
        # ordenar por elegidas responde y no rompe el paginado
        assert c.get("/g/ccc?por=likes").status_code == 200
        assert c.get("/g/ccc?por=likes&dir=asc").status_code == 200
        # y el scroll dentro de una lista tiene que seguir filtrando por ella:
        # concatenando el "&" Jinja lo escapaba y traia la galeria entera
        una = c.get("/g/ccc?lista=%d" % fav["id"]).get_data(as_text=True)
        assert 'const LISTA_ABIERTA = "%d"' % fav["id"] in una
        assert 'searchParams.set("lista"' in una

        # y una lista que no es suya no se activa
        assert c.post("/api/listas/ccc/99999/activa",
                      json={"activa": True}).status_code == 404

        # el panel abre la lista de un cliente como galeria...
        r = c.get(f"/panel/ccc/lista/{fav['id']}")
        assert r.status_code == 200, r.status_code
        html = r.get_data(as_text=True)
        assert html.count('class=\"celda\"') == 2, html.count('class=\"celda\"')
        assert 'x@y.com' in html
        # la vista previa del link en un chat: sin esto WhatsApp solo muestra
        # "rb.fotos" y el dominio, igual para todas las galerias
        og = c.get("/g/ccc").get_data(as_text=True)
        assert 'og:title" content="Galeria de C"' in og, "sin titulo propio"
        assert 'property="og:image"' in og, "sin imagen de vista previa"
        assert "3 fotos" in og.split("<style>")[0], "la bajada no dice cuantas son"
        # y el muro de correo la lleva igual: es la pagina que ve quien recibe
        # el link sin haber entrado, o sea justo la que previsualiza el chat
        with app.test_client() as c2:
            g8 = c2.get("/g/ccc").get_data(as_text=True)
            assert 'og:title" content="Galeria de C"' in g8, "el gate no la lleva"

        # renombrar la galeria: lo ve el cliente, y vacio no puede dejarla sin
        # titulo en la vidriera
        c.post("/panel/ccc/ajustes", data={"nombre": "Otro nombre"})
        cx = conectar()
        assert cx.execute("SELECT nombre FROM galeria WHERE slug='ccc'"
                          ).fetchone()[0] == "Otro nombre"
        cx.close()
        c.post("/panel/ccc/ajustes", data={"nombre": "   "})
        cx = conectar()
        assert cx.execute("SELECT nombre FROM galeria WHERE slug='ccc'"
                          ).fetchone()[0] == "Otro nombre", "un nombre vacio la borro"
        cx.execute("UPDATE galeria SET nombre = 'C' WHERE slug = 'ccc'")
        cx.commit()
        cx.close()

        # el ID del pedido: ni secuencial ni con el correo adentro, y siempre
        # el mismo para la misma lista
        cod = codigo_lista(fav["id"])
        assert len(cod) == 6 and cod == codigo_lista(fav["id"]), cod
        assert cod != codigo_lista(fav["id"] + 1), "el ID seria adivinable"
        assert cod in html, "la vista de la lista no muestra el ID"

        # y desde el panel se llega a la seleccion con ese ID
        r = c.get("/panel/buscar?id=" + cod)
        assert r.status_code == 302 and ("/lista/%d" % fav["id"]) in r.headers["Location"], \
            r.headers.get("Location")
        # en minuscula tambien, que es como lo va a pegar cualquiera
        assert c.get("/panel/buscar?id=" + cod.lower()).status_code == 302
        # un ID que no existe avisa en vez de romper
        assert c.get("/panel/buscar?id=ZZZZZZ").status_code == 404
        assert c.get("/panel/buscar?id=").status_code == 404

        # los nombres, separados por espacio y listos para copiar.
        # `con,coma.jpg` es el caso que importa: separados por coma se
        # partiria en dos
        assert 'id="noms"' in html and "Copiar los 2 nombres" in html
        cuerpo = html.split('id="noms"', 1)[1].split(">", 1)[1].split("</textarea>")[0]
        assert "bcopiar2" in html
        # el boton "sin extension" tiene que partir por lo mismo que se junto,
        # o devuelve un pegote solo con todos los nombres adentro
        assert '.split(" ")' in html, "el JS sigue partiendo por salto de linea"
        assert chr(10) not in cuerpo.strip(), "quedo un salto: " + repr(cuerpo)
        assert sorted(cuerpo.split(" ")) == ["con,coma.jpg", "uno.jpg"], cuerpo
        # ...pero no la de otra galeria, ni una inventada
        assert c.get(f"/panel/aaa/lista/{fav['id']}").status_code == 404,             "FUGA: vio la lista de otra galeria"
        assert c.get("/panel/ccc/lista/99999").status_code == 404

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
        g2 = cx2.execute("SELECT id, slug FROM galeria "
                         "WHERE nombre='Desde el evento'").fetchone()
        assert g2 is not None, "no creo la galeria"
        assert cx2.execute("SELECT COUNT(*) FROM galeria").fetchone()[0] == n0 + 1
        assert cx2.execute("SELECT COUNT(*) FROM foto WHERE galeria_id=?",
                           (g2["id"],)).fetchone()[0] == 2, "no puso las 2 fotos"
        cx2.close()

        # ------------------------- agregar y quitar fotos de una galeria viva
        s2 = g2["slug"]

        def cuantas():
            cx = conectar()
            n = cx.execute("SELECT COUNT(*) FROM foto WHERE galeria_id = ?",
                           (g2["id"],)).fetchone()[0]
            cx.close()
            return n

        html = c.get("/panel/%s/fotos" % s2)
        assert html.status_code == 200
        html = html.get_data(as_text=True)
        assert c.get("/panel/no-existe/fotos").status_code == 404
        # la pantalla de quitar tiene visor, y la miniatura entra en el
        assert 'id="pv"' in html and "data-ver=" in html, "sin visor"
        # y la imagen NO puede estar dentro del label de la casilla: tocarla
        # para verla en grande marcaria la foto para quitarla sin querer
        assert '<label class="celda elegir"' not in html, "la foto marca al tocarla"
        # el panel tambien se puede dar vuelta, y el paginado se lleva el orden
        rev = c.get("/panel/%s/fotos?orden=nuevas" % s2).get_data(as_text=True)
        ids_asc = re.findall(r'name="foto" value="(\d+)"', html)
        ids_rev = re.findall(r'name="foto" value="(\d+)"', rev)
        assert ids_asc and ids_rev == ids_asc[::-1], (ids_asc, ids_rev)

        # el mismo evento otra vez no puede duplicar ninguna foto
        assert c.post("/panel/%s/agregar" % s2,
                      data={"evento": "7"}).status_code == 302
        assert cuantas() == 2, "agregar el mismo evento duplico fotos"

        # una carpeta suelta si suma, y el orden sigue desde la ultima
        assert c.post("/panel/%s/agregar" % s2,
                      data={"dir": "_subidas/prueba"}).status_code == 302
        assert cuantas() == 3, "no agrego la foto de la carpeta"
        cx2 = conectar()
        ords = [r["orden"] for r in cx2.execute(
            "SELECT orden FROM foto WHERE galeria_id = ? ORDER BY orden", (g2["id"],))]
        assert ords == [0, 1, 2], ords
        ids = [r["id"] for r in cx2.execute(
            "SELECT id FROM foto WHERE galeria_id = ? ORDER BY orden", (g2["id"],))]
        ajeno = cx2.execute("SELECT id FROM foto WHERE galeria_id <> ? LIMIT 1",
                            (g2["id"],)).fetchone()["id"]
        # la tapa apunta a una que vamos a sacar
        cx2.execute("UPDATE galeria SET portada_foto_id = ? WHERE id = ?",
                    (ids[0], g2["id"]))
        cx2.commit()
        cx2.close()

        # se quita la marcada, y el id de OTRA galeria metido a mano no borra
        # nada: sin el guard por galeria_id el formulario seria una puerta
        c.post("/panel/%s/fotos/quitar" % s2,
               data={"foto": [str(ids[0]), str(ajeno)]})
        assert cuantas() == 2, "no saco la foto marcada"
        cx2 = conectar()
        assert cx2.execute("SELECT 1 FROM foto WHERE id = ?", (ajeno,)).fetchone(), \
            "FUGA: borro una foto de otra galeria"
        # y la tapa no puede quedar apuntando a una foto que ya no esta
        assert cx2.execute("SELECT portada_foto_id FROM galeria WHERE id = ?",
                           (g2["id"],)).fetchone()[0] is None, "la tapa quedo colgada"
        cx2.close()

        # sin nada marcado no pasa nada
        c.post("/panel/%s/fotos/quitar" % s2, data={})
        assert cuantas() == 2

    # ------------------------------ eventos publicos, venta y transferencia
    cx = conectar()
    cx.execute("UPDATE galeria SET venta = 1, fecha = '2026-08-20' "
               "WHERE slug = 'ccc'")
    cx.execute("UPDATE galeria SET venta = 1 WHERE slug = 'ddd'")  # vencida
    cx.commit()
    cx.close()

    with app.test_client() as c:
        html = c.get("/eventos").get_data(as_text=True)
        assert "/g/ccc" in html, "la galeria en venta no aparece en /eventos"
        assert "/g/aaa" not in html, "FUGA: se listo una galeria que no esta en venta"
        assert "/g/ddd" not in html, "FUGA: se listo una galeria vencida"
        assert "/g/ccc" in c.get("/").get_data(as_text=True), "la portada no la lista"

        # buscadores: la vidriera se indexa, lo privado no. El default de
        # BASE_HTML es noindex, asi que una pagina nueva nace oculta
        for p in INDEXABLES:
            h = c.get(p).get_data(as_text=True).split("<style>")[0]
            assert "noindex" not in h, "%s no se puede indexar" % p
            assert 'rel="canonical" href="%s%s"' % (SITIO, p) in h, p
        for p in ("/g/ccc", "/calculadora", "/panel"):
            assert "noindex" in c.get(p).get_data(as_text=True).split("<style>")[0], \
                "FUGA: %s se puede indexar" % p
        rb = c.get("/robots.txt").get_data(as_text=True)
        assert "Disallow: /panel" in rb and "Disallow: /g/" not in rb, rb
        assert "%s/eventos</loc>" % SITIO in c.get("/sitemap.xml").get_data(as_text=True)

        # tarjeta de evento con srcset: ?w= solo acepta los anchos de ANCHOS,
        # y uno inventado cae en la tapa de siempre en vez de generar otra
        assert "/portada/ccc?h=" in html and "&amp;w=480 480w" in html, "sin srcset"
        # el muro de email tampoco baja la tapa entera
        gate = c.get("/g/ccc").get_data(as_text=True)
        assert 'class="fondo"' not in gate or ("/portada/ccc?h=" in gate and "w=480 480w" in gate),             "la tapa del muro se baja entera"

        assert '<source type="image/webp"' in html and "f=webp&amp;w=480" in html, \
            "la tarjeta no ofrece WebP"
        chica = c.get("/portada/ccc?w=480")
        assert chica.status_code == 200
        assert Image.open(io.BytesIO(chica.data)).width <= 480
        rara = c.get("/portada/ccc?w=333")
        assert Image.open(io.BytesIO(rara.data)).width != 333, "acepto un ancho libre"

        # WebP de verdad, y el cache largo solo con la URL versionada: sin ?h=
        # la tapa sigue con su dia, porque nada avisaria que cambio
        h = url_tapa(conectar().execute(
            "SELECT * FROM galeria WHERE slug = 'ccc'").fetchone())
        w = c.get(h + "&f=webp&w=480")
        assert w.mimetype == "image/webp", w.mimetype
        assert Image.open(io.BytesIO(w.data)).format == "WEBP"
        assert "immutable" in w.headers["Cache-Control"], w.headers["Cache-Control"]
        assert "max-age=31536000" in w.headers["Cache-Control"]
        sin_v = c.get("/portada/ccc").headers["Cache-Control"]
        assert "immutable" not in sin_v and "max-age=86400" in sin_v, sin_v
        # la version cambia si cambia lo que dibuja la tapa
        cx = conectar()
        cx.execute("UPDATE galeria SET marca_texto = 'otra' WHERE slug = 'ccc'")
        assert url_tapa(cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'"
                                   ).fetchone()) != h, "la tapa nueva no cambia de URL"
        cx.rollback()
        cx.close()

        # cambiar la marca borra tambien la tapa chica y su WebP
        g_ = conectar().execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone()
        if g_["marca"]:
            f_ = conectar().execute(
                "SELECT ruta FROM foto WHERE galeria_id = ? AND (? IS NULL OR id = ?) "
                "ORDER BY orden, id LIMIT 1", (g_["id"], g_["portada_foto_id"],
                                               g_["portada_foto_id"])).fetchone()
            jpg_ = ruta_cache(f_["ruta"], MARCA if g_["marca_texto"] is None
                              else g_["marca_texto"], logos_de(g_), 480, 0.017,
                              firma_de(g_))
            assert jpg_.with_suffix(".webp").exists(), "el webp no quedo al lado"
            with app.app_context():
                limpiar_cache(g_["id"], g_["marca_texto"], logos_de(g_), firma_de(g_))
            assert not jpg_.exists() and not jpg_.with_suffix(".webp").exists(), \
                "quedaron huerfanas la tapa chica o su webp"

        # la vidriera: version por mtime, reemplazar la foto cambia la URL
        MUESTRAS.mkdir(exist_ok=True)
        Image.new("RGB", (1600, 1000), (40, 90, 60)).save(MUESTRAS / "a b.jpg")
        um = url_muestra("a b.jpg")
        assert um.startswith("/muestra/a%20b.jpg?h="), um
        m_ = c.get(um + "&f=webp&w=800")
        assert m_.mimetype == "image/webp" and "immutable" in m_.headers["Cache-Control"]
        assert Image.open(io.BytesIO(m_.data)).width == 800
        os.utime(MUESTRAS / "a b.jpg", (1, 1))
        assert url_muestra("a b.jpg") != um, "reemplazar la muestra no cambia la URL"
        assert "max-age=604800" in c.get("/muestra/a%20b.jpg").headers["Cache-Control"]
        assert c.get("/muestra/a%20b.jpg?f=webp").mimetype == "image/webp"

        # icono y cabeceras de seguridad, en todas las respuestas
        ico = c.get("/favicon.ico")
        assert ico.status_code == 200 and Image.open(io.BytesIO(ico.data)).format == "ICO"
        assert Image.open(io.BytesIO(c.get("/apple-touch-icon.png").data)).size == (180, 180)
        for r_ in (c.get("/"), c.get("/g/ccc"), w):
            assert r_.headers.get("Strict-Transport-Security") == "max-age=31536000"
            assert r_.headers.get("X-Frame-Options") == "SAMEORIGIN"
            assert r_.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
            # la CSP va en modo reportar: avisa, no bloquea
            csp = r_.headers.get("Content-Security-Policy-Report-Only") or ""
            assert "nonce-" in csp and "report-uri /csp-reportes" in csp, csp
            assert not r_.headers.get("Content-Security-Policy"), "la CSP no debe bloquear todavia"
            assert r_.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
            assert "camera=()" in (r_.headers.get("Permissions-Policy") or "")
            assert r_.headers.get("X-Content-Type-Options") == "nosniff"

        # comprar: hay que haber entrado, y no existe si la venta esta apagada
        assert c.get("/g/ccc/comprar").status_code == 302, "compro sin entrar"
        assert c.get("/g/aaa/comprar").status_code == 404, "compro con la venta apagada"
        c.post("/g/ccc/entrar", data={"email": "z@y.com", "clave": "secreta"})
        assert c.get("/g/ccc/comprar").status_code == 200
        assert "noindex" in c.get("/g/ccc/comprar").get_data(as_text=True), \
            "FUGA: la pagina de compra se puede indexar"
        # -------------------------------------------------------- carrito
        # el carrito no es una lista que se administre: no sale entre las
        # listas del cliente, pero se cuenta y se puede abrir como galeria
        d = c.get("/api/listas/ccc").get_json()
        assert CARRITO not in [l["nombre"] for l in d["listas"]], d
        assert d["carrito"] == 0 and d["carrito_id"], d
        car = d["carrito_id"]
        # y no se puede prender: si recibiera me-gusta dejaria de ser el carrito
        assert c.post(f"/api/listas/ccc/{car}/activa",
                      json={"activa": True}).status_code == 400

        r = c.post("/api/carrito", json={"foto_id": id_uno}).get_json()
        assert r == {"en": True, "carrito": 1}, r
        assert c.get("/api/listas/ccc").get_json()["carrito"] == 1
        # el mismo boton lo saca
        assert c.post("/api/carrito", json={"foto_id": id_uno}).get_json() == \
            {"en": False, "carrito": 0}
        c.post("/api/carrito", json={"foto_id": id_uno})

        # el carrito de OTRA galeria no se toca desde aca
        assert c.post("/api/carrito", json={"foto_id": id_venc}).status_code == 404

        # una sola foto cae en el tramo mas chico, no se cobra suelta
        txt = c.get("/g/ccc/comprar").get_data(as_text=True)
        assert pesos(precio_de(1, tramos())) in txt, "una foto sola no cayo en un tramo"
        assert CARRITO in txt, "no compra el carrito por defecto"

        # los pasos salen de los textos editables, con {n} y {total} puestos
        txt = c.get("/g/ccc/comprar").get_data(as_text=True)
        assert "calidad indicada" in txt, "no uso el texto del paso 3"
        assert "{n}" not in txt and "{total}" not in txt, "quedo un marcador crudo"
        c.post("/panel/textos", data={"compra_paso3": "Te mando las {n} y listo"})
        txt = c.get("/g/ccc/comprar").get_data(as_text=True)
        assert "Te mando las 1 y listo" in txt, "no tomo el paso editado"
        c.post("/panel/textos", data={"compra_paso3": DEFAULTS["compra_paso3"]})
        # un paso vacio no deja un renglon suelto
        c.post("/panel/textos", data={"compra_paso2": ""})
        assert c.get("/g/ccc/comprar").get_data(as_text=True).count("<li>") == 2
        c.post("/panel/textos", data={"compra_paso2": DEFAULTS["compra_paso2"]})

        # se puede comprar cualquier lista propia, no solo el carrito
        fav = [l for l in c.get("/api/listas/ccc").get_json()["listas"]
               if l["nombre"] == FAVORITOS][0]
        c.post("/api/megusta", json={"foto_id": id_coma})
        txt = c.get(f"/g/ccc/comprar?lista={fav['id']}").get_data(as_text=True)
        assert "1 foto" in txt, "no compro la lista pedida"
        # y una lista ajena no: cae en el carrito en vez de mostrarla
        txt = c.get("/g/ccc/comprar?lista=99999").get_data(as_text=True)
        assert CARRITO in txt, "una lista ajena no puede elegirse"

        # y los precios se ven ANTES de comprar: en la vidriera y en la galeria
        assert pesos(3000) in c.get("/eventos").get_data(as_text=True), \
            "la vidriera no muestra los precios del evento"
        html = c.get("/g/ccc").get_data(as_text=True)
        assert 'class="tarifas"' in html and pesos(3000) in html, \
            "la galeria no muestra sus precios"
        assert 'class="car ' in html, "sin boton de agregar al carrito"
        assert "Ver mi seleccion" not in html
        # el boton de la tapa tiene a donde bajar
        assert 'href="#abajo"' in html and 'id="abajo"' in html
        # las indicaciones de entrega, con el default del codigo
        assert "2560 x 1440" in html, "la galeria no dice que resolucion entrega"
        assert "TransferNow" in html, "no dice las opciones de entrega"

    # lo propio de la galeria pisa al texto general, campo por campo
    cx = conectar()
    cx.execute("UPDATE galeria SET ent_resolucion = ?, ent_edicion = ? "
               "WHERE slug = 'ccc'", ("4K nativo", "completa"))
    cx.commit()
    cx.close()
    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "z@y.com", "clave": "secreta"})
        html = c.get("/g/ccc").get_data(as_text=True)
        assert "4K nativo" in html, "no tomo la resolucion de la galeria"
        assert "2560 x 1440" not in html, "el general se colo encima de la galeria"
        # lo que la galeria no pisa sigue saliendo del general
        assert "TransferNow" in html, "perdio los plazos generales"
        # y el pack de edicion se despliega con sus items
        assert DEFAULTS["edicion_completa_nom"] in html, "no nombro el pack"
        assert "Retoque de piel" in html, "no listo lo que incluye la edicion"
        assert "Balance de blancos" not in html, "mezclo el pack basico"
    # los campos sueltos: lo de la galeria pisa al general, lo vacio lo hereda
    with app.test_request_context():
        vacia = {"ent_resolucion": None, "ent_metodo": None, "ent_plazo": None,
                 "ent_edicion": None}
        etiquetas = {f[0]: f for f in indicaciones(vacia)}
        assert etiquetas[DEFAULTS["ent_resolucion_nom"]][1] == \
            DEFAULTS["ent_resolucion"], "no heredo la resolucion general"
        # sin pack elegido no se dibuja la fila de edicion
        assert DEFAULTS["ent_edicion_nom"] not in etiquetas

        propia = dict(vacia, ent_resolucion="4K nativo", ent_edicion="basica")
        etiquetas = {f[0]: f for f in indicaciones(propia)}
        assert etiquetas[DEFAULTS["ent_resolucion_nom"]][1] == "4K nativo"
        ed = etiquetas[DEFAULTS["ent_edicion_nom"]]
        assert ed[1] == DEFAULTS["edicion_basica_nom"] and len(ed[2]) == 6, ed

        # los detalles: vacios no dibujan fila, con texto si
        assert DEFAULTS["ent_detalles_nom"] not in etiquetas
        con = {f[0]: f for f in indicaciones(dict(vacia, ent_detalles="Sin flash"))}
        assert con[DEFAULTS["ent_detalles_nom"]][1] == "Sin flash"

        # un pack que no existe se ignora en vez de romper la galeria
        assert edicion_de(dict(vacia, ent_edicion="ninja")) is None
        assert edicion_de(vacia) is None

        # opciones de entrega: que se guarda...
        from werkzeug.datastructures import MultiDict as MD
        F = lambda **kw: MD(list(kw.items()))   # MultiDict ya no toma kwargs
        e = entrega_de_form
        assert e(MD(), "ent_resolucion") is None, "sin elegir debe heredar"
        assert e(F(ent_resolucion_modo="4K"), "ent_resolucion") == "4K"
        assert e(F(ent_resolucion_modo="8K"), "ent_resolucion") is None
        assert e(F(ent_resolucion_modo="otro", ent_resolucion_otro="  "),
                 "ent_resolucion") == OCULTO, "personalizado vacio = no aparece"
        assert e(F(ent_plazo_modo=OCULTO), "ent_plazo") == OCULTO
        assert e(MD([("ent_metodo_modo", "elegir"), ("ent_metodo_op", "WeTransfer"),
                     ("ent_metodo_op", "Google Drive"), ("ent_metodo_op", "Paloma")]),
                 "ent_metodo") == "Google Drive, WeTransfer"
        assert e(F(ent_plazo_modo="dias", ent_plazo_dias="7"), "ent_plazo") == "7 días"
        assert e(F(ent_plazo_modo="dias", ent_plazo_dias="1"), "ent_plazo") == "1 día"
        assert e(F(ent_plazo_modo="dias", ent_plazo_dias="-3"), "ent_plazo") == OCULTO
        # ...y como vuelve al selector: ida y vuelta sin perder nada
        for col, v in (("ent_resolucion", "4K"), ("ent_metodo", "Google Drive, WeTransfer"),
                       ("ent_plazo", "7 días"), ("ent_resolucion", "1616 x 1080 pixeles")):
            modo, extra = entrega_a_form(col, v)
            form = MD([(col + "_modo", modo), (col + "_otro", extra),
                       ("ent_plazo_dias", extra)]
                      + [("ent_metodo_op", x) for x in (extra if modo == "elegir" else [])])
            assert e(form, col) == v, (col, v, modo, extra)
        # lo escrito a mano antes de las opciones se conserva como personalizado
        assert entrega_a_form("ent_resolucion", "1616 x 1080 pixeles") == \
            ("otro", "1616 x 1080 pixeles")
        # y OCULTO saca la fila de lo que ve el cliente
        filas = {f[0] for f in indicaciones(dict(vacia, ent_plazo=OCULTO))}
        assert DEFAULTS["ent_plazo_nom"] not in filas, "no mostrar dejo la fila"
    cx = conectar()
    cx.execute("UPDATE galeria SET ent_resolucion = NULL, ent_edicion = NULL "
               "WHERE slug = 'ccc'")
    cx.commit()
    cx.close()
    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "z@y.com", "clave": "secreta"})
        assert "2560 x 1440" in c.get("/g/ccc").get_data(as_text=True), \
            "vaciar los campos no volvio a las indicaciones generales"

    # un evento sin venta pero con descarga no muestra precios: muestra que es libre
    cx = conectar()
    cx.execute("UPDATE galeria SET venta = 0, descargas = 1 WHERE slug = 'ccc'")
    cx.commit()
    cx.close()
    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "z@y.com", "clave": "secreta"})
        html = c.get("/g/ccc").get_data(as_text=True)
        assert DEFAULTS["gal_gratis_texto"] in html, "el evento libre no lo dice"
        assert 'class="packs"' not in html, "un evento libre no puede mostrar precios"
        # ni carrito, ni boton de comprar, ni la ruta: no hay nada que vender
        assert 'class="car ' not in html, "boton de carrito en un evento libre"
        assert 'class="bcomprar"' not in html, "boton de comprar en un evento libre"
        assert c.get("/g/ccc/comprar").status_code == 404, \
            "FUGA: se compra una galeria de descarga libre"
        # y la vidriera tampoco le pone precio
        assert pesos(3000) not in c.get("/eventos").get_data(as_text=True), \
            "la vidriera le puso precio a un evento de descarga libre"
    cx = conectar()
    cx.execute("UPDATE galeria SET venta = 1, descargas = 0 WHERE slug = 'ccc'")
    cx.commit()
    cx.close()

    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "z@y.com", "clave": "secreta"})

    # apagar los interruptores tiene que sacarla de la vidriera y del cobro
    cx = conectar()
    cx.execute("UPDATE galeria SET venta = 0 WHERE slug = 'ccc'")
    cx.commit()
    cx.close()
    with app.test_client() as c:
        assert "/g/ccc" not in c.get("/eventos").get_data(as_text=True), \
            "apagar la venta no la saco de /eventos"
        c.post("/g/ccc/entrar", data={"email": "z@y.com", "clave": "secreta"})
        assert c.get("/g/ccc/comprar").status_code == 404, \
            "FUGA: se compra con la venta apagada"

    # los criterios de orden: la expresion sale del codigo, nunca de la URL
    assert como_ordenar({}) == ("captura", "ASC")
    assert como_ordenar({"por": "captura", "dir": "desc"}) == ("captura", "DESC")
    # lo mas elegido arranca al reves, que es como se mira
    assert como_ordenar({"por": "likes"}) == ("likes", "DESC")
    # los criterios sacados ya no existen: caen en el de siempre
    assert como_ordenar({"por": "descargas"}) == ("captura", "ASC")
    assert como_ordenar({"por": "orden"}) == ("captura", "ASC")
    assert set(ORDENES) == {"captura", "likes"}, ORDENES
    # basura en la URL cae en el orden de siempre, no en un ORDER BY inventado
    assert como_ordenar({"por": "foto.id; DROP TABLE foto"}) == ("captura", "ASC")
    assert como_ordenar({"dir": "; DELETE FROM foto"}) == ("captura", "ASC")

    assert fecha_corta("2026-08-20T19:00") == "20/08/2026"
    assert fecha_corta(None) == "" and fecha_corta("cualquiera") == "cualquiera"

    # ------------------------------------------------- precios por tramo
    d = tramos()
    assert d == [(5, 3000), (10, 5500), (20, 10000), (50, 20000)], d
    assert [precio_de(n, d) for n in (0, 1, 5, 6, 10, 11, 20, 21, 50)] == \
        [0, 3000, 3000, 5500, 5500, 10000, 10000, 20000, 20000]
    # arriba del ultimo tramo no se regala ni se dispara: 20000 + 10 a $400
    assert precio_de(60, d) == 24000, precio_de(60, d)

    cx = conectar()
    cx.execute("UPDATE galeria SET precios = ? WHERE slug = 'ccc'",
               ("3 $2.000\n8, 4000\nesta linea no se entiende",))
    cx.commit()
    g = cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone()
    assert tramos(g) == [(3, 2000), (8, 4000)], tramos(g)
    # y si no se entiende NADA, la galeria no se queda sin precios
    cx.execute("UPDATE galeria SET precios = 'cualquier cosa' WHERE slug = 'ccc'")
    cx.commit()
    g = cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone()
    assert tramos(g) == d, tramos(g)
    cx.execute("UPDATE galeria SET precios = NULL WHERE slug = 'ccc'")
    cx.commit()
    cx.close()

    # los logos de una galeria: solo los que existen, y como mucho MAX_LOGOS
    assert logos_de(None) == ()
    assert logos_de({"logos": "rb.png\nno-existe.png"}) == ("rb.png",)
    assert len(logos_de({"logos": "\n".join(["rb.png", "liga.png"] * 4)})) == MAX_LOGOS

    # ------------------------------------------------ filtro por partido
    cx = conectar()
    gccc = cx.execute("SELECT id FROM galeria WHERE slug = 'ccc'").fetchone()["id"]
    fids = [r["id"] for r in cx.execute(
        "SELECT id FROM foto WHERE galeria_id = ? ORDER BY orden", (gccc,))]
    cx.execute("UPDATE foto SET grupo = 'Partido 1', grupo_col = '#111,#222' "
               "WHERE id = ?", (fids[0],))
    cx.execute("UPDATE foto SET grupo = 'Partido 2', grupo_col = '#333,#444' "
               "WHERE id IN (?, ?)", (fids[1], fids[2]))
    cx.commit()
    cx.close()

    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "secreta"})
        html = c.get("/g/ccc").get_data(as_text=True)
        assert "Partido 1" in html and "Partido 2" in html, "no ofrece los partidos"
        assert "#333" in html, "no dibuja el color del partido"
        # filtrar deja solo las de ese partido
        assert len(re.findall(r'/p/([0-9]+)"',
                              c.get("/g/ccc?grupo=Partido+2").get_data(as_text=True))) == 2
        assert len(re.findall(r'/p/([0-9]+)"',
                              c.get("/g/ccc?grupo=Partido+1").get_data(as_text=True))) == 1
        # y el scroll infinito se lleva el filtro puesto
        assert 'const GRUPO = "Partido 2"' in \
            c.get("/g/ccc?grupo=Partido+2").get_data(as_text=True)
        # un partido inventado muestra todas, no cero
        assert len(re.findall(r'/p/([0-9]+)"',
                              c.get("/g/ccc?grupo=Partido+9").get_data(as_text=True))) == 3

    cx = conectar()
    cx.execute("UPDATE foto SET grupo = NULL, grupo_col = NULL WHERE galeria_id = ?",
               (gccc,))
    cx.commit()
    cx.close()

    # ningun escape octal accidental en las plantillas: "\\2014" dentro de un
    # string de Python es chr(0o201) + "4", y en pantalla sale un cuadrito
    for nombre in dir(sys.modules[__name__]):
        if not nombre.isupper():
            continue
        v = getattr(sys.modules[__name__], nombre)
        if isinstance(v, str):
            malos = [c for c in v if ord(c) < 32 and c not in "\n\t\r"]
            assert not malos, (nombre, [hex(ord(c)) for c in malos[:3]])

    # toda plantilla tiene que compilar: un error de sintaxis de Jinja tira la
    # pagina ENTERA y recien se ve cuando alguien la abre (paso con `.4`)
    for nombre in dir(sys.modules[__name__]):
        v = getattr(sys.modules[__name__], nombre)
        if nombre.isupper() and isinstance(v, str) and ("{%" in v or "{{" in v):
            try:
                app.jinja_env.from_string(v)
            except Exception as e:
                raise AssertionError("%s no compila: %s" % (nombre, e))

    # dos `let` con el mismo nombre en el scope de arriba del <script> son un
    # SyntaxError, y eso no rompe una funcion: rompe el script ENTERO, asi que
    # se cae el visor, los me-gusta y el scroll de una sola vez. Paso de
    # verdad con `cargando`, que ya usaba el scroll infinito.
    for nombre in ("GRILLA", "PANEL_GAL", "PANEL_VISOR"):
        js = getattr(sys.modules[__name__], nombre, "")
        if "<script>" not in js:
            continue
        decl = _declaraciones_top(js[js.index("<script>"):])
        repes = sorted({n for n in decl if decl.count(n) > 1})
        assert not repes, "%s declara dos veces en el mismo scope: %s" % (nombre, repes)

    # carpetas: junta galerias bajo un link, y borrar una galeria la saca sola
    from werkzeug.datastructures import MultiDict as MD
    with app.test_client() as c:
        cx_ = conectar()
        gids = []
        for n in ("CarpA", "CarpB", "CarpC"):
            sl, _p, _f = crear_galeria(cx_, n, [], verificar=False)
            gid = cx_.execute("SELECT id FROM galeria WHERE slug = ?", (sl,)).fetchone()[0]
            cx_.execute("INSERT INTO foto (galeria_id, ruta) VALUES (?, '/x.jpg')", (gid,))
            gids.append(gid)
        cx_.execute("UPDATE galeria SET tipo = 'amigos' WHERE id = ?", (gids[0],))
        cx_.commit()
        assert "Amigos" in c.get("/panel").get_data(as_text=True), "no agrupo Amigos"
        r = c.post("/panel/carpetas", data=MD([("nombre", "Uandes"), ("g", str(gids[0])),
                                               ("g", str(gids[1])), ("g", "999999")]))
        assert r.status_code == 302
        csl = cx_.execute("SELECT slug FROM carpeta WHERE nombre = 'Uandes'").fetchone()[0]
        html = c.get("/c/" + csl).get_data(as_text=True)
        assert "CarpA" in html and "CarpB" in html and "CarpC" not in html, "carpeta mal armada"
        assert c.get("/panel/carpetas").status_code == 200
        assert "Uandes" in c.get("/panel").get_data(as_text=True)
        # reescribir: queda solo C
        c.post("/panel/carpetas", data=MD([("slug", csl), ("nombre", "Uandes2"),
                                           ("g", str(gids[2]))]))
        html = c.get("/c/" + csl).get_data(as_text=True)
        assert "Uandes2" in html and "CarpC" in html and "CarpA" not in html
        # borrar la galeria la saca de la carpeta; borrar la carpeta no toca galerias
        c.post("/panel/%s/borrar" % cx_.execute(
            "SELECT slug FROM galeria WHERE id = ?", (gids[2],)).fetchone()[0])
        assert "CarpC" not in c.get("/c/" + csl).get_data(as_text=True)
        c.post("/panel/carpetas/%s/borrar" % csl)
        assert c.get("/c/" + csl).status_code == 404
        assert cx_.execute("SELECT COUNT(*) FROM galeria WHERE id IN (?, ?)",
                           gids[:2]).fetchone()[0] == 2, "borrar carpeta borro galerias"
        cx_.execute("DELETE FROM galeria WHERE id IN (?, ?)", gids[:2])
        cx_.commit()
        cx_.close()

    # WAL o la grilla se cae sola: 300 fotos pidiendo a la vez mientras se
    # anota una vista daban "database is locked" y un 500 por foto
    cx_ = conectar()
    assert cx_.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    cx_.close()
    # y si igual se traba, la vista se pierde pero la foto se sirve igual
    with app.test_request_context("/p/1"):
        db().execute("PRAGMA query_only = ON")      # toda escritura falla
        try:
            anotar_vista(1, 1, False)               # no tiene que levantar
        finally:
            db().execute("PRAGMA query_only = OFF")

    # cada pagina lleva su nonce y los <script> lo llevan puesto
    with app.test_client() as c:
        r_ = c.get("/")
        html = r_.get_data(as_text=True)
        nonce = re.search(r"'nonce-([^']+)'", r_.headers["Content-Security-Policy-Report-Only"]).group(1)
        assert "<script>" not in html, "quedo un <script> sin nonce"
        assert ('<script nonce="%s">' % nonce) in html or "<script" not in html
        # dos visitas, dos nonce distintos
        otro = c.get("/").headers["Content-Security-Policy-Report-Only"]
        assert otro != r_.headers["Content-Security-Policy-Report-Only"], "el nonce se repite"
        # el buzon acepta un aviso y lo anota
        assert c.post("/csp-reportes", json={"csp-report": {
            "document-uri": "https://x/y", "violated-directive": "script-src-attr",
            "blocked-uri": "inline"}}).status_code == 204
        assert "script-src-attr" in csp_log().read_text(encoding="utf-8")
        csp_log().unlink()

    assert lineas("a\n\n b \n") == ["a", "b"]
    assert parrafos("uno\n\ndos") == ["uno", "dos"]

    # ------------------------------------------ textos editables desde el panel
    with app.test_client() as c:
        assert c.get("/sobre-mi").status_code == 200
        assert c.get("/calculadora").status_code == 200
        # la barra fija tiene que estar en las cuatro publicas, y en ninguna
        # galeria de cliente (ahi manda la cabecera de la galeria)
        for u in ("/", "/eventos", "/sobre-mi", "/calculadora"):
            assert 'class="barra"' in c.get(u).get_data(as_text=True), u
        # la calculadora vive en su pagina y en la barra, nunca en la portada
        assert DEFAULTS["calc_texto"] not in c.get("/").get_data(as_text=True)
        assert DEFAULTS["calc_texto"] in c.get("/calculadora").get_data(as_text=True)
        assert DEFAULTS["mi_titulo"] in c.get("/sobre-mi").get_data(as_text=True)
        assert c.get("/panel/textos").status_code == 200
        assert DEFAULTS["hero_titulo"] in c.get("/").get_data(as_text=True)
        assert c.post("/panel/textos",
                      data={"hero_titulo": "Titulo cambiado"}).status_code == 302
        html = c.get("/").get_data(as_text=True)
        assert "Titulo cambiado" in html, "no tomo el texto editado"
        assert DEFAULTS["hero_titulo"] not in html

        # vaciar el titulo de un bloque lo saca de la portada
        c.post("/panel/textos", data={"como_titulo": ""})
        assert DEFAULTS["como_titulo"] not in c.get("/").get_data(as_text=True), \
            "el bloque vaciado sigue dibujandose"

        # {precio} sale rellenado con lo que se cobra de verdad
        c.post("/panel/textos", data={"hero_bajada": "bajada de prueba"})
        assert "bajada de prueba" in c.get("/").get_data(as_text=True)

        # guardar el mismo default no deja fila: asi el texto sigue el codigo
        c.post("/panel/textos", data={"hero_bajada": DEFAULTS["hero_bajada"]})
        cx = conectar()
        assert cx.execute("SELECT 1 FROM texto WHERE clave='hero_bajada'"
                          ).fetchone() is None, "guardo el default como si fuera edicion"
        cx.close()

        # y restaurar vuelve todo a como estaba
        assert c.post("/panel/textos/restaurar").status_code == 302
        html = c.get("/").get_data(as_text=True)
        assert "Titulo cambiado" not in html and DEFAULTS["hero_titulo"] in html
        assert DEFAULTS["como_titulo"] in html, "restaurar no devolvio el bloque"

    # --------------------------- la marca vieja no se queda en el cache
    with app.test_client() as c:
        c.post("/panel/ccc/ajustes", data={"marca_texto": MARCAS[0], "logos": "rb.png"})
        viejas = [preview(0, r, MARCAS[0], logos=("rb.png",)) for r in reales]
        assert all(p.exists() for p in viejas), "no genero las previews de prueba"

        # cambiar el logo tiene que borrar las de antes, no acumularlas
        c.post("/panel/ccc/ajustes", data={"marca_texto": MARCAS[0], "logos": "liga.png"})
        assert not any(p.exists() for p in viejas), \
            "quedaron en el cache las previews del logo viejo"

        # y cambiar el texto tambien
        nuevas = [preview(0, r, MARCAS[0], logos=("liga.png",)) for r in reales]
        c.post("/panel/ccc/ajustes", data={"marca_texto": MARCAS[1], "logos": "liga.png"})
        assert not any(p.exists() for p in nuevas), \
            "quedaron en el cache las previews del texto viejo"

        # guardar sin tocar la marca no puede tirar el cache: seria regenerar
        # la galeria entera cada vez que se guarda cualquier otro ajuste
        quedan = [preview(0, r, MARCAS[1], logos=("liga.png",)) for r in reales]
        c.post("/panel/ccc/ajustes", data={"marca_texto": MARCAS[1],
                                           "logos": "liga.png", "descargas": "1"})
        assert all(p.exists() for p in quedan), "borro el cache sin que cambiara la marca"

        # la combinacion usada queda ofrecida en las otras galerias
        assert 'data-logos="liga.png"' in c.get("/panel/aaa").get_data(as_text=True), \
            "no ofrece las combinaciones de logos ya usadas"

    # ------------------------------------- firma en la foto que se entrega
    assert firma_de(None) is None
    assert firma_de({"firma": None, "firma_pos": None, "firma_tam": None}) is None
    # un logo borrado apaga la firma en vez de romper la descarga
    assert firma_de({"firma": "no-existe.png", "firma_pos": None,
                     "firma_tam": None}) is None
    assert firma_de({"firma": "liga.png", "firma_pos": "abajo-izq",
                     "firma_tam": 9}) == ("liga.png", "abajo-izq", 9)
    # lo que venga fuera de rango se acota, no revienta
    assert firma_de({"firma": "liga.png", "firma_pos": "atras",
                     "firma_tam": 900}) == ("liga.png", FIRMA_POS, 40)

    # una foto vertical sale ya girada y con la etiqueta EXIF en 1: si se
    # guardara la orientacion original, el visor la giraria una segunda vez
    vert = tmp / "vert.jpg"
    exv = Image.Exif()
    exv[274] = 6
    Image.new("RGB", (400, 300), (30, 60, 90)).save(vert, exif=exv)
    fv = tmp / "vert_firmada.jpg"
    firmar(vert, fv, "liga.png")
    sal = Image.open(fv)
    assert sal.size == (300, 400), sal.size
    assert (sal.getexif() or {}).get(274, 1) == 1, "saldria rotada dos veces"

    # las 9 posiciones caen en 9 lugares distintos y ninguna se sale de la foto
    vistos = set()
    for pp in POSICIONES:
        d = tmp / ("p_%s.jpg" % pp)
        firmar(vert, d, "liga.png", pp, 20)
        im9 = Image.open(d)
        assert im9.size == (300, 400), (pp, im9.size)
        vistos.add(Image.open(d).tobytes())
    assert len(POSICIONES) == 9 and len(vistos) == 9, len(vistos)

    # el cache distingue logo, posicion y tamano: sin eso cambiar la firma
    # seguiria entregando la copia de antes
    claves = {ruta_firma(str(vert), *f) for f in (
        ("liga.png", "arriba-der", 16), ("liga.png", "abajo-izq", 16),
        ("liga.png", "arriba-der", 20), ("rb.png", "arriba-der", 16))}
    assert len(claves) == 4, claves

    # sin firma se entrega el archivo original, sin tocar un byte
    assert entregable(str(vert), None) == str(vert)

    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "secreta"})
        base = {"descargas": "1", "marca_texto": MARCAS[0]}
        c.post("/panel/ccc/ajustes", data=base)
        assert c.get(f"/d/{id_uno}").data == Path(reales[0]).read_bytes(), \
            "sin firma la descarga tiene que ser el archivo original"

        c.post("/panel/ccc/ajustes",
               data=dict(base, firma="liga.png", firma_tam="20"))
        d = c.get(f"/d/{id_uno}")
        assert d.status_code == 200
        assert d.data != Path(reales[0]).read_bytes(), "no firmo la entrega"
        # misma foto, mismas medidas: la firma no recorta ni reescala
        assert (Image.open(io.BytesIO(d.data)).size
                == Image.open(reales[0]).size), "la firma cambio el tamano"
        # y el panel la puede mirar antes de que la baje un cliente
        assert c.get("/panel/ccc/firma").status_code == 200
        # la muestra sale reducida: al tamano de entrega costaria el doble y
        # se ve igual, porque la firma es un % del ancho
        m = Image.open(io.BytesIO(c.get("/panel/ccc/firma").data))
        assert m.width <= ANCHO_MUESTRA_FIRMA, m.size

        # se puede probar otra posicion SIN guardar, que es para lo que existe
        # el preview del panel
        a = c.get("/panel/ccc/firma?pos=abajo-der&tam=30").data
        b = c.get("/panel/ccc/firma?pos=arriba-izq&tam=30").data
        assert a != b, "el preview no hace caso a la posicion pedida"
        # un logo que no existe no se dibuja ni inventa una ruta
        assert c.get("/panel/ccc/firma?logo=../secreto.png").status_code == 404
        assert c.get("/panel/ccc/firma?logo=no-existe.png").status_code == 404
        # y las muestras estan en el panel, listas para el visor
        html = c.get("/panel/ccc").get_data(as_text=True)
        assert 'class="firmaprev"' in html and "data-base=" in html

        # sacarla vuelve a entregar el original
        c.post("/panel/ccc/ajustes", data=base)
        assert c.get(f"/d/{id_uno}").data == Path(reales[0]).read_bytes(), \
            "sacar la firma no devolvio el original"
        assert c.get("/panel/ccc/firma").status_code == 404

    # el calentado tiene que generar EXACTAMENTE lo que sirve /p/
    cx = conectar()
    cx.execute("UPDATE galeria SET marca = 1, marca_texto = ?, logos = 'liga.png', "
               "firma = 'rb.png', firma_pos = 'arriba-cen', firma_tam = 15 "
               "WHERE slug = 'ccc'", (MARCAS[0],))
    cx.commit()
    g0 = cx.execute("SELECT * FROM galeria WHERE slug = 'ccc'").fetchone()
    cx.close()
    assert (preview_de(g0, 1, str(orig))
            != preview(1, str(orig), g0["marca_texto"])),         "el calentado generaria un cache que /p/ no pide"
    # y clave_preview_de() tiene que apuntar EXACTAMENTE ahi sin generar nada:
    # el contador de calentado se apoya en ella, y si se separan, miente
    for _a in (None, ANCHO_VISOR):
        assert clave_preview_de(g0, str(orig), _a) == preview_de(g0, 1, str(orig), _a), \
            "clave_preview_de se desincronizo de preview_de (ancho=%s)" % _a

    # --------------------------------------------- franja horaria
    cx = conectar()
    gccc = cx.execute("SELECT id FROM galeria WHERE slug = 'ccc'").fetchone()["id"]
    ids = [r["id"] for r in cx.execute(
        "SELECT id FROM foto WHERE galeria_id = ? ORDER BY orden", (gccc,))]
    for k, fid in enumerate(ids):          # 18:00, 19:00, 20:00
        cx.execute("UPDATE foto SET tomada = ? WHERE id = ?",
                   ("2026-09-03 %02d:00:00" % (18 + k), fid))
    cx.commit()
    cx.close()

    with app.test_client() as c:
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "secreta"})
        todas = re.findall(r'/p/([0-9]+)"', c.get("/g/ccc").get_data(as_text=True))
        assert len(todas) == 3, todas

        # el rango va de 0 a 120 minutos; la primera hora deja 2 fotos
        html = c.get("/g/ccc?desde=0&hasta=60").get_data(as_text=True)
        una = re.findall(r'/p/([0-9]+)"', html)
        assert len(una) == 2, una
        # y el scroll infinito tiene que llevarse el filtro: sin esto la
        # segunda tanda vuelve sin filtrar y mete fotos de otras horas
        assert 'const DESDE = "0"' in html and 'const HASTA = "60"' in html

        # los sliders cruzados se ordenan solos en vez de devolver nada
        assert re.findall(r'/p/([0-9]+)"',
                          c.get("/g/ccc?desde=60&hasta=0").get_data(as_text=True)) == una
        # y basura en la URL no filtra nada
        assert len(re.findall(r'/p/([0-9]+)"',
                              c.get("/g/ccc?desde=x&hasta=y").get_data(as_text=True))) == 3

    # ------------------------------------------------ galeria de videos
    # Frigate de mentira: lo que importa es quien llega al clip, no Frigate.
    # Dos clips pegados (una visita, en el horario de comida) y uno suelto.
    def _ts(h, m, s=0):
        return datetime(2026, 9, 14, h, m, s).timestamp()
    EVENTOS = [{"id": "%.6f-aaa111" % _ts(13, 31), "label": "dog",
                "start_time": _ts(13, 31), "end_time": _ts(13, 31, 20)},
               {"id": "%.6f-bbb222" % _ts(13, 31, 40), "label": "cat",
                "start_time": _ts(13, 31, 40), "end_time": _ts(13, 31, 50)},
               {"id": "%.6f-ccc333" % _ts(16, 0), "label": "dog",
                "start_time": _ts(16, 0), "end_time": _ts(16, 0, 24)}]
    for e in EVENTOS:
        e.update(has_clip=True, retain_indefinitely=False, camera=FRIGATE_CAMARA)
    EV, EV2, SUELTO = (e["id"] for e in EVENTOS)
    llamadas, estado = [], {"camara": FRIGATE_CAMARA, "caido": False}

    def falso(ruta, metodo="GET", **query):
        llamadas.append((metodo, ruta, query))
        if estado["caido"]:
            raise urllib.error.URLError("caido")
        if ruta == "/api/events":
            # Frigate los devuelve del mas nuevo al mas viejo
            return io.BytesIO(json.dumps(
                sorted(EVENTOS, key=lambda e: -e["start_time"])).encode())
        for e in EVENTOS:
            if ruta == f"/api/events/{e['id']}":
                return io.BytesIO(json.dumps(dict(e, camera=estado["camara"])).encode())
        if ruta.endswith("/snapshot.jpg"):
            return io.BytesIO(b"\xff\xd8foto")
        if ruta.endswith("/clip.mp4"):
            return io.BytesIO(bytes(range(256)) * 20)
        if ruta.endswith("/retain"):
            return io.BytesIO(b"{}")
        raise urllib.error.URLError("ruta inesperada " + ruta)

    globals()["frigate"] = falso
    globals()["FF_PUENTE"] = tmp / "puente"    # sin esto escribe en el 8TB
    cx = conectar()
    cx.execute("INSERT INTO galeria (slug, nombre, creada, clave_hash) VALUES (?,?,?,?)",
               (VIDEOS_SLUG, "Perros", "2026-01-01", hash_clave("gua")))
    cx.commit()
    cx.close()
    V = f"/g/{VIDEOS_SLUG}/v/{EV}"
    with app.test_client() as c:
        # sin entrar no sale nada: ni clip, ni foto, ni el corazon
        assert c.get(V + ".mp4").status_code == 404, "FUGA: clip sin entrar"
        assert c.get(V + ".jpg").status_code == 404, "FUGA: foto sin entrar"
        assert c.post(V + "/guardar", json={"on": True}).status_code == 404
        c.post(f"/g/{VIDEOS_SLUG}/entrar", data={"email": "x@y.com", "clave": "mala"})
        assert c.get(V + ".mp4").status_code == 404, "FUGA: clip con clave equivocada"
        assert all(q[1] != f"/api/events/{EV}/clip.mp4" for q in llamadas)

        c.post(f"/g/{VIDEOS_SLUG}/entrar", data={"email": "x@y.com", "clave": "gua"})
        html = c.get(f"/g/{VIDEOS_SLUG}").get_data(as_text=True)
        assert V + ".jpg" in html and "24 s" in html, html[-800:]
        assert c.get(V + ".jpg").data == b"\xff\xd8foto"
        assert llamadas[-1][2].get("bbox") == 0, "la miniatura trae los recuadros"

        # los dos clips pegados son UNA visita, con los dos animales y su
        # duracion total; el de las 16 va aparte
        visitas = re.findall(r'data-clips="([^"]+)"', html)
        assert visitas == [SUELTO, f"{EV},{EV2}"], visitas
        assert "Gato y Perro" in html and "50 s" in html and "2 clips" in html
        # y la del horario del dispensador sale marcada como comida
        assert html.count("Comida 13:30") == 1, "no marco la comida"
        assert "Comida 16" not in html
        solo = c.get(f"/g/{VIDEOS_SLUG}?que=comidas").get_data(as_text=True)
        assert re.findall(r'data-clips="([^"]+)"', solo) == [f"{EV},{EV2}"]

        # los filtros llegan a Frigate; basura en la URL no llega
        c.get(f"/g/{VIDEOS_SLUG}?que=guardados")
        assert llamadas[-1][2].get("favorites") == 1
        c.get(f"/g/{VIDEOS_SLUG}?que=cat&dia=2026-09-14")
        q = llamadas[-1][2]
        assert q["labels"] == "cat" and q["before"] - q["after"] == 86400, q
        c.get(f"/g/{VIDEOS_SLUG}?que=<x>&dia=ayer&antes=1;drop")
        q = llamadas[-1][2]
        assert q["labels"] == "dog,cat" and "after" not in q and "before" not in q, q

        # un id que no es de Frigate no viaja a Frigate
        n = len(llamadas)
        for malo in ("..%2F..%2Fapi%2Fconfig", "abc", "1789494267.5-x/../y"):
            assert c.get(f"/g/{VIDEOS_SLUG}/v/{malo}.mp4").status_code == 404, malo
        assert len(llamadas) == n, "un id invalido llego a Frigate"

        # el clip admite Range (sin eso el iPhone no lo reproduce) y se cachea
        r = c.get(V + ".mp4", headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and r.data == bytes(range(10, 20)), r.status_code
        r.close()
        n = sum(1 for q in llamadas if q[1].endswith("/clip.mp4"))
        c.get(V + ".mp4").close()
        assert sum(1 for q in llamadas if q[1].endswith("/clip.mp4")) == n, "no cacheo"

        # el corazon: solo con JSON, y dice POST para guardar y DELETE para soltar
        assert c.post(V + "/guardar", data={"on": "1"}).status_code == 400
        assert c.post(V + "/guardar", json={"on": True}).get_json() == {"guardado": True}
        assert llamadas[-1][:2] == ("POST", f"/api/events/{EV}/retain")
        c.post(V + "/guardar", json={"on": False})
        assert llamadas[-1][:2] == ("DELETE", f"/api/events/{EV}/retain")

        # un evento de otra camara no se ve aunque el id sea valido
        estado["camara"] = "puerta"
        assert c.post(V + "/guardar", json={"on": True}).status_code == 404
        estado["camara"] = FRIGATE_CAMARA

        # Frigate caido: la pagina avisa en vez de romperse
        estado["caido"] = True
        r = c.get(f"/g/{VIDEOS_SLUG}")
        assert r.status_code == 200 and "no\nresponde" in r.get_data(as_text=True)
        estado["caido"] = False

        # y el mismo id por una galeria de fotos no es un video
        c.post("/g/ccc/entrar", data={"email": "x@y.com", "clave": "secreta"})
        assert c.get(f"/g/ccc/v/{EV}.mp4").status_code == 404

    # el cron: prepara lo nuevo y borra lo viejo, para que abrir un video sea
    # servir un archivo que ya esta en el disco
    for p in (CACHE / "videos").glob("*.mp4"):
        p.unlink()
    viejo = CACHE / "videos" / "viejo.mp4"
    viejo.write_bytes(b"x")
    os.utime(viejo, (0, 0))
    cachear_videos()
    assert (CACHE / "videos" / f"{EV}.mp4").exists(), "el cron no preparo el clip"
    assert not viejo.exists(), "el cron no borro el clip viejo"

    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "servir"
    if cmd == "test":
        test()
    elif cmd == "publicar":
        if len(sys.argv) != 4:
            sys.exit('uso: galeria.py publicar "Nombre" lista.txt')
        publicar(sys.argv[2], sys.argv[3])
    elif cmd == "partidos":
        if len(sys.argv) != 4:
            sys.exit("uso: galeria.py partidos <slug> <partidos.csv>")
        partidos(sys.argv[2], sys.argv[3])
    elif cmd == "fechas":
        if len(sys.argv) != 3:
            sys.exit("uso: galeria.py fechas <slug>")
        fechas(sys.argv[2])
    elif cmd == "calentar":
        if len(sys.argv) != 3:
            sys.exit("uso: galeria.py calentar <slug>")
        calentar(sys.argv[2])
    elif cmd == "videos":
        cachear_videos(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif cmd == "csp":
        csp_resumen()
    elif cmd == "reparar_raw":
        reparar_raw(sys.argv[2] if len(sys.argv) > 2 else None,
                    sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "servir":
        CACHE.mkdir(parents=True, exist_ok=True)
        # ponytail: servidor de desarrollo de Flask (threaded), detras de Caddy.
        # Aguanta un puñado de clientes simultaneos, que es el caso real. Si
        # alguna vez hay decenas a la vez, pasar a gunicorn -w 4.
        app.run(host="127.0.0.1", port=8000)
    else:
        sys.exit(__doc__)
