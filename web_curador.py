r"""
Curador web: revisar y corregir a mano lo que la IA agrupó.

Hace lo que el pipeline no puede saber solo:
  - renombrar eventos, corregir fechas, escribir notas
  - crear eventos de verdad y meterles fotos rápido (selección múltiple)
  - marcar viajes y colecciones (VDS, trabajo, etc.) — lo que el GPS no da
  - rescatar fotos mal clasificadas de _Telefono / _Sueltas

    python web_curador.py                 # http://127.0.0.1:5000
    python web_curador.py --port 8080

No mueve ni renombra archivos: solo escribe en la BD. El árbol de carpetas se
regenera después con la Fase 4 (4_exportar.py).
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from flask import (Flask, abort, after_this_request, g, jsonify, redirect,
                   render_template, request, send_file, url_for)
from werkzeug.utils import secure_filename

sys.path.insert(0, str(Path(__file__).parent))
from glosario_etiquetas import explicar        # noqa: E402
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Todo lo que cambia entre la notebook y la Pi sale del entorno. Los valores
# por defecto son los de la notebook, asi que aca no cambia nada: si no seteas
# ninguna variable, el curador se comporta exactamente como siempre.
_RAIZ = Path(os.environ.get("CURADOR_RAIZ", r"C:\Renzo\Proyectos\IMGorgni"))

DB = os.environ.get("CURADOR_DB", str(_RAIZ / "photos_v2.db"))
# En C: (SSD NVMe interno): E: es un disco externo por USB, y SQLite con WAL
# hace muchas lecturas/escrituras chicas que sufren esa latencia. Adentro de
# la carpeta del proyecto, no en la raíz: Windows no deja crear archivos
# nuevos directo en C:\ para procesos no elevados (hace falta para el
# -wal/-journal de SQLite).
THUMBS = Path(os.environ.get("CURADOR_THUMBS", _RAIZ / "_thumbs_curador"))
FACE_THUMBS = Path(os.environ.get("CURADOR_FACE_THUMBS", _RAIZ / "_face_thumbs_curador"))
VISTAS_DIR = Path(os.environ.get("CURADOR_VISTAS", _RAIZ / "_vistas_curador"))
THUMB_PX = 320
VISTA_PX = 1600      # lo que sirve el visor: se ve bien en pantalla y pesa ~300 KB
POR_PAGINA = 180

app = Flask(__name__)
# Cloudflare corta los pedidos de mas de 100 MB, asi que el limite propio va
# mas abajo: mejor un error claro nuestro que un 413 del proxy.
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024
_lock = threading.Lock()


def _db_write(c, fn, max_retries=5, delay=0.5):
    """Ejecuta *fn(c)* y hace commit, reintentando si la BD está bloqueada
    por otro proceso (ej. analizar_caras.py corriendo en paralelo)."""
    for attempt in range(max_retries):
        try:
            fn(c)
            c.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(delay)
            else:
                raise


# --------------------------------------------------------------------------
# Acceso desde afuera de la notebook
# --------------------------------------------------------------------------
# El curador no tiene usuarios: muestra el archivo entero a quien lo abra. Eso
# esta bien en 127.0.0.1, donde solo llega esta maquina. Si se abre a la red
# —para verlo del telefono— cualquier dispositivo del wifi lo tendria. El token
# es lo minimo que cierra eso: va una vez en la URL y queda en una cookie.
# En loopback TOKEN queda en None y no molesta.
TOKEN = None
LIBRES = {"/salud"}


def _mis_ips():
    """Las IPv4 reales de esta maquina, para imprimir un link que sirva.
    Se saltean las 169.254.x (link-local: interfaz sin red de verdad)."""
    import socket
    vistas = []
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = info[4][0]
        if not ip.startswith(("127.", "169.254.")) and ip not in vistas:
            vistas.append(ip)
    return vistas or ["127.0.0.1"]


@app.before_request
def _exigir_token():
    if TOKEN is None or request.path in LIBRES:
        return None
    if request.cookies.get("curador") == TOKEN:
        return None
    if request.args.get("t") == TOKEN:
        # Se limpia el token de la URL para que no quede en el historial ni se
        # comparta al copiar un link.
        limpio = {k: v for k, v in request.args.items() if k != "t"}
        r = redirect(url_for(request.endpoint, **{**(request.view_args or {}), **limpio})
                     if request.endpoint else "/")
        r.set_cookie("curador", TOKEN, max_age=60 * 60 * 24 * 30,
                     samesite="Lax", httponly=True)
        return r
    return ("Falta el token de acceso.", 403)


@app.route("/salud")
def salud():
    return jsonify(ok=True)


@app.context_processor
def _barra_lateral():
    """Datos de la barra de la derecha, en todas las pantallas.

    Va por context_processor y no como argumento de cada ruta para no tener que
    acordarse de pasarlo en las nueve pantallas. Solo los eventos de primer
    nivel: los sub-eventos se llegan desde su grupo, y meter los 431 en la lista
    la volveria inservible. Sin conteos a proposito — contar cada tema es un
    escaneo de photo_features, y no vale pagarlo en cada carga de pagina.
    """
    if request.endpoint in (None, "static", "thumb", "vista", "salud", "api_fotos"):
        return {}
    try:
        eventos = con().execute(
            """SELECT id, COALESCE(NULLIF(TRIM(name),''), suggested_name) t,
                      SUBSTR(start_time,1,10) d,
                      (SELECT COUNT(*) FROM events h WHERE h.parent_id = e.id) hijos
               FROM events e WHERE e.parent_id IS NULL
               ORDER BY e.start_time DESC""").fetchall()
    except sqlite3.Error:
        eventos = []
    return {"barra_eventos": eventos,
            "barra_temas": [n for n, _t, _o in temas_guardados(incluir_ocultos=False)]}


# --------------------------------------------------------------------------
# BD
# --------------------------------------------------------------------------

def con():
    if "db" not in g:
        g.db = sqlite3.connect(DB, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def _cerrar(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def asegurar_tag_foto(c, forzar=False):
    """`tag_foto` es scene_tags desnormalizado: una fila por (etiqueta, foto),
    con indice.

    Sin esto, cada vista por etiqueta barre photo_features entero con
    LIKE '%"tag"%'. Medido el 21/8/2026 en la Pi: 65,6 s contra 0,148 s con la
    tabla, mismo resultado (10.448 fotos). Los conteos de /temas pasan de seis
    barridos completos —que no terminaban— a 0,93 s.

    Se reconstruye sola cuando cambia la cantidad de filas de photo_features,
    que es lo que pasa cuando el analisis indexa fotos nuevas.

    OJO con el limite de esa señal: si el analisis vuelve a correr sobre fotos
    que YA estaban y solo reescribe sus scene_tags, la cantidad de filas no
    cambia y esta tabla queda vieja sin avisar. Despues de un reanalisis de lo
    ya indexado, forzala:

        python web_curador.py --rehacer-tags
    """
    c.execute("CREATE TABLE IF NOT EXISTS tag_foto_meta (filas INTEGER)")
    filas = c.execute("SELECT COUNT(*) FROM photo_features").fetchone()[0]
    guardado = c.execute("SELECT filas FROM tag_foto_meta").fetchone()
    hay = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='tag_foto'").fetchone()
    if hay and guardado and guardado[0] == filas and not forzar:
        return

    t0 = time.perf_counter()
    c.execute("DROP TABLE IF EXISTS tag_foto")
    c.execute("CREATE TABLE tag_foto (tag TEXT NOT NULL, photo_id INTEGER NOT NULL)")
    pares = []
    for pid, s in c.execute("SELECT photo_id, scene_tags FROM photo_features "
                            "WHERE scene_tags IS NOT NULL"):
        try:
            for et in json.loads(s):
                if isinstance(et, str):
                    pares.append((et, pid))
        except (json.JSONDecodeError, TypeError):
            pass
    c.executemany("INSERT INTO tag_foto VALUES (?,?)", pares)
    # el indice cubre las dos columnas: el EXISTS no llega a tocar la tabla
    c.execute("CREATE INDEX ix_tag_foto ON tag_foto(tag, photo_id)")
    c.execute("DELETE FROM tag_foto_meta")
    c.execute("INSERT INTO tag_foto_meta VALUES (?)", (filas,))
    c.commit()
    print(f"  + tag_foto: {len(pares)} pares en {time.perf_counter() - t0:.0f}s")


def migrar():
    """Columnas que el curador necesita y el pipeline no creó."""
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
    # parent_id: un evento puede colgar de otro (fecha → semestre → liga).
    # Lista de adyacencia: las fotos viven siempre en la hoja, el padre suma.
    for col, tipo in (("notas", "TEXT"), ("confirmado", "INTEGER DEFAULT 0"),
                      ("parent_id", "INTEGER REFERENCES events(id)")):
        if col not in cols:
            c.execute(f"ALTER TABLE events ADD COLUMN {col} {tipo}")
            print(f"  + events.{col}")
    c.execute("CREATE INDEX IF NOT EXISTS ix_events_parent ON events(parent_id)")
    # Para aparear un JPEG con su RAW cuando estan en arboles distintos
    # (ver _ruta_original). Sin esto es un scan de toda la tabla por foto, y
    # un ZIP de 100 seleccionadas hacia 100 scans.
    c.execute("CREATE INDEX IF NOT EXISTS ix_files_captura ON files(capture_date)")
    crear_tabla_temas(c)
    # Basurero: la foto sigue en la BD y en el disco, pero desaparece de todas
    # las vistas. Solo se borra de verdad entrando al basurero.
    cf = {r[1] for r in c.execute("PRAGMA table_info(files)")}
    if "borrado" not in cf:
        c.execute("ALTER TABLE files ADD COLUMN borrado INTEGER DEFAULT 0")
        print("  + files.borrado")
    # 'telefono' / 'camara' / 'desconocido', calculado una sola vez.
    if "dispositivo" not in cf:
        c.execute("ALTER TABLE files ADD COLUMN dispositivo TEXT")
        print("  + files.dispositivo")
    c.execute("CREATE INDEX IF NOT EXISTS ix_files_borrado ON files(borrado)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_files_disp ON files(dispositivo)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ep_event ON event_photos(event_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ep_photo ON event_photos(photo_id)")
    # Decisiones sobre duplicados. La Fase 5 propone; acá queda lo que vos resolvés.
    # score y ambos_corruptos los usan /duplicados y /duplicados/corruptos: sin
    # ellos en el CREATE, una base nueva revienta con "no such column: score".
    c.execute(f"""CREATE TABLE IF NOT EXISTS duplicados (
                   id INTEGER PRIMARY KEY,
                   tipo TEXT, razon TEXT, mb REAL,
                   ruta_conservar TEXT, ruta_descartar TEXT,
                   id_conservar INTEGER, id_descartar INTEGER,
                   decision TEXT DEFAULT 'pendiente',
                   score INTEGER DEFAULT {SIN_SCORE},
                   ambos_corruptos INTEGER DEFAULT 0)""")

    # Si/no humano por foto y etiqueta. Manda sobre lo que dijo el modelo y
    # queda como datos para ajustar despues el umbral de cada etiqueta.
    c.execute("""CREATE TABLE IF NOT EXISTS etiqueta_veredicto (
                   photo_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                   etiqueta TEXT NOT NULL,
                   ok INTEGER NOT NULL,
                   ts TEXT NOT NULL,
                   PRIMARY KEY (photo_id, etiqueta))""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_veredicto_etq ON etiqueta_veredicto(etiqueta)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_dup_dec ON duplicados(decision, tipo)")

    for col in ("bytes INTEGER", "ancho INTEGER", "alto INTEGER", "software TEXT"):
        if col.split()[0] not in cf:
            c.execute(f"ALTER TABLE files ADD COLUMN {col}")
            print(f"  + files.{col.split()[0]}")

    pend = c.execute("SELECT id, file_path, camera_model FROM files "
                     "WHERE dispositivo IS NULL").fetchall()
    if pend:
        from utils_analisis import clasificar_dispositivo
        c.executemany("UPDATE files SET dispositivo=? WHERE id=?",
                      [(clasificar_dispositivo(cm, fp), i) for i, fp, cm in pend])
        print(f"  clasificadas {len(pend):,} fotos por dispositivo")

    # Sin este índice el UPDATE de abajo hace un scan por cada una de las 121k filas.
    c.execute("CREATE INDEX IF NOT EXISTS ix_pf_photo ON photo_features(photo_id)")
    # Las dimensiones ya las midió la Fase 2: salen gratis de quality_json.
    n = c.execute("""UPDATE files SET
                       ancho = (SELECT json_extract(p.quality_json,'$.w') FROM photo_features p
                                WHERE p.photo_id = files.id),
                       alto  = (SELECT json_extract(p.quality_json,'$.h') FROM photo_features p
                                WHERE p.photo_id = files.id)
                     WHERE ancho IS NULL
                       AND EXISTS (SELECT 1 FROM photo_features p WHERE p.photo_id=files.id)"""
                   ).rowcount
    if n > 0:
        print(f"  dimensiones tomadas del análisis: {n:,}")
    c.commit()
    n = recalcular_scores(c)
    if n:
        print(f"  score calculado para {n:,} pares que estaban sin verificar")
    limpiar_cuarentena(c)
    # Un evento que colgaba de otro que se borró queda invisible: la portada
    # solo muestra los que no tienen padre. Se sueltan para que reaparezcan.
    h = c.execute("""UPDATE events SET parent_id=NULL WHERE parent_id IS NOT NULL
                     AND parent_id NOT IN (SELECT id FROM events)""").rowcount
    if h:
        c.commit()
        print(f"  {h:,} eventos huérfanos sueltos (su padre ya no existe)")
    # Ultimo porque es lo mas caro: relee scene_tags entero. Solo corre cuando
    # cambio la cantidad de filas de photo_features, o sea despues del analisis.
    asegurar_tag_foto(c)
    c.close()


# Un archivo puede ser a la vez el descarte de un par ya movido y el lado "se
# conserva" de otro que sigue pendiente (pasa cuando una corrida vieja mandó la
# copia buena a cuarentena). Mandarlo al basurero esconde ese par pendiente y te
# deja sin poder decidirlo: mientras haya una decisión abierta encima, se queda.
SQL_NO_ES_CONSERVAR_PENDIENTE = """id NOT IN (
    SELECT id_conservar FROM duplicados WHERE decision='pendiente'
      AND id_conservar IS NOT NULL)"""


def limpiar_cuarentena(c):
    """Lo que está en la carpeta de cuarentena ya no es parte del archivo.

    Seguía contando adentro de los eventos (7.718 fotos al 2026-08-02) y
    apareciendo en las vistas de fotos sueltas, así que los conteos de cada
    evento y la cola de "sin evento" estaban inflados con descartes ya movidos.
    """
    pref = str(CUARENTENA).lower()
    n_ev = c.execute("""DELETE FROM event_photos WHERE photo_id IN
                          (SELECT id FROM files
                           WHERE LOWER(SUBSTR(file_path,1,?)) = ?)""",
                     (len(pref), pref)).rowcount
    # borrado=1 SOLO para los que están ahí por ser el descarte de un par
    # resuelto. Los otros llegaron al revés — una corrida vieja mandó la copia
    # BUENA a cuarentena — y tienen que seguir visibles para poder rescatarlos.
    n_b = c.execute(f"""UPDATE files SET borrado=1
                        WHERE COALESCE(borrado,0)=0
                          AND LOWER(SUBSTR(file_path,1,?)) = ?
                          AND id IN (SELECT id_descartar FROM duplicados
                                     WHERE decision IN ('movido','descartar'))
                          AND {SQL_NO_ES_CONSERVAR_PENDIENTE}""",
                    (len(pref), pref)).rowcount
    if n_ev or n_b:
        c.commit()
        print(f"  cuarentena: {n_ev:,} fotos sacadas de eventos, "
              f"{n_b:,} mandadas al basurero")


MAX_MPX = 300 * 10 ** 6      # ni la panorámica más bestia llega acá


def _plausible(w, h, bytes_):
    """Descarta medidas imposibles. Algunos DNG dañados del rescate de F:
    declaran 49435x40988 (2 Gpx) en 23 MB: rawpy los lee así, pero es basura
    -- eso ya lo frena el tope de MAX_MPX solo (2 Gpx >> 300 Mpx).

    El piso de bytes/pixel es un segundo guardarraíl para resoluciones que
    sí entran bajo MAX_MPX pero igual son un invento. "0.02" era un numero
    tirado sin medir: sobre 3.892 archivos marcados no-medibles, 2.800
    abrían y decodificaban perfecto -- fotos JPEG de 24 Mpx bien comprimidas,
    hasta 0.0021 bytes/pixel. El caso de rescate de arriba da ~0.0000119.
    0.0005 separa los dos con margen para ambos lados.
    """
    if not w or not h or w <= 0 or h <= 0 or w * h > MAX_MPX:
        return False
    return not (bytes_ and bytes_ > 0 and bytes_ / (w * h) < 0.0005)


def _medidas(fp, bytes_=None):
    """(ancho, alto) leyendo solo el encabezado. (None, None) si no se puede
    o si el valor leído no es creíble."""
    from PIL import Image
    try:
        if Path(fp).suffix.lower() in {".cr2", ".arw", ".nef", ".rw2", ".raf",
                                       ".raw", ".dng"}:
            import rawpy
            with rawpy.imread(fp) as raw:
                w, h = int(raw.sizes.width), int(raw.sizes.height)
        else:
            with Image.open(fp) as im:     # no decodifica los píxeles
                w, h = im.size
    except Exception:
        return None, None
    return (w, h) if _plausible(w, h, bytes_) else (None, None)


def rellenar_metadatos():
    """Completa peso y dimensiones de lo que falte. En segundo plano: son
    ~120k lecturas de disco y la app tiene que estar usable ya."""
    c = sqlite3.connect(DB, timeout=180)

    pend = c.execute("SELECT id, file_path FROM files WHERE bytes IS NULL").fetchall()
    if pend:
        print(f"  midiendo el peso de {len(pend):,} archivos…")
        lote = []
        for i, fp in pend:
            try:
                lote.append((os.path.getsize(fp), i))
            except OSError:
                lote.append((-1, i))      # -1 = no está (F: desconectada)
            if len(lote) >= 2000:
                c.executemany("UPDATE files SET bytes=? WHERE id=?", lote)
                c.commit(); lote = []
        if lote:
            c.executemany("UPDATE files SET bytes=? WHERE id=?", lote)
            c.commit()
        print(f"  peso listo ({len(pend):,})")

    # Las que nunca pasaron por la Fase 2 no tienen dimensiones: se leen del
    # encabezado del archivo, sin decodificar la imagen.
    # Medidas imposibles ya guardadas (DNG dañados): a -1, que es "no se sabe".
    # Mismo umbral que _plausible(): ver ahi por que es 0.0005 y no 0.02.
    malas = c.execute(f"""UPDATE files SET ancho=-1, alto=-1
                          WHERE ancho > 0 AND
                                (ancho*alto > {MAX_MPX}
                                 OR (bytes > 0 AND CAST(bytes AS REAL)/(ancho*alto) < 0.0005))
                       """).rowcount
    if malas:
        c.commit()
        print(f"  descartadas {malas:,} dimensiones imposibles (archivos dañados)")

    pend = c.execute("""SELECT id, file_path, bytes FROM files
                        WHERE ancho IS NULL AND COALESCE(bytes,0) > 0""").fetchall()
    if not pend:
        c.close()
        return
    print(f"  leyendo dimensiones de {len(pend):,} archivos…")
    lote, ok = [], 0
    for i, fp, b in pend:
        w, h = _medidas(fp, b)
        lote.append((w or -1, h or -1, i))    # -1 = ilegible, no reintentar
        ok += 1 if w else 0
        if len(lote) >= 500:
            c.executemany("UPDATE files SET ancho=?, alto=? WHERE id=?", lote)
            c.commit(); lote = []
    if lote:
        c.executemany("UPDATE files SET ancho=?, alto=? WHERE id=?", lote)
        c.commit()
    print(f"  dimensiones listas: {ok:,} de {len(pend):,}")
    c.close()


def descendientes(c, eid):
    """ids de *eid* y de todo lo que cuelga de el, a cualquier profundidad.

    Sirve para no dejar que un evento se cuelgue de su propio hijo: SQL_ARBOL
    es un CTE recursivo y un ciclo lo haria girar sin fin.
    """
    return {r[0] for r in c.execute("""
        WITH RECURSIVE baja(id) AS (
            SELECT ?
            UNION
            SELECT e.id FROM events e JOIN baja b ON e.parent_id = b.id)
        SELECT id FROM baja""", (eid,))}


def nombre_evento(row):
    return (row["name"] or "").strip() or (row["suggested_name"] or "").strip() or f"Evento {row['id']}"


# --------------------------------------------------------------------------
# Miniaturas
# --------------------------------------------------------------------------

def _leer_imagen(fp: str, px: int = THUMB_PX):
    """Devuelve un PIL.Image reducido a ~px, o None. Los RAW van por su preview.

    `px` manda cuanto se decodifica: para la miniatura alcanza 320, pero el
    visor pide 1600 y con el draft de la miniatura salia una imagen borrosa
    estirada.
    """
    from PIL import Image, ImageFile
    # Buena parte del archivo viene del rescate de F: y esta truncado (~15% de
    # los JPEG). Sin esto PIL levanta OSError al decodificar y la miniatura
    # quedaba en error 500: preferimos mostrar el pedazo que si se puede leer,
    # que es justo lo que hace falta para decidir si la foto sirve o no.
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    from utils_images import enderezar
    ext = Path(fp).suffix.lower()
    if ext in {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}:
        try:
            import rawpy
            with rawpy.imread(fp) as raw:
                flip = raw.sizes.flip
                thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return enderezar(Image.open(io.BytesIO(thumb.data)), flip)
            return enderezar(Image.fromarray(thumb.data), flip)
        except Exception:
            return None
    try:
        im = Image.open(fp)
        # draft() decodifica a menor resolución directo del JPEG: mucho más rápido
        im.draft("RGB", (px * 2, px * 2))
        return enderezar(im)
    except Exception:
        return None


def _placeholder(texto):
    """Cartel en vez de ícono roto: el archivo no está o no se pudo leer."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{THUMB_PX}" height="{THUMB_PX}">'
           f'<rect width="100%" height="100%" fill="#3a3742"/>'
           f'<text x="50%" y="50%" fill="#9d97a6" font-family="sans-serif" '
           f'font-size="17" text-anchor="middle">{texto}</text></svg>')
    return app.response_class(svg, mimetype="image/svg+xml")


@app.route("/thumb/<int:pid>")
def thumb(pid):
    dest = THUMBS / f"{pid}.jpg"
    if not dest.exists():
        row = con().execute("SELECT file_path FROM files WHERE id=?", (pid,)).fetchone()
        if not row:
            return _placeholder("no está en la base")
        if not os.path.exists(row["file_path"]):
            return _placeholder("archivo no encontrado")
        im = _leer_imagen(row["file_path"])
        if im is None:
            return _placeholder("no se pudo leer")
        # El decodificado real pasa recien en convert(): Image.open() solo mira
        # el encabezado. Si el archivo esta roto, revienta aca y antes eso salia
        # como HTTP 500 (el navegador mostraba el icono de imagen rota, sin decir
        # por que). Un cartel es mas util: se entiende que el archivo esta dañado.
        try:
            with _lock:
                dest.parent.mkdir(parents=True, exist_ok=True)
                im = im.convert("RGB")
                im.thumbnail((THUMB_PX, THUMB_PX))
                im.save(dest, "JPEG", quality=82)
        except Exception:
            dest.unlink(missing_ok=True)   # no dejar una miniatura a medio escribir
            return _placeholder("archivo dañado")
    return send_file(dest, mimetype="image/jpeg", max_age=86400)


@app.route("/vista/<int:pid>")
def vista(pid):
    """Version de ~1600px para el visor, cacheada igual que la miniatura.

    La miniatura (320px) no alcanza para decidir si una foto sirve, y el
    original pesa 14-25 MB: por red eso son 50 segundos por foto (medido con el
    disco en la Pi). 1600px son ~300 KB y se genera una sola vez.
    """
    dest = VISTAS_DIR / f"{pid}.jpg"
    if not dest.exists():
        row = con().execute("SELECT file_path FROM files WHERE id=?", (pid,)).fetchone()
        if not row:
            return _placeholder("no está en la base")
        if not os.path.exists(row["file_path"]):
            return _placeholder("archivo no encontrado")
        im = _leer_imagen(row["file_path"], VISTA_PX)
        if im is None:
            return _placeholder("no se pudo leer")
        try:
            with _lock:
                dest.parent.mkdir(parents=True, exist_ok=True)
                im = im.convert("RGB")
                im.thumbnail((VISTA_PX, VISTA_PX))
                im.save(dest, "JPEG", quality=86)
        except Exception:
            dest.unlink(missing_ok=True)
            return _placeholder("archivo dañado")
    return send_file(dest, mimetype="image/jpeg", max_age=86400)


@app.route("/full/<int:pid>")
def full(pid):
    row = con().execute("SELECT file_path FROM files WHERE id=?", (pid,)).fetchone()
    if not row or not os.path.exists(row["file_path"]):
        abort(404)
    ext = Path(row["file_path"]).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return send_file(row["file_path"])
    im = _leer_imagen(row["file_path"])       # RAW: se sirve el preview
    if im is None:
        return _placeholder("no se pudo leer")
    buf = io.BytesIO()
    try:
        im.convert("RGB").save(buf, "JPEG", quality=90)
    except Exception:
        return _placeholder("archivo dañado")
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")


# --------------------------------------------------------------------------
# Listado de eventos
# --------------------------------------------------------------------------

# Suma de cada evento incluyendo TODA su descendencia (fechas de un semestre,
# semestres de una liga). Sirve también para sacarle miniatura a un evento
# padre, que no tiene fotos propias.
SQL_ARBOL = """
WITH RECURSIVE arbol(raiz, id) AS (
    SELECT id, id FROM events
    UNION ALL
    SELECT a.raiz, e.id FROM events e JOIN arbol a ON e.parent_id = a.id
)
SELECT raiz, COUNT(DISTINCT ep.photo_id) n, MIN(ep.photo_id) pid
FROM arbol LEFT JOIN event_photos ep ON ep.event_id = arbol.id
GROUP BY raiz"""


@app.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    filtro = request.args.get("f") or "todos"
    # Corte del filtro "grandes". Era fijo en 800; ahora se elige desde la
    # barra, porque el umbral util cambia segun lo que estes buscando: 800
    # para las jornadas de la liga, 20 para separar el ruido de lo real.
    minimo = request.args.get("n", type=int)
    if minimo is None or minimo < 0:
        minimo = 800
    sql = """SELECT e.*, COUNT(ep.photo_id) n,
                    MIN(f.file_path) muestra, MIN(ep.photo_id) pid
             FROM events e
             LEFT JOIN event_photos ep ON ep.event_id = e.id
             LEFT JOIN files f ON f.id = ep.photo_id
             GROUP BY e.id"""
    filas = [dict(r) for r in con().execute(sql)]
    arbol = {r["raiz"]: r for r in con().execute(SQL_ARBOL)}
    hijos = {r["parent_id"]: r["k"] for r in con().execute(
        """SELECT parent_id, COUNT(*) k FROM events
           WHERE parent_id IS NOT NULL GROUP BY parent_id""")}
    # Los hijos de cada grupo, para poder desplegarlos en la propia tarjeta sin
    # tener que entrar. Antes solo se sabia cuantos eran, y el arbol era invisible.
    subs = defaultdict(list)
    for r in con().execute(
            """SELECT e.id, e.parent_id, e.name, e.suggested_name, e.start_time,
                      e.confirmado,
                      (SELECT COUNT(*) FROM event_photos ep
                       WHERE ep.event_id = e.id) n
               FROM events e WHERE e.parent_id IS NOT NULL
               ORDER BY e.start_time"""):
        d = dict(r)
        d["titulo"] = nombre_evento(d)
        subs[r["parent_id"]].append(d)
    for r in filas:
        r["titulo"] = nombre_evento(r)
        sub = arbol.get(r["id"])
        r["n_total"] = sub["n"] if sub else r["n"]
        r["hijos"] = hijos.get(r["id"], 0)
        if not r["pid"] and sub:          # el padre hereda miniatura de un hijo
            r["pid"] = sub["pid"]
    if q:
        # Buscando se ve todo, también los sub-eventos: es la forma de llegar
        # a una fecha suelta sin pasar por el padre.
        ql = q.lower()
        filas = [r for r in filas if ql in r["titulo"].lower()
                 or ql in (r["notas"] or "").lower()
                 or ql in (r["coleccion"] or "").lower()]
    else:
        filas = [r for r in filas if not r["parent_id"]]
    if filtro == "sin_revisar":
        filas = [r for r in filas if not r["confirmado"]]
    elif filtro == "revisados":
        # Los confirmados son los unicos que organizar_por_evento.py mueve a
        # carpetas: este filtro es para revisarlos antes de correr eso.
        filas = [r for r in filas if r["confirmado"]]
    elif filtro == "grandes":
        filas = [r for r in filas if r["n_total"] >= minimo]
    elif filtro == "colecciones":
        filas = [r for r in filas if (r["coleccion"] or "").strip()]
    filas.sort(key=lambda r: (r["start_time"] or ""), reverse=True)

    tot = con().execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    en_ev = con().execute("SELECT COUNT(DISTINCT photo_id) c FROM event_photos").fetchone()["c"]
    revisados = sum(1 for r in filas if r["confirmado"])
    return render_template("index.html", eventos=filas, q=q, filtro=filtro,
                           minimo=minimo, fotos_listadas=sum(r["n_total"] for r in filas),
                           total_fotos=tot, en_eventos=en_ev, revisados=revisados,
                           subs=subs, otros=lista_eventos())


# --------------------------------------------------------------------------
# Detalle de un evento
# --------------------------------------------------------------------------

@app.route("/evento/<int:eid>")
def evento(eid):
    ev = con().execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not ev:
        abort(404)
    otros = con().execute(
        """SELECT e.id, e.name, e.suggested_name, e.start_time
           FROM events e WHERE e.id <> ? ORDER BY e.start_time DESC""", (eid,)).fetchall()
    padre = con().execute(
        """SELECT id, name, suggested_name FROM events
           WHERE id = (SELECT parent_id FROM events WHERE id=?)""", (eid,)).fetchone()
    hijos = [dict(r) for r in con().execute(
        """SELECT e.id, e.name, e.suggested_name, e.start_time, e.end_time,
                  e.confirmado,
                  (SELECT COUNT(*) FROM event_photos WHERE event_id=e.id) n,
                  (SELECT COUNT(*) FROM events h WHERE h.parent_id=e.id) nietos
           FROM events e WHERE e.parent_id=? ORDER BY e.start_time""", (eid,))]
    if hijos:
        # miniatura y total de cada hijo contando SU descendencia, igual que en
        # la portada: un hijo que es a su vez grupo no tiene fotos propias
        arbol = {r["raiz"]: r for r in con().execute(SQL_ARBOL)}
        for h in hijos:
            sub = arbol.get(h["id"])
            h["titulo"] = nombre_evento(h)
            h["n_total"] = sub["n"] if sub else h["n"]
            h["pid"] = sub["pid"] if sub else None

    # Candidatos a padre: cualquiera menos el propio evento y su descendencia
    # (colgarse de un hijo propio seria un ciclo).
    baja = descendientes(con(), eid)
    posibles_padres = [o for o in otros if o["id"] not in baja]
    a = _args()
    # `solo=1` deja solo las fotos puestas directamente en este evento. Por
    # defecto se ve toda la rama, que es lo que casi siempre queres.
    solo = request.args.get("solo") == "1"
    vista = "evento_solo" if solo else "evento"
    return render_template("evento.html", ev=ev, titulo=nombre_evento(ev),
                           vista=vista, solo=solo, **a,
                           fotos=consultar_fotos(vista, eid, **a),
                           n=contar_fotos(vista, eid, q=a["q"], formato=a["formato"]),
                           total=contar_fotos(vista, eid), otros=otros,
                           padre=padre, hijos=hijos,
                           posibles_padres=posibles_padres,
                           error=request.args.get("error"))


# --------------------------------------------------------------------------
# Autocull sobre un evento
# --------------------------------------------------------------------------

# El codigo de Autocull vive en este proyecto (autocull/); el venv con sus dependencias
# pesadas sigue en la carpeta vieja, porque un venv no se puede mover.
AUTOCULL_DIR = Path(os.environ.get("CURADOR_AUTOCULL", Path(__file__).resolve().parent))
AUTOCULL_PY = Path(os.environ.get("CURADOR_AUTOCULL_PY",
                                  r"C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"))
AUTOCULL_SALIDA = Path(os.environ.get("CURADOR_AUTOCULL_SALIDA",
                                      _RAIZ / "_autocull_curador"))
# Segundos sin que el runner toque su JSON para darlo por muerto. Escribe dos
# veces por segundo, asi que 120 es de sobra incluso si el disco se traba.
LOCK_MUERTO = 120
# Las estrellas se reparten por posicion DENTRO de las que pasaron, no por el
# puntaje absoluto: final_score es un percentil de toda la sesion, y como las
# rechazadas se llevan la cola baja, las que pasan se amontonan arriba. Con
# cortes absolutos (>=90 = 5) sobre F2 daba 2 fotos de 5 estrellas y 308 de 3:
# un balde inservible y otro demasiado grande. Por posicion el embudo sirve.
REPARTO = [(0.05, 5), (0.20, 4), (0.50, 3), (0.80, 2), (1.01, 1)]


def _repartir_estrellas(fotos):
    """Pone 'estrellas' en cada foto. Las rechazadas quedan en 0."""
    pasan = sorted((f for f in fotos if f.get("decision") == "PASS"),
                   key=lambda f: -(f.get("puntaje") or 0))
    n = len(pasan)
    for i, f in enumerate(pasan):
        pos = (i + 1) / n if n else 1
        f["estrellas"] = next(e for corte, e in REPARTO if pos <= corte)
    for f in fotos:
        f.setdefault("estrellas", 0)
    return fotos


def _autocull_json(eid):
    return AUTOCULL_SALIDA / f"{eid}.json"


def _fotos_con_estrellas(eid, d):
    """Las fotos del JSON de Autocull con id del curador y estrellas puestas.

    Las rutas del runner vuelven a ser ids para poder pintar las celdas; las
    que ya no estan en el evento se descartan.
    """
    porruta = {r["file_path"].lower(): r["id"] for r in con().execute(
        f"""SELECT id, file_path FROM files WHERE id IN (
                SELECT ep.photo_id FROM event_photos ep
                 WHERE ep.event_id IN ({SQL_RAMA}))""", {"eid": eid})}
    salida = []
    for f in d.get("fotos") or []:
        pid = porruta.get((f.get("ruta") or "").lower())
        if pid is None:
            continue
        salida.append({"id": pid, "puntaje": f.get("puntaje"),
                       "decision": f.get("decision"), "motivo": f.get("motivo")})
    return _repartir_estrellas(salida)


def _estrellas_evento(eid):
    """{photo_id: estrellas} del ultimo Autocull del evento; {} si no corrio."""
    p = _autocull_json(eid)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if d.get("estado") != "listo":
        return {}
    return {f["id"]: f["estrellas"] for f in _fotos_con_estrellas(eid, d)}


@app.route("/api/evento/<int:eid>/autocull", methods=["GET"])
def autocull_estado(eid):
    """Avance y resultado. El runner reescribe el JSON mientras corre."""
    p = _autocull_json(eid)
    if not p.exists():
        return jsonify(estado="sin_correr")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return jsonify(estado="corriendo", hechas=0, total=0)  # justo se estaba reescribiendo

    if d.get("estado") == "listo" and d.get("fotos"):
        salida = _fotos_con_estrellas(eid, d)
        salida.sort(key=lambda x: (-(x["estrellas"]), -(x["puntaje"] or 0)))
        d["fotos"] = salida
    return jsonify(d)


@app.route("/api/evento/<int:eid>/autocull", methods=["POST"])
def autocull_correr(eid):
    """Lanza Autocull sobre las fotos del evento, en segundo plano."""
    if not AUTOCULL_PY.exists():
        return jsonify(ok=False, error=f"no encuentro el python de Autocull en {AUTOCULL_PY}"), 500

    p = _autocull_json(eid)
    if p.exists():
        try:
            corriendo = json.loads(p.read_text(encoding="utf-8")).get("estado") in (
                "arrancando", "corriendo")
        except (json.JSONDecodeError, OSError):
            corriendo = False
        # Si el proceso muere de golpe (lo matan, se lo lleva el reinicio del
        # curador, se cae una libreria nativa) nunca llega a escribir el
        # "error" y el JSON queda en "corriendo" para siempre: el boton no
        # vuelve a andar nunca mas. El archivo se reescribe 2 veces por
        # segundo mientras vive, asi que un rato sin tocarlo es que ya murio.
        if corriendo and time.time() - p.stat().st_mtime > LOCK_MUERTO:
            corriendo = False
        if corriendo:
            return jsonify(ok=False, error="ya hay un análisis corriendo"), 409

    c = con()
    # Sobre que archivos corre: lo elige el filtro de la pagina. Por omision
    # los JPEG, que es lo rapido; con tipo=raw analiza los RAW, que dan mejor
    # dato pero tardan bastante mas. "todos" no se acepta a proposito: correrlo
    # sobre el RAW y su JPEG es analizar dos veces la misma toma.
    fmt = request.args.get("formato")
    fmt = fmt if fmt in ("raw", "jpeg") else "jpeg"
    fotos = [r["file_path"] for r in c.execute(
        f"""SELECT f.file_path FROM files f
             WHERE COALESCE(f.borrado,0)=0 {_filtro_formato(fmt)}
               AND f.id IN (SELECT ep.photo_id FROM event_photos ep
                             WHERE ep.event_id IN ({SQL_RAMA}))""", {"eid": eid})]
    if not fotos:
        return jsonify(
            ok=False, error=f"el evento no tiene {fmt.upper()} para analizar"), 400

    AUTOCULL_SALIDA.mkdir(parents=True, exist_ok=True)
    lista = AUTOCULL_SALIDA / f"{eid}.lista.txt"
    lista.write_text("\n".join(fotos), encoding="utf-8")
    p.write_text(json.dumps({"estado": "arrancando", "hechas": 0,
                             "total": len(fotos), "segundos": 0.0,
                             "error": None, "fotos": [], "tipo": fmt}),
                 encoding="utf-8")

    # A un archivo y no a DEVNULL: cuando el runner se muere de una forma que
    # no puede capturar (una libreria nativa que se cae, el proceso matado
    # desde afuera) el JSON queda sin "error" y esto es lo unico que queda
    # para saber que paso.
    log = open(AUTOCULL_SALIDA / f"{eid}.log", "w", encoding="utf-8", errors="replace")
    # DETACHED: el analisis dura mucho mas que el request, y si cuelga del
    # proceso de Flask se muere al reiniciar el curador.
    subprocess.Popen(
        [str(AUTOCULL_PY), str(Path(__file__).parent / "autocull_runner.py"),
         str(lista), str(p)],
        cwd=str(AUTOCULL_DIR),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
        stdout=log, stderr=subprocess.STDOUT)
    return jsonify(ok=True, total=len(fotos), tipo=fmt)


# --------------------------------------------------------------------------
# Descargar originales (uno suelto o varios en un ZIP)
# --------------------------------------------------------------------------

RAW_EXT = (".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2")


def _ruta_original(pid):
    r"""La ruta a bajar: el RAW hermano si existe, si no el archivo propio.

    El RAW y su JPEG son dos filas distintas en `files`, no una con dos
    formatos. Se busca en dos pasos, del mas barato al mas caro:

    1. Al lado: misma carpeta y mismo nombre. Va por rango sobre file_path
       (que tiene el indice unico) en vez de LIKE, que no lo usaria.
    2. En otro arbol. Es lo normal en el flujo de Renzo: el RAW se queda en la
       carpeta de la SD (K:\SD_Liga\D\DCIM@MSDCF\DSC09049.ARW) y el JPEG
       sale a la de exportacion (K:otos\...\jpeg\DSC09049.jpg). El paso 1
       no encuentra ni uno de esos pares. Se aparea por nombre + instante
       exacto de captura, que con microsegundos es practicamente una clave.
    """
    row = con().execute("SELECT file_path, capture_date FROM files WHERE id=?",
                        (pid,)).fetchone()
    if not row:
        return None
    p = Path(row["file_path"])
    if p.suffix.lower() in RAW_EXT:
        return p

    pref = str(p.with_suffix("")) + "."
    for h in con().execute(
            """SELECT file_path FROM files
               WHERE file_path >= ? AND file_path < ? AND COALESCE(borrado,0)=0""",
            (pref, pref + "￿")):
        if Path(h["file_path"]).suffix.lower() in RAW_EXT:
            return Path(h["file_path"])

    if row["capture_date"]:
        nombre = p.stem.lower()
        for h in con().execute(
                """SELECT file_path FROM files
                   WHERE capture_date = ? AND COALESCE(borrado,0)=0""",
                (row["capture_date"],)):
            q = Path(h["file_path"])
            if q.suffix.lower() in RAW_EXT and q.stem.lower() == nombre:
                return q
    return p


def _desc_estrellas(vals):
    """'4 y 5 estrellas' / '3 estrellas' / '2 a 5 estrellas'. Las 0 no cuentan:
    son las descartadas por Autocull o las que nunca se analizaron."""
    v = sorted({x for x in vals if x})
    if not v:
        return ""
    if len(v) == 1:
        return f"{v[0]} estrellas"
    if len(v) == 2:
        return f"{v[0]} y {v[1]} estrellas"
    return f"{v[0]} a {v[-1]} estrellas"


def _limpiar_nombre(s):
    s = "".join(" " if ord(c) < 32 else c for c in s)
    for c in '<>:"/\\|?*':
        s = s.replace(c, "-")
    return " ".join(s.split())[:120].strip(" .") or "fotos"


def _nombre_zip(eid, ids):
    """«F2 - 09-05-2026 (82 fotos, 4 y 5 estrellas).zip»"""
    base = "fotos"
    if eid:
        ev = con().execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if ev:
            base = nombre_evento(dict(ev))
    partes = [f"{len(ids)} fotos"]
    if eid:
        est = _estrellas_evento(eid)
        d = _desc_estrellas(est.get(i, 0) for i in ids)
        if d:
            partes.append(d)
    return _limpiar_nombre(f"{base} ({', '.join(partes)})") + ".zip"


@app.route("/descargar/<int:pid>")
def descargar_una(pid):
    ruta = _ruta_original(pid)
    if not ruta or not ruta.exists():
        abort(404)
    return send_file(ruta, as_attachment=True, download_name=ruta.name)


@app.route("/descargar/zip", methods=["POST"])
def descargar_zip():
    """Arma un ZIP con los originales de las fotos marcadas.

    ponytail: se arma entero en un temporal antes de mandarlo, y Flask atiende
    de a uno: mientras se arma, el curador no responde. Con 50-100 fotos por
    LAN son segundos; para bajar un evento completo conviene el dock por USB, y
    si aun asi molesta, pasarlo a segundo plano como el runner de Autocull.
    """
    ids = [int(x) for x in (request.form.get("ids") or "").split(",") if x.strip()]
    if not ids:
        return jsonify(ok=False, error="no hay fotos seleccionadas"), 400
    eid = request.form.get("eid", type=int)

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    faltan, usados = 0, {}
    try:
        # ZIP_STORED: JPEG y RAW ya vienen comprimidos, deflate solo gasta CPU.
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for pid in ids:
                ruta = _ruta_original(pid)
                if not ruta or not ruta.exists():
                    faltan += 1
                    continue
                clave = ruta.name.lower()
                vistas = usados.get(clave, 0)
                usados[clave] = vistas + 1
                # el mismo nombre en otra carpeta: DSC09850.JPG, DSC09850 (2).JPG
                nombre = ruta.name if not vistas else f"{ruta.stem} ({vistas+1}){ruta.suffix}"
                z.write(ruta, nombre)
            if faltan:
                z.writestr("FALTANTES.txt",
                           f"{faltan} de {len(ids)} no se pudieron leer del disco.\n"
                           "Suele ser que K: no esta montado o el archivo se movio.\n")
    except Exception as e:
        os.unlink(tmp.name)
        return jsonify(ok=False, error=str(e)), 500

    @after_this_request
    def _borrar(resp):
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return resp

    return send_file(tmp.name, as_attachment=True, download_name=_nombre_zip(eid, ids))


@app.route("/evento/<int:eid>/guardar", methods=["POST"])
def guardar(eid):
    d = request.form
    crudo = (d.get("parent_id") or "").strip()
    padre = int(crudo) if crudo.isdigit() else None
    # El desplegable ya no ofrece la descendencia, pero se revisa igual: un
    # ciclo deja colgado el CTE recursivo que arma el arbol.
    if padre is not None and padre in descendientes(con(), eid):
        return redirect(url_for("evento", eid=eid, error="ciclo"))
    con().execute(
        """UPDATE events SET name=?, start_time=?, end_time=?,
                             coleccion=?, coleccion_tipo=?, notas=?, confirmado=?,
                             parent_id=?
           WHERE id=?""",
        (d.get("name", "").strip(), d.get("start_time", "").strip(),
         d.get("end_time", "").strip(),
         d.get("coleccion", "").strip(), d.get("coleccion_tipo", "").strip(),
         d.get("notas", "").strip(), 1 if d.get("confirmado") else 0, padre, eid))
    con().commit()
    return redirect(url_for("evento", eid=eid))


# --------------------------------------------------------------------------
# Fotos sin evento (rescatar de _Telefono / _Sueltas)
# --------------------------------------------------------------------------

SIN_EVENTO = """FROM files f
                WHERE f.id NOT IN (SELECT photo_id FROM event_photos)
                  AND COALESCE(f.borrado,0)=0 AND """

# Cada vista es un FROM+WHERE; el resto (orden, paginado, JSON) se comparte.
# danado: 1 = perdió datos, 2 = solo le falta el marcador de fin (abre igual).
# La distinción importa: contarlos juntos infló el daño 10x.
_VIVAS = "FROM files f WHERE COALESCE(f.borrado,0)=0 AND "
_DANADAS = _VIVAS + "f.danado = 1"

VISTAS = {
    "sueltas":  SIN_EVENTO + "COALESCE(f.dispositivo,'') <> 'telefono'",
    "telefono": SIN_EVENTO + "f.dispositivo = 'telefono'",
    "basurero": "FROM files f WHERE f.borrado = 1",
    # El barrido de integridad marcó estas; acá se revisan a ojo antes de tirarlas.
    # Una vista por motivo en vez de un filtro aparte: así el scroll infinito
    # sigue funcionando sin tocar nada (ya arrastra `vista` en cada tramo).
    "danadas":      _DANADAS,
    "danadas_raw":  _DANADAS + " AND f.danado_motivo LIKE 'RAW%'",
    "danadas_jpeg": _DANADAS + " AND f.danado_motivo NOT LIKE 'RAW%'",
    "danadas_menor": _VIVAS + "f.danado = 2",
}

# Los ids de un evento y de todo lo que cuelga de el. Las fotos viven en las
# hojas —una liga no tiene fotos propias, las tienen sus fechas—, asi que sin
# esto la pagina de un semestre sale vacia y no hay nada sobre lo que correr
# Autocull. Para una hoja la rama es ella sola, o sea que no cambia nada.
SQL_RAMA = """WITH RECURSIVE rama(id) AS (
                  SELECT :eid
                  UNION
                  SELECT e.id FROM events e JOIN rama r ON e.parent_id = r.id)
              SELECT id FROM rama"""

# Via `f.id IN (...)` y no con un JOIN a event_photos: el PK es
# (event_id, photo_id), asi que nada impide que una foto este en dos eventos
# de la misma rama, y por el JOIN saldria dos veces en la grilla. Con IN no
# puede pasar, sin pagar el DISTINCT sobre 30 mil filas.
VISTAS["evento"] = f"""FROM files f
    WHERE COALESCE(f.borrado,0)=0 AND f.id IN (
        SELECT ep.photo_id FROM event_photos ep
         WHERE ep.event_id IN ({SQL_RAMA}))"""

# Igual pero sin bajar por la rama: solo lo puesto directamente en el evento.
# Lo usa el interruptor de la pagina del evento.
VISTAS["evento_solo"] = """FROM files f
    WHERE COALESCE(f.borrado,0)=0 AND f.id IN (
        SELECT ep.photo_id FROM event_photos ep WHERE ep.event_id = :eid)"""

# --------------------------------------------------------------------------
# Temas: cortes transversales que cruzan fechas y eventos
# --------------------------------------------------------------------------
# "Todas las de naturaleza" no es un evento: una foto de plantas del viaje a
# Puelo pertenece al viaje Y al tema. Como evento habria que elegir uno de los
# dos, o duplicarla. Como vista no hay nada que elegir ni que duplicar: se
# calcula al vuelo sobre las scene_tags que la Fase 2 ya dejo escritas.
TEMAS = {
    "naturaleza": ["forest", "garden", "flowers", "park", "lake", "waterfall",
                   "mountain", "beach", "foggy landscape", "river", "desert",
                   "sunset", "starry night sky"],
    "animales":   ["cat", "dog", "bird", "farm animals", "zoo",
                   "fish underwater", "horse", "insect"],
    "ciudad":     ["city street", "historic building", "monument", "museum",
                   "church interior", "store interior", "small town"],
    "comida":     ["plate of food", "dessert or cake", "restaurant", "cafe",
                   "birthday cake", "bar"],
    "fiestas":    ["birthday party", "new year party", "wedding reception",
                   "concert", "dancing", "graduation ceremony", "parade"],
    "gente":      ["group photo of people", "portrait of a person",
                   "couple together", "family photo", "a baby", "selfie"],
}


def _sql_tema(etiquetas):
    """Sale de tag_foto, no de un LIKE sobre scene_tags: ver asegurar_tag_foto().
    Las etiquetas salen de TEMAS, que es nuestro, pero igual se rechaza cualquier
    comilla por las dudas."""
    limpias = [t for t in etiquetas if '"' not in t and "'" not in t]
    if not limpias:
        return " FROM files f WHERE 0 "
    lista = ",".join(f"'{t}'" for t in limpias)
    return (" FROM files f "
            " WHERE COALESCE(f.borrado,0)=0 AND EXISTS ("
            f"   SELECT 1 FROM tag_foto tf"
            f"   WHERE tf.photo_id = f.id AND tf.tag IN ({lista}))")


# TEMAS de arriba es solo la semilla: a partir de la primera corrida los temas
# viven en la tabla `temas` y se editan desde la web. Se guardan como una fila
# por tema con la lista de etiquetas en JSON —igual que scene_tags— en vez de
# una tabla de union: son 6 filas de 6 a 13 etiquetas, y con JSON no hay joins
# ni cascadas que mantener.
_CACHE_TEMAS = {"ts": 0.0, "datos": None}


def crear_tabla_temas(c):
    """Crea la tabla y la siembra con los temas que estaban en el codigo."""
    c.execute("""CREATE TABLE IF NOT EXISTS temas (
                     nombre    TEXT PRIMARY KEY,
                     etiquetas TEXT NOT NULL,
                     oculto    INTEGER NOT NULL DEFAULT 0,
                     orden     INTEGER NOT NULL DEFAULT 0)""")
    if not c.execute("SELECT COUNT(*) FROM temas").fetchone()[0]:
        c.executemany("INSERT INTO temas (nombre, etiquetas, orden) VALUES (?,?,?)",
                      [(n, json.dumps(t), i) for i, (n, t) in enumerate(TEMAS.items())])
        print(f"  + tabla temas sembrada con {len(TEMAS)} temas")


def temas_guardados(incluir_ocultos=True, max_edad=5):
    """[(nombre, [etiquetas], oculto)] en orden. Cacheado unos segundos: se
    consulta en cada request para armar la barra lateral."""
    if time.time() - _CACHE_TEMAS["ts"] > max_edad or _CACHE_TEMAS["datos"] is None:
        filas = []
        for r in con().execute(
                "SELECT nombre, etiquetas, oculto FROM temas ORDER BY orden, nombre"):
            try:
                tags = json.loads(r["etiquetas"])
            except (json.JSONDecodeError, TypeError):
                tags = []
            filas.append((r["nombre"], tags, bool(r["oculto"])))
        _CACHE_TEMAS.update(ts=time.time(), datos=filas)
    datos = _CACHE_TEMAS["datos"]
    return datos if incluir_ocultos else [x for x in datos if not x[2]]


def olvidar_temas():
    """Invalida el cache. Se llama despues de cualquier edicion."""
    _CACHE_TEMAS.update(ts=0.0, datos=None)


def refrescar_vistas_tema():
    """Rearma las entradas `tema:*` de VISTAS con lo que diga la base.

    VISTAS es un dict de fragmentos SQL que se arma una vez; los temas ahora
    cambian en caliente, asi que sus entradas se rehacen despues de editar.
    """
    for k in [k for k in VISTAS if k.startswith("tema:")]:
        del VISTAS[k]
    for nombre, tags, _oculto in temas_guardados():
        if tags:
            VISTAS[f"tema:{nombre}"] = _sql_tema(tags)


def etiquetas_sin_tema():
    """Las del vocabulario que no estan en ningun tema."""
    puestas = {t for _n, tags, _o in temas_guardados() for t in tags}
    return [(t, n) for t, n in etiquetas_con_cuenta() if t not in puestas]


def vocabulario():
    """Todas las etiquetas elegibles para un tema.

    etiquetas_con_cuenta() solo devuelve las que hoy tienen alguna foto viva, y
    hay etiquetas en cero (`mountain`, `insect`, `restaurant`) que igual estan
    dentro de un tema. Si el editor no las muestra, no quedan marcadas, y al
    guardar se caian del tema sin que nadie se entere. Por eso el vocabulario es
    la union: lo que tiene fotos MAS lo que ya esta puesto en algun tema.
    """
    conteo = dict(etiquetas_con_cuenta())
    puestas = {t for _n, tags, _o in temas_guardados() for t in tags}
    return sorted(set(conteo) | puestas)


for _nombre, _tags in TEMAS.items():
    VISTAS[f"tema:{_nombre}"] = _sql_tema(_tags)


# --------------------------------------------------------------------------
# Etiquetas sueltas y veredictos humanos
# --------------------------------------------------------------------------
# Las scene_tags salen de un z-score con UN umbral global para las 98
# etiquetas. Eso no puede andar bien parejo: 'cat' y 'night scene with lights'
# no se separan del ruido a la misma altura. Los veredictos son la correccion:
# un si/no humano por foto y etiqueta que manda sobre lo que dijo el modelo, y
# que ademas queda como datos para despues ajustar el umbral de cada etiqueta.

def _sql_etiqueta(tag):
    """Fotos de UNA etiqueta, con los veredictos aplicados encima del modelo.

    Un 'no' saca la foto aunque el modelo la haya etiquetado; un 'si' la mete
    aunque no la haya etiquetado. Por eso no alcanza con filtrar scene_tags.
    """
    t = tag.replace("'", "").replace('"', "")
    return (f""" FROM files f
        WHERE COALESCE(f.borrado,0)=0 AND (
            (EXISTS (SELECT 1 FROM tag_foto tf
                     WHERE tf.photo_id=f.id AND tf.tag='{t}')
             AND NOT EXISTS (SELECT 1 FROM etiqueta_veredicto v
                             WHERE v.photo_id=f.id AND v.etiqueta='{t}' AND v.ok=0))
            OR EXISTS (SELECT 1 FROM etiqueta_veredicto v
                       WHERE v.photo_id=f.id AND v.etiqueta='{t}' AND v.ok=1))""")


_CACHE_TAGS = {"ts": 0.0, "datos": []}


def etiquetas_con_cuenta(max_edad=300):
    """[(etiqueta, cuantas)] ordenado. Cacheado: contar las 98 etiquetas obliga
    a leer scene_tags de toda la base, que son segundos, no milisegundos."""
    if time.time() - _CACHE_TAGS["ts"] < max_edad and _CACHE_TAGS["datos"]:
        return _CACHE_TAGS["datos"]
    # un GROUP BY sobre tag_foto en vez de leer y parsear todo scene_tags:
    # 0,93 s contra los ~80 s que tardaba leyendo la tabla entera
    datos = [(r[0], r[1]) for r in con().execute(
        "SELECT tf.tag, COUNT(DISTINCT tf.photo_id) n FROM tag_foto tf "
        "JOIN files f ON f.id = tf.photo_id "
        "WHERE COALESCE(f.borrado,0)=0 GROUP BY tf.tag ORDER BY n DESC")]
    _CACHE_TAGS["datos"] = datos
    _CACHE_TAGS["ts"] = time.time()
    return _CACHE_TAGS["datos"]


# El nombre de archivo sin la carpeta, en SQL: rtrim(ruta, todo-menos-barras)
# deja el prefijo de directorio, y se lo saca con replace.
_BASENAME = r"replace(f.file_path, rtrim(f.file_path, replace(f.file_path,'\','')), '')"

# La extension, con el mismo truco: rtrim deja la ruta hasta el ultimo punto,
# y sacarsela deja lo que sigue. Sirve para ordenar por tipo sin tabla nueva.
_EXT = (r"LOWER(replace(f.file_path, "
        r"rtrim(f.file_path, replace(f.file_path,'.','')), ''))")

ORDENES = {
    "fecha":     "f.capture_date",
    "nombre":    _BASENAME,
    "peso":      "f.bytes",
    "dimension": "COALESCE(f.ancho,0)*COALESCE(f.alto,0)",
    "carpeta":   "f.file_path",
    "formato":   _EXT,
}

JPEG_EXT = (".jpg", ".jpeg")


def _filtro_formato(formato):
    """Deja solo RAW o solo JPEG. Cualquier otra cosa no filtra nada.

    Va por LIKE sobre la ruta y no por una columna: la extension no esta
    guardada, y agregarla obligaria a reescribir las 162.600 filas para algo
    que el LIKE ya resuelve.
    """
    exts = {"raw": RAW_EXT, "jpeg": JPEG_EXT}.get(formato)
    if not exts:
        return ""
    return " AND (" + " OR ".join(
        f"LOWER(f.file_path) LIKE '%{e}'" for e in exts) + ")"


def _filtro_busqueda(q):
    """Busca en el nombre de archivo y en la carpeta. '' = sin filtro."""
    return (" AND f.file_path LIKE :q", {"q": f"%{q}%"}) if q else ("", {})


def consultar_fotos(vista, eid=None, orden="fecha", offset=0,
                    limite=POR_PAGINA, q="", desc=False, formato=""):
    extra, pq = _filtro_busqueda(q)
    extra += _filtro_formato(formato)
    col = ORDENES.get(orden, ORDENES["fecha"])
    dirn = "DESC" if desc else "ASC"
    p = {"eid": eid, "lim": limite, "off": offset, **pq}
    return con().execute(
        f"""SELECT f.id, f.file_path, f.capture_date, f.bytes, f.ancho, f.alto
            {VISTAS[vista]}{extra}
            ORDER BY {col} {dirn}, f.id LIMIT :lim OFFSET :off""", p).fetchall()


def contar_fotos(vista, eid=None, q="", formato=""):
    extra, pq = _filtro_busqueda(q)
    extra += _filtro_formato(formato)
    return con().execute(f"SELECT COUNT(*) c {VISTAS[vista]}{extra}",
                         {"eid": eid, **pq}).fetchone()["c"]


def lista_eventos():
    return con().execute("""SELECT id, name, suggested_name, start_time
                            FROM events ORDER BY start_time DESC""").fetchall()


def _args():
    fmt = request.args.get("formato", "")
    return dict(orden=request.args.get("orden", "fecha"),
                q=(request.args.get("q") or "").strip(),
                desc=request.args.get("desc") == "1",
                formato=fmt if fmt in ("raw", "jpeg") else "")


@app.route("/api/fotos")
def api_fotos():
    """Los siguientes tramos del scroll infinito, o un conjunto exacto de ids.

    Con `ids=1,2,3` devuelve esas fotos y nada mas, en ese mismo orden. Lo usa
    el filtro por estrellas de Autocull: las 20 mejores de un evento estan
    desparramadas entre miles, asi que filtrar solo lo ya cargado por el scroll
    mostraba 1 de 20.
    """
    crudos = request.args.get("ids")
    if crudos:
        pedidos = [int(i) for i in crudos.split(",") if i.strip().isdigit()][:500]
        if not pedidos:
            return jsonify(fotos=[])
        q = ",".join("?" * len(pedidos))
        filas = {r["id"]: r for r in con().execute(
            f"SELECT id, file_path, bytes, ancho, alto, capture_date "
            f"FROM files WHERE id IN ({q})", pedidos)}
        fotos = [filas[i] for i in pedidos if i in filas]   # respeta el orden pedido
    else:
        vista = request.args.get("vista", "sueltas")
        if vista not in VISTAS:
            abort(404)
        fotos = consultar_fotos(vista, request.args.get("eid", type=int),
                                offset=request.args.get("offset", 0, type=int), **_args())
    return jsonify(fotos=[{"id": f["id"],
                           "nombre": os.path.basename(f["file_path"]),
                           "ruta": f["file_path"],
                           "meta": _meta(f),
                           "hora": (f["capture_date"] or "")[11:16]} for f in fotos])


def _meta(f):
    """Peso y dimensiones para mostrar bajo la miniatura al ordenar por eso."""
    p = []
    if f["bytes"] and f["bytes"] > 0:
        mb = f["bytes"] / 1024 ** 2
        p.append(f"{mb:.1f} MB" if mb >= 1 else f"{f['bytes']//1024} KB")
    if (f["ancho"] or 0) > 0 and (f["alto"] or 0) > 0:
        p.append(f"{f['ancho']}×{f['alto']}")
    return " · ".join(p)


def _pantalla(vista, plantilla):
    a = _args()
    return render_template(plantilla, vista=vista, **a,
                           fotos=consultar_fotos(vista, **a),
                           n=contar_fotos(vista, q=a["q"], formato=a["formato"]),
                           total=contar_fotos(vista), otros=lista_eventos())


@app.route("/sin-evento")
def sin_evento():
    # Las de teléfono viven en su propia pantalla: acá solo lo que puede
    # llegar a merecer un evento.
    return _pantalla("sueltas", "sueltas.html")


@app.route("/telefono")
def telefono():
    return _pantalla("telefono", "telefono.html")


@app.route("/temas")
def temas():
    """Portada de los temas, con cuantas fotos tiene cada uno."""
    filas = []
    for nombre, tags, oculto in temas_guardados():
        n = contar_fotos(f"tema:{nombre}") if tags else 0
        filas.append({"nombre": nombre, "n": n, "etiquetas": tags, "oculto": oculto})
    return render_template("temas.html", temas=filas,
                           sueltas=etiquetas_sin_tema(),
                           todas_etiquetas=vocabulario(),
                           otros=lista_eventos())


# --------------------------------------------------------------------------
# Editar temas desde la web
# --------------------------------------------------------------------------
# Antes los temas eran un dict en este archivo: agregar uno obligaba a editar
# codigo y reiniciar. Ahora son filas, asi que se arman mientras se curan las
# fotos, que es cuando uno se da cuenta de que falta uno.

def _nombre_tema(crudo):
    """Nombre limpio y usable en una URL. Vacio = invalido."""
    n = " ".join((crudo or "").split()).lower()
    return n if n and "/" not in n and len(n) <= 40 else ""


def _validar_etiquetas(crudas):
    """Solo etiquetas del vocabulario real, sin repetir y en orden estable."""
    if not isinstance(crudas, list):
        return []
    validas = set(vocabulario())
    vistas, salida = set(), []
    for t in crudas:
        if isinstance(t, str) and t in validas and t not in vistas:
            vistas.add(t)
            salida.append(t)
    return salida


@app.route("/api/tema", methods=["POST"])
def api_tema():
    """Crear, renombrar, cambiar etiquetas, ocultar o borrar un tema.

    {"accion": "crear"|"guardar"|"ocultar"|"borrar"|"agregar_etiqueta", ...}
    """
    d = request.get_json(force=True, silent=True) or {}
    accion = d.get("accion")
    nombre = _nombre_tema(d.get("nombre"))
    if not nombre:
        return jsonify(ok=False, error="nombre invalido"), 400
    c = con()
    existe = c.execute("SELECT 1 FROM temas WHERE nombre=?", (nombre,)).fetchone()

    if accion == "crear":
        if existe:
            return jsonify(ok=False, error=f"ya existe un tema «{nombre}»"), 409
        orden = c.execute("SELECT COALESCE(MAX(orden),0)+1 FROM temas").fetchone()[0]
        c.execute("INSERT INTO temas (nombre, etiquetas, orden) VALUES (?,?,?)",
                  (nombre, json.dumps(_validar_etiquetas(d.get("etiquetas") or [])), orden))

    elif accion == "guardar":
        if not existe:
            return jsonify(ok=False, error="no existe ese tema"), 404
        tags = _validar_etiquetas(d.get("etiquetas") or [])
        nuevo = _nombre_tema(d.get("nuevo_nombre")) or nombre
        if nuevo != nombre and c.execute("SELECT 1 FROM temas WHERE nombre=?",
                                         (nuevo,)).fetchone():
            return jsonify(ok=False, error=f"ya existe un tema «{nuevo}»"), 409
        c.execute("UPDATE temas SET nombre=?, etiquetas=? WHERE nombre=?",
                  (nuevo, json.dumps(tags), nombre))

    elif accion == "agregar_etiqueta":
        # Para mandar una etiqueta huerfana a un tema desde la lista de etiquetas.
        if not existe:
            return jsonify(ok=False, error="no existe ese tema"), 404
        etq = d.get("etiqueta")
        actuales = json.loads(c.execute("SELECT etiquetas FROM temas WHERE nombre=?",
                                        (nombre,)).fetchone()[0])
        tags = _validar_etiquetas(actuales + [etq])
        c.execute("UPDATE temas SET etiquetas=? WHERE nombre=?",
                  (json.dumps(tags), nombre))

    elif accion == "ocultar":
        if not existe:
            return jsonify(ok=False, error="no existe ese tema"), 404
        c.execute("UPDATE temas SET oculto=? WHERE nombre=?",
                  (int(bool(d.get("oculto", True))), nombre))

    elif accion == "borrar":
        if not existe:
            return jsonify(ok=False, error="no existe ese tema"), 404
        # Borrar un tema no toca ninguna foto ni ninguna etiqueta: un tema es
        # solo una forma de agrupar lo que el analisis ya dejo escrito.
        c.execute("DELETE FROM temas WHERE nombre=?", (nombre,))

    else:
        return jsonify(ok=False, error="accion desconocida"), 400

    c.commit()
    olvidar_temas()
    refrescar_vistas_tema()
    return jsonify(ok=True)


@app.route("/etiquetas")
def etiquetas():
    """Las 98 etiquetas del vocabulario, con cuantas fotos tiene cada una y
    cuantas ya revisaste."""
    rev = {r["etiqueta"]: (r["si"], r["no"]) for r in con().execute(
        """SELECT etiqueta, SUM(ok) si, SUM(1-ok) no
           FROM etiqueta_veredicto GROUP BY etiqueta""")}
    filas = [{"tag": t, "n": n, "si": rev.get(t, (0, 0))[0], "no": rev.get(t, (0, 0))[1]}
             for t, n in etiquetas_con_cuenta()]
    return render_template("etiquetas.html", filas=filas,
                           revisadas=sum(a + b for a, b in rev.values()),
                           otros=lista_eventos())


@app.route("/etiqueta/<tag>")
def etiqueta(tag):
    if tag not in {t for t, _ in etiquetas_con_cuenta()}:
        abort(404)
    vista = _vista_de_etiqueta(tag)
    a = _args()
    return render_template("etiqueta.html", vista=vista, tag=tag, **a,
                           fotos=consultar_fotos(vista, **a),
                           n=contar_fotos(vista, q=a["q"], formato=a["formato"]),
                           total=contar_fotos(vista), otros=lista_eventos())


@app.route("/revisar/<tag>")
def revisar(tag):
    """El repaso rapido: una foto por vez, «¿es <tag>?», flechas del teclado."""
    if tag not in {t for t, _ in etiquetas_con_cuenta()}:
        abort(404)
    hechas = con().execute("SELECT COUNT(*) c FROM etiqueta_veredicto WHERE etiqueta=?",
                           (tag,)).fetchone()["c"]
    return render_template("revisar.html", tag=tag, hechas=hechas, otros=lista_eventos())


# Dos fotos son "el mismo momento" si se dispararon casi juntas Y se parecen.
#
# El hamming se calibro contra algo que no depende del phash: el numero de
# disparo de la camara. Tomando pares vecinos dentro de 10s en «flowers»,
# separados en misma-foto / rafaga / otra-toma segun ese numero:
#
#     lo que difiere el phash        mediana   p90   p99   max
#     misma foto en otro formato        0       12    22    32
#     disparo consecutivo (rafaga)     10       26    34    41
#
# O sea que la MISMA foto guardada en .ARW y en .dng puede diferir 32 bits: el
# preview de cada RAW se decodifica distinto. Con el 12 de antes se separaban
# el 9,3% de los duplicados literales — eso es lo que se veia repetido diez
# fotos despues. Con 20 se agarra el 98,8%.
#
# Los segundos casi no deciden nada (el 1,7% de los cortes eran solo por
# tiempo), asi que quedan en 10: subirlos no arregla nada y agranda grupos.
GRUPO_SEG = 10
GRUPO_HAMMING = 20
# Tope de fotos por tarjeta. Una racha larga puede ser una sola toma de verdad
# (12 disparos en 16 segundos, con sus copias, dan 48 fotos), pero 48 en el
# mosaico quedan a 100px y no se puede decidir nada. Se parte en pedazos, que
# salen uno atras del otro: la misma toma nunca vuelve a aparecer diez tarjetas
# despues, que era el problema original. Afecta al 1,7% de las tarjetas.
GRUPO_MAX = 12
VENTANA_REVISAR = 120     # cuantas fotos se traen por tanda para agrupar


def _hamming_hex(a, b):
    """Bits distintos entre dos phash hex. Sin phash -> 64 (nunca agrupa).

    Hay phash degenerados (el 0000000000000000 de los RAW cuyo preview no se
    pudo decodificar lo comparten 80 fotos que no tienen nada que ver), pero
    aca no molestan: para agrupar hace falta ademas caer dentro de GRUPO_SEG,
    y dos fotos disparadas con 10 segundos de diferencia son el mismo momento
    aunque el phash no diga nada.
    """
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def _momento(s):
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def agrupar_por_momento(filas):
    """Corta una lista ordenada por fecha en rachas del mismo momento.

    Encadenado, no todos-contra-todos: A agrupa con B y B con C aunque A y C ya
    no se parezcan. Es lo correcto para una ráfaga, donde el encuadre se corre
    de a poco — y ademas es O(n).

    Las rachas de mas de GRUPO_MAX salen partidas en pedazos consecutivos.
    """
    if not filas:
        return []
    grupos, actual = [], [filas[0]]
    for f in filas[1:]:
        prev = actual[-1]
        t0, t1 = _momento(prev["capture_date"]), _momento(f["capture_date"])
        junto = (t0 is not None and t1 is not None and abs(t1 - t0) <= GRUPO_SEG
                 and _hamming_hex(prev["phash"], f["phash"]) <= GRUPO_HAMMING)
        if junto:
            actual.append(f)
        else:
            grupos.append(actual)
            actual = [f]
    grupos.append(actual)
    return [g[i:i + GRUPO_MAX] for g in grupos for i in range(0, len(g), GRUPO_MAX)]


@app.route("/api/reiniciar-revision", methods=["POST"])
def api_reiniciar_revision():
    """Borra tus veredictos de una etiqueta para revisarla de cero.

    Sirve cuando revisaste con un criterio y despues lo cambiaste: los
    veredictos viejos y los nuevos mezclados son peores que no tener ninguno,
    porque son justamente los datos para recalibrar el umbral.

    Antes de borrar deja un respaldo en _respaldos_veredictos/, asi la decision
    es reversible: son unos pocos KB por etiqueta.
    """
    d = request.get_json(force=True, silent=True) or {}
    tag = d.get("etiqueta")
    if not tag or tag not in set(vocabulario()):
        return jsonify(ok=False, error="etiqueta desconocida"), 400
    solo = d.get("solo")            # "si" | "no" | None (todos)
    cond, param = "", [tag]
    if solo in ("si", "no"):
        cond = " AND ok=?"
        param.append(1 if solo == "si" else 0)

    c = con()
    filas = c.execute(
        f"SELECT photo_id, etiqueta, ok, ts FROM etiqueta_veredicto "
        f"WHERE etiqueta=?{cond}", param).fetchall()
    if not filas:
        return jsonify(ok=False, error="no hay veredictos que borrar"), 404

    resp = Path(__file__).parent / "_respaldos_veredictos"
    resp.mkdir(exist_ok=True)
    seguro = "".join(ch if ch.isalnum() else "_" for ch in tag)
    arch = resp / f"{seguro}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    arch.write_text(json.dumps([dict(r) for r in filas], ensure_ascii=False),
                    encoding="utf-8")

    n = c.execute(f"DELETE FROM etiqueta_veredicto WHERE etiqueta=?{cond}", param).rowcount
    c.commit()
    return jsonify(ok=True, borrados=n, respaldo=arch.name)


@app.route("/api/referencia/<tag>")
def api_referencia(tag):
    """Con que comparar mientras revisas: la traduccion y ejemplos ya resueltos.

    «¿esta foto es waterfall?» es incontestable si no sabes que quiere decir la
    palabra ni donde corta el modelo. Se devuelven tres cosas:

      - traduccion y una aclaracion de los casos de borde (glosario_etiquetas)
      - las que YA dijiste que si, y las que dijiste que no: tu propio criterio,
        que es lo unico consistente contra lo que comparar
      - si todavia no votaste nada, una muestra de lo que propuso el modelo,
        marcada como sin confirmar para no tomarla como verdad
    """
    # vocabulario() y no etiquetas_con_cuenta(): las que hoy tienen cero fotos
    # igual son etiquetas reales y el glosario las cubre. Responder con las
    # listas vacias es mas util que un 404.
    if tag not in set(vocabulario()):
        abort(404)
    trad, ojo = explicar(tag)

    def fotos(ok, limite=8):
        return [r["id"] for r in con().execute(
            """SELECT f.id FROM etiqueta_veredicto v JOIN files f ON f.id = v.photo_id
                WHERE v.etiqueta=? AND v.ok=? AND COALESCE(f.borrado,0)=0
                  AND COALESCE(f.danado,0)=0
                ORDER BY v.ts DESC LIMIT ?""", (tag, ok, limite))]

    si, no = fotos(1), fotos(0)
    modelo = []
    if not si:
        # Sin veredictos propios: lo que el modelo marco, para al menos ver de
        # que esta hablando. No es verdad confirmada y la pagina lo aclara.
        modelo = [r["id"] for r in con().execute(
            f"""SELECT f.id FROM files f
                 WHERE COALESCE(f.borrado,0)=0 AND COALESCE(f.danado,0)=0
                   AND EXISTS (SELECT 1 FROM tag_foto tf
                               WHERE tf.photo_id=f.id AND tf.tag=?) LIMIT 8""", (tag,))]
    return jsonify(tag=tag, traduccion=trad, ojo=ojo, si=si, no=no, modelo=modelo)


@app.route("/api/revisar/<tag>")
def api_revisar(tag):
    """Las siguientes fotos sin veredicto, agrupadas por momento.

    Se mandan de a tandas para que el repaso no espere al servidor entre foto y
    foto: el JS precarga las imagenes de la tanda mientras vos mirás la primera.

    Va por fecha, no al azar: agrupar exige que las de un mismo momento caigan
    juntas. El costo es que el repaso deja de ser una muestra variada del
    archivo y pasa a ser un barrido cronologico.
    """
    if tag not in {t for t, _ in etiquetas_con_cuenta()}:
        abort(404)
    # danado=0 tambien: una foto rota no dice nada sobre si la etiqueta acierta,
    # y ademas asi la que marcas como dañada no vuelve a aparecer.
    filtro = """FROM files f JOIN photo_features pf ON pf.photo_id = f.id
                     LEFT JOIN etiqueta_score es
                            ON es.photo_id = f.id AND es.etiqueta = ?
                WHERE COALESCE(f.borrado,0)=0 AND COALESCE(f.danado,0)=0
                  AND EXISTS (SELECT 1 FROM tag_foto tf
                              WHERE tf.photo_id=f.id AND tf.tag=?)
                  AND NOT EXISTS (SELECT 1 FROM etiqueta_veredicto v
                                  WHERE v.photo_id=f.id AND v.etiqueta=?)"""
    # Por donde empezar. `dudosas` trae primero las que quedaron JUSTO en el
    # borde del umbral: son las unicas donde tu respuesta agrega informacion.
    # En una etiqueta de 88.922 fotos, revisar por fecha es contestar 200 veces
    # lo obvio antes de llegar a la primera dificil.
    dudosas = request.args.get("orden") == "dudosas"
    if dudosas:
        filas = con().execute(
            f"""SELECT f.id, f.file_path, f.capture_date, pf.phash, es.z {filtro}
                  AND es.z IS NOT NULL
                ORDER BY es.z ASC LIMIT {VENTANA_REVISAR}""",
            (tag, tag, tag)).fetchall()
        # se reordena por fecha para que el agrupado por momento siga andando:
        # agrupar exige que las de una misma racha caigan juntas
        filas = sorted(filas, key=lambda r: (r["capture_date"] or "", r["id"]))
    else:
        filas = con().execute(
            f"""SELECT f.id, f.file_path, f.capture_date, pf.phash {filtro}
                ORDER BY f.capture_date LIMIT {VENTANA_REVISAR}""",
            (tag, tag, tag)).fetchall()
    quedan = con().execute(f"SELECT COUNT(*) c {filtro}",
                           (tag, tag, tag)).fetchone()["c"]
    grupos = agrupar_por_momento(filas)
    # El ultimo grupo puede estar cortado al medio por el LIMIT: se descarta y
    # vuelve entero en la proxima tanda (las ya votadas salen del filtro).
    if len(grupos) > 1 and quedan > len(filas):
        grupos.pop()
    return jsonify(quedan=quedan, grupos=[
        [{"id": r["id"], "nombre": os.path.basename(r["file_path"])} for r in g]
        for g in grupos])


def _ids_del_pedido(d):
    """Acepta `photo_id` (una) o `photo_ids` (varias, para votar un grupo)."""
    crudos = d.get("photo_ids") if "photo_ids" in d else [d.get("photo_id")]
    if not isinstance(crudos, list) or not crudos:
        raise ValueError("faltan ids")
    return [int(x) for x in crudos]


@app.route("/api/veredicto", methods=["POST"])
def api_veredicto():
    d = request.get_json(force=True)
    try:
        ids, tag, ok = _ids_del_pedido(d), str(d["etiqueta"]), int(bool(d["ok"]))
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="faltan datos"), 400
    ahora = dt.datetime.now().isoformat(timespec="seconds")
    con().executemany("""INSERT INTO etiqueta_veredicto (photo_id, etiqueta, ok, ts)
                         VALUES (?,?,?,?)
                         ON CONFLICT(photo_id, etiqueta) DO UPDATE SET ok=excluded.ok,
                                                                       ts=excluded.ts""",
                      [(pid, tag, ok, ahora) for pid in ids])
    con().commit()
    return jsonify(ok=True, n=len(ids))


@app.route("/api/danada", methods=["POST"])
def api_danada():
    """Marca (o desmarca) una foto como dañada desde el repaso.

    Va a la misma columna `danado` que usa el barrido de integridad, con motivo
    propio para poder distinguir despues lo que marcaste a ojo de lo que detecto
    el chequeo automatico.
    """
    d = request.get_json(force=True)
    try:
        ids = _ids_del_pedido(d)
        danada = int(bool(d.get("danada", 1)))
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="faltan datos"), 400
    con().executemany("UPDATE files SET danado=?, danado_motivo=? WHERE id=?",
                      [(danada, "marcada a ojo en el repaso" if danada else None, pid)
                       for pid in ids])
    con().commit()
    return jsonify(ok=True, n=len(ids))


@app.route("/api/cajas/<int:pid>")
def api_cajas(pid):
    """Cuadros para dibujar encima de una foto: por ahora, las caras.

    Los bbox de `faces` estan en pixeles de la imagen completa (los deja
    InsightFace sobre el array que carga load_image_array). Se devuelven
    normalizados 0..1 contra files.ancho/alto para poder pintarlos encima de
    cualquier version reescalada — la miniatura, la vista de 1600, lo que sea.

    Nota: en los RAW el bbox vive en el espacio del preview incrustado, que no
    siempre coincide con ancho/alto. Por eso se recorta a 0..1 en vez de confiar.
    """
    f = con().execute("SELECT ancho, alto FROM files WHERE id=?", (pid,)).fetchone()
    if not f or not f["ancho"] or not f["alto"]:
        return jsonify(caras=[])
    w, h = float(f["ancho"]), float(f["alto"])
    caras = []
    for r in con().execute(
            """SELECT fa.id, fa.bbox_json, fa.det_score, fa.person_id, p.name
               FROM faces fa LEFT JOIN persons p ON p.id = fa.person_id
               WHERE fa.photo_id=?""", (pid,)):
        try:
            x1, y1, x2, y2 = json.loads(r["bbox_json"])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        caja = [max(0.0, min(1.0, x1 / w)), max(0.0, min(1.0, y1 / h)),
                max(0.0, min(1.0, x2 / w)), max(0.0, min(1.0, y2 / h))]
        if caja[2] <= caja[0] or caja[3] <= caja[1]:
            continue
        caras.append({"caja": caja, "score": r["det_score"],
                      "nombre": r["name"] or ("persona " + str(r["person_id"])
                                              if r["person_id"] is not None else None)})
    return jsonify(caras=caras)


@app.route("/tema/<nombre>")
def tema(nombre):
    """Un tema, con sus etiquetas como filtros.

    `?tag=cat&tag=dog` deja solo esas dos. Sin ?tag, entran todas las del tema.
    Es el paso previo al repaso: primero acotás a la etiqueta que querés mirar,
    después la revisás de a una.
    """
    guardados = {n: t for n, t, _o in temas_guardados()}
    if nombre not in guardados:
        abort(404)
    todas = guardados[nombre]
    if not todas:
        return render_template("tema_vacio.html", tema=nombre, otros=lista_eventos())
    elegidas = [t for t in request.args.getlist("tag") if t in todas] or todas
    # Con un subconjunto la vista se arma al vuelo; con todas se usa la fija.
    if len(elegidas) == len(todas):
        vista = f"tema:{nombre}"
    else:
        vista = "tema:" + nombre + ":" + ",".join(sorted(elegidas))
        VISTAS[vista] = _sql_tema(elegidas)
    a = _args()
    cuenta = {t: contar_fotos(_vista_de_etiqueta(t)) for t in todas}
    return render_template("tema.html", vista=vista, tema=nombre,
                           etiquetas=todas, elegidas=set(elegidas), cuenta=cuenta, **a,
                           fotos=consultar_fotos(vista, **a),
                           n=contar_fotos(vista, q=a["q"], formato=a["formato"]),
                           total=contar_fotos(vista),
                           otros=lista_eventos())


def _vista_de_etiqueta(tag):
    """Registra (si hace falta) la vista de una etiqueta suelta y da su clave."""
    v = f"etq:{tag}"
    if v not in VISTAS:
        VISTAS[v] = _sql_etiqueta(tag)
    return v


# --------------------------------------------------------------------------
# Acciones sobre una selección de fotos
# --------------------------------------------------------------------------

@app.route("/evento/nuevo", methods=["POST"])
def evento_nuevo():
    """Crea un evento VACIO y te lleva adentro.

    Hasta ahora un evento solo nacia moviendole fotos que ya estaban en la base
    (ver /api/mover). Eso deja afuera el caso de subir fotos que todavia no
    estan en ningun lado: sin evento no hay donde subirlas, y sin fotos no se
    podia crear el evento. Este es el huevo antes que la gallina.

    Queda sin confirmar a proposito: lo confirmas cuando tenga contenido y le
    hayas puesto nombre, que es lo que mira organizar_por_evento.py.
    """
    nombre = (request.form.get("nombre") or "").strip() or "Evento nuevo"
    hoy = dt.datetime.now().replace(microsecond=0).isoformat()
    c = con()
    cur = c.execute(
        """INSERT INTO events (suggested_name, name, start_time, end_time, confirmado)
           VALUES (?,?,?,?,0)""", (nombre, nombre, hoy, hoy))
    c.commit()
    return redirect(url_for("evento", eid=cur.lastrowid))


@app.route("/api/mover", methods=["POST"])
def mover():
    """Mueve fotos a un evento existente, a uno nuevo, o las saca de todos."""
    d = request.get_json(force=True)
    ids = [int(i) for i in d.get("ids", [])]
    if not ids:
        return jsonify(ok=False, error="sin fotos seleccionadas"), 400
    destino = d.get("destino")
    c = con()

    if destino == "nuevo":
        nombre = (d.get("nombre") or "").strip() or "Evento nuevo"
        rango = c.execute(
            f"SELECT MIN(capture_date) a, MAX(capture_date) b FROM files "
            f"WHERE id IN ({','.join('?'*len(ids))})", ids).fetchone()
        cur = c.execute(
            """INSERT INTO events (suggested_name, name, start_time, end_time, confirmado)
               VALUES (?,?,?,?,1)""", (nombre, nombre, rango["a"], rango["b"]))
        eid = cur.lastrowid
    elif destino == "ninguno":
        c.executemany("DELETE FROM event_photos WHERE photo_id=?", [(i,) for i in ids])
        c.commit()
        return jsonify(ok=True, quitadas=len(ids))
    else:
        eid = int(destino)

    # una foto pertenece a un solo evento: se saca de los demás
    c.executemany("DELETE FROM event_photos WHERE photo_id=?", [(i,) for i in ids])
    c.executemany("INSERT OR IGNORE INTO event_photos (event_id, photo_id) VALUES (?,?)",
                  [(eid, i) for i in ids])
    _recalcular_fechas(c, eid)
    c.commit()
    return jsonify(ok=True, evento=eid, movidas=len(ids))


def _fechas_desde_hijos(c, eid):
    """Un evento padre no tiene fotos propias: su rango sale de sus hijos."""
    r = c.execute("""SELECT MIN(start_time) a, MAX(end_time) b FROM events
                     WHERE parent_id=?""", (eid,)).fetchone()
    if r and r["a"]:
        c.execute("UPDATE events SET start_time=?, end_time=? WHERE id=?",
                  (r["a"], r["b"], eid))


@app.route("/api/eventos/agrupar", methods=["POST"])
def agrupar_eventos():
    """Cuelga varios eventos de un mismo padre (nuevo o existente), o los suelta.

    No mueve una sola foto: solo cambia de quien cuelga cada evento.
    """
    d = request.get_json(force=True)
    ids = [int(i) for i in d.get("ids", [])]
    if not ids:
        return jsonify(ok=False, error="ningún evento seleccionado"), 400
    destino = d.get("destino")
    c = con()

    nuevo = destino == "nuevo"
    padre = None if destino in ("nuevo", "ninguno") else int(destino)
    if padre is not None:
        if padre in ids:
            return jsonify(ok=False, error="un evento no puede colgar de sí mismo"), 400
        # colgar algo de su propia descendencia deja el arbol en ciclo
        for eid in ids:
            if padre in descendientes(c, eid):
                return jsonify(
                    ok=False,
                    error="no se puede colgar de un evento que ya cuelga de la selección"), 400
    if nuevo:      # recien aca, con la validacion pasada, para no dejar basura
        nombre = (d.get("nombre") or "").strip() or "Grupo nuevo"
        padre = c.execute(
            "INSERT INTO events (name, suggested_name, confirmado) VALUES (?,?,1)",
            (nombre, nombre)).lastrowid

    # el padre que pierde hijos también cambia de rango, no solo el que los gana
    antes = {r["parent_id"] for r in c.execute(
        f"SELECT DISTINCT parent_id FROM events WHERE id IN ({','.join('?'*len(ids))})",
        ids) if r["parent_id"]}
    c.executemany("UPDATE events SET parent_id=? WHERE id=?", [(padre, i) for i in ids])
    for viejo in antes - {padre}:
        _fechas_desde_hijos(c, viejo)
        _subir_fechas(c, viejo)
    if padre is not None:
        _fechas_desde_hijos(c, padre)
        _subir_fechas(c, padre)
    c.commit()
    return jsonify(ok=True, padre=padre, n=len(ids))


def _recalcular_fechas(c, eid):
    r = c.execute("""SELECT MIN(f.capture_date) a, MAX(f.capture_date) b
                     FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                     WHERE ep.event_id=?""", (eid,)).fetchone()
    if r and r["a"]:
        c.execute("UPDATE events SET start_time=?, end_time=? WHERE id=?",
                  (r["a"], r["b"], eid))
    _subir_fechas(c, eid)


def _subir_fechas(c, eid):
    """Vuelve a calcular el rango de todos los ancestros de *eid*.

    El rango de un padre sale de sus hijos, así que mover fotos a una hoja o
    colgarla de otro lado deja al abuelo con fechas viejas — y la portada
    ordena justo por start_time. El set corta si la base tuviera un ciclo.
    """
    visto = set()
    while True:
        r = c.execute("SELECT parent_id FROM events WHERE id=?", (eid,)).fetchone()
        if not r or not r["parent_id"] or r["parent_id"] in visto:
            return
        eid = r["parent_id"]
        visto.add(eid)
        _fechas_desde_hijos(c, eid)


# --------------------------------------------------------------------------
# Basurero
# --------------------------------------------------------------------------

@app.route("/basurero")
def basurero():
    return _pantalla("basurero", "basurero.html")


# Los nombres de pestaña van a la vista que corresponde; el scroll infinito
# hereda la vista y sigue trayendo solo esa categoría.
_TABS_DANADAS = [("todas", "danadas", "Perdieron datos"),
                 ("raw", "danadas_raw", "RAW ilegibles"),
                 ("jpeg", "danadas_jpeg", "JPEG ilegibles"),
                 ("menor", "danadas_menor", "Solo falta el marcador (abren igual)")]


@app.route("/danadas")
def danadas():
    """Las que el barrido de integridad marcó como rotas, para verlas a ojo."""
    cual = request.args.get("tipo", "todas")
    vista = next((v for k, v, _ in _TABS_DANADAS if k == cual), "danadas")
    a = _args()
    cuentas = {k: contar_fotos(v) for k, v, _ in _TABS_DANADAS}
    return render_template("danadas.html", vista=vista, tipo=cual, **a,
                           tabs=_TABS_DANADAS, cuentas=cuentas,
                           fotos=consultar_fotos(vista, **a),
                           n=contar_fotos(vista, q=a["q"], formato=a["formato"]),
                           total=contar_fotos(vista), otros=lista_eventos())


@app.route("/api/basurero", methods=["POST"])
def a_basurero():
    """Manda al basurero (o lo saca). No toca el disco: solo marca y desvincula."""
    d = request.get_json(force=True)
    ids = [int(i) for i in d.get("ids", [])]
    if not ids:
        return jsonify(ok=False, error="nada seleccionado"), 400
    c = con()
    if d.get("restaurar"):
        def _w(c):
            c.executemany("UPDATE files SET borrado=0 WHERE id=?", [(i,) for i in ids])
        _db_write(c, _w)
    else:
        def _w(c):
            c.executemany("UPDATE files SET borrado=1 WHERE id=?", [(i,) for i in ids])
            c.executemany("DELETE FROM event_photos WHERE photo_id=?", [(i,) for i in ids])
        _db_write(c, _w)
    return jsonify(ok=True, n=len(ids))


@app.route("/api/basurero/borrar", methods=["POST"])
def borrar_definitivo():
    """Borra los archivos del disco. Solo se llega acá desde el basurero."""
    d = request.get_json(force=True)
    c = con()
    if d.get("todo"):
        filas = c.execute("SELECT id, file_path FROM files WHERE borrado=1").fetchall()
    else:
        ids = [int(i) for i in d.get("ids", [])]
        if not ids:
            return jsonify(ok=False, error="nada seleccionado"), 400
        marca = ",".join("?" * len(ids))
        filas = c.execute(f"SELECT id, file_path FROM files "
                          f"WHERE id IN ({marca}) AND borrado=1", ids).fetchall()
    borrados = ya_no_estaba = fallos = 0
    for f in filas:
        try:
            if os.path.exists(f["file_path"]):
                os.remove(f["file_path"])
                borrados += 1
            else:
                # la fila se limpia igual (es basura de la base), pero esto
                # NO liberó espacio real: el archivo ya no estaba ahí.
                ya_no_estaba += 1
            c.execute("DELETE FROM event_photos WHERE photo_id=?", (f["id"],))
            c.execute("DELETE FROM photo_features WHERE photo_id=?", (f["id"],))
            c.execute("DELETE FROM files WHERE id=?", (f["id"],))
            (THUMBS / f"{f['id']}.jpg").unlink(missing_ok=True)
        except Exception:
            fallos += 1
    c.commit()
    return jsonify(ok=True, borrados=borrados, ya_no_estaba=ya_no_estaba, fallos=fallos)


# --------------------------------------------------------------------------
# Duplicados: verificar a ojo lo que propuso la Fase 5
# --------------------------------------------------------------------------

CSV_DUPLICADOS = "duplicados.csv"
CUARENTENA = Path(os.environ.get("CURADOR_CUARENTENA", r"K:\_duplicados_cuarentena"))

# Donde caen las fotos que subis desde adentro de un evento. Una carpeta por
# evento: asi el disco queda navegable sin la base, que es como podes recuperar
# el material si algun dia la base se pierde.
SUBIDAS_EVENTO = Path(os.environ.get("CURADOR_SUBIDAS", CUARENTENA.parent / "_eventos"))
_CSV_IMPORTADO = None  # mtime del CSV ya cargado, para no reimportar en cada request

SIN_SCORE = -1   # no se pudo comparar el contenido: sin phash o phash no confiable


def _hamming(a, b):
    return bin(a ^ b).count("1")


def _phash_degenerados(c, minimo=10):
    """pHash que aparecen en muchas fotos distintas: no son huella de nada.

    Un RAW/DNG cuyo preview no se pudo decodificar da una imagen plana, y todos
    terminan con el mismo hash (0000000000000000 lo comparten 80 fotos que no
    tienen nada que ver). Comparados entre si dan distancia 0 y se agrupan como
    si fueran la misma foto.

    ponytail: umbral empirico sobre el sintoma. Una rafaga real de 10+ tomas
    identicas tambien cae aca; esos pares siguen visibles uno por uno en
    /duplicados, solo se los excluye de agrupar y de puntuar automatico.
    """
    return {r[0] for r in c.execute(
        "SELECT phash FROM photo_features WHERE phash IS NOT NULL "
        "GROUP BY phash HAVING COUNT(*) >= ?", (minimo,))}


def _score(ha, hb, malos):
    """Distancia real entre dos pHash hex, o SIN_SCORE si no es confiable."""
    if not ha or not hb or ha in malos or hb in malos:
        return SIN_SCORE
    try:
        return _hamming(int(ha, 16), int(hb, 16))
    except ValueError:
        return SIN_SCORE


def recalcular_scores(c):
    """Completa el score de los pares que quedaron en SIN_SCORE.

    El CSV trae phash de los dos lados, pero los pares importados antes de que
    esto existiera quedaron con el DEFAULT -1. Eso no es inocuo: los filtros de
    Fiabilidad comparan `score <= N`, y -1 pasa TODOS, asi que 20 mil pares sin
    verificar se mostraban como los mas seguros de la lista.
    """
    malos = _phash_degenerados(c)
    ph = {r[0]: r[1] for r in c.execute(
        "SELECT photo_id, phash FROM photo_features WHERE phash IS NOT NULL")}
    lote = []
    for pid, ic, idd in c.execute(
            "SELECT id, id_conservar, id_descartar FROM duplicados WHERE score = ?",
            (SIN_SCORE,)).fetchall():
        s = _score(ph.get(ic), ph.get(idd), malos)
        if s != SIN_SCORE:
            lote.append((s, pid))
    if lote:
        c.executemany("UPDATE duplicados SET score=? WHERE id=?", lote)
        c.commit()
    return len(lote)

_EDITORES_CONOCIDOS = ("adobe", "lightroom", "photoshop", "gimp", "capture one",
                      "acdsee", "snapseed", "picasa", "affinity", "darktable",
                      "luminar", "paint.net", "paintshop")

# Convertir a DNG no es editar: es cambiar de contenedor sin perder nada. Como
# el string dice "Adobe DNG Converter", caía en el "adobe" de arriba y marcaba
# 5.636 archivos como "editado", empujando a descartar originales intactos.
_NO_SON_EDICION = ("dng converter",)


def es_editado(software):
    """True si el tag Software delata una copia ya editada."""
    if not software:
        return False
    s = software.lower()
    if any(x in s for x in _NO_SON_EDICION):
        return False
    return any(e in s for e in _EDITORES_CONOCIDOS)


def leer_software(path):
    """Tag EXIF Software leyendo solo el encabezado. Lento: toca el disco."""
    try:
        import exifread
        with open(path, "rb") as fh:
            tags = exifread.process_file(fh, stop_tag="Image Software", details=False)
        tag = tags.get("Image Software")
        return str(tag).strip() if tag else None
    except Exception:
        return None


def info_archivo(path, software=None):
    """Formato (RAW/JPEG/...) y el tag EXIF Software, para priorizar el
    original de cámara sobre una copia ya editada/exportada.

    El `software` viene de afuera (columna files.software, que llena
    precomputar_software.py). Antes se leía del archivo acá mismo y eran hasta
    80 aperturas en el disco externo por página: la revisión iba al ritmo del
    disco y peleaba con cualquier otra cosa que lo estuviera usando.
    """
    if not path:
        return {"formato": None, "software": None, "editado": False}
    ext = Path(path).suffix.lower()
    formato = "RAW" if ext in {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"} \
        else ext.lstrip(".").upper()
    return {"formato": formato, "software": software,
            "editado": es_editado(software)}


@app.route("/personas")
def personas():
    c = con()
    # List all persons, ordered by face count
    filas = c.execute("""
        SELECT p.id, p.name, p.face_count, COALESCE(p.rep_face_id, MIN(f.id)) as face_id
        FROM persons p
        JOIN faces f ON f.person_id = p.id
        WHERE p.face_count >= 1
        GROUP BY p.id
        ORDER BY p.face_count DESC
    """).fetchall()
    
    personas_data = [dict(r) for r in filas]
    return render_template("personas.html", personas=personas_data)

@app.route("/persona/<int:pid>")
def persona_detalle(pid):
    page = request.args.get("page", 1, type=int)
    limit = 100
    offset = (page - 1) * limit

    c = con()
    person = c.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    if not person:
        return "Not found", 404

    # Galeria por FOTO (no por cara): /thumb ya esta cacheado y es rapido,
    # mientras que /face/<id> recorta al vuelo la primera vez (lento, sobre
    # todo con RAW en disco externo). Ademas viendo la foto entera ya se
    # entiende cual cara es la persona sin necesidad del recorte.
    # El JOIN con files saca las que fueron al basurero: la cara sigue en la
    # tabla, pero la foto ya no esta y la galeria mostraba el hueco. Eran 4.713
    # despues de mandar al basurero las rutas huerfanas (2026-08-05).
    fotos = [r["photo_id"] for r in c.execute("""
        SELECT DISTINCT f.photo_id
        FROM faces f JOIN files img ON img.id = f.photo_id
        WHERE f.person_id = ? AND COALESCE(img.borrado,0) = 0
        ORDER BY f.photo_id
        LIMIT ? OFFSET ?
    """, (pid, limit, offset)).fetchall()]

    return render_template("persona_detalle.html", person=person, fotos=fotos, page=page, limit=limit)

@app.route("/face/<int:face_id>")
def face_thumb(face_id):
    dest = FACE_THUMBS / f"face_{face_id}.jpg"
    if dest.exists():
        return send_file(dest, mimetype="image/jpeg", max_age=86400)
        
    row = con().execute("""
        SELECT f.bbox_json, img.file_path 
        FROM faces f 
        JOIN files img ON f.photo_id = img.id 
        WHERE f.id=?
    """, (face_id,)).fetchone()
    
    if not row or not os.path.exists(row["file_path"]):
        return _placeholder("No foto")
        
    try:
        bbox = json.loads(row["bbox_json"])
    except Exception:
        return _placeholder("Error JSON")

    from utils_images import recortar_cara
    face_img = recortar_cara(row["file_path"], bbox)
    if face_img is None:
        return _placeholder("Error rec")
    with _lock:
        dest.parent.mkdir(parents=True, exist_ok=True)
        face_img.save(dest, "JPEG", quality=85)
    return send_file(dest, mimetype="image/jpeg", max_age=86400)

@app.route("/api/persona/nombrar", methods=["POST"])
def nombrar_persona():
    d = request.get_json()
    pid = int(d["id"])
    nombre = d["nombre"].strip()
    c = con()
    resultado = {}
    def _w(c):
        # Si el nombre ya existe en OTRA persona, hay que fusionar -- si no,
        # dos clusters de cara terminan con el mismo nombre pero person_id
        # distinto (asignar_cara() ya evita esto del otro lado; acá faltaba).
        destino = pid
        if nombre:
            otra = c.execute("SELECT id FROM persons WHERE name=? AND id!=?",
                             (nombre, pid)).fetchone()
            if otra:
                destino = otra["id"]
                movidas = c.execute("SELECT COUNT(*) n FROM faces WHERE person_id=?",
                                    (pid,)).fetchone()["n"]
                c.execute("UPDATE faces SET person_id=? WHERE person_id=?", (destino, pid))
                c.execute("UPDATE persons SET face_count=face_count+? WHERE id=?",
                         (movidas, destino))
                c.execute("DELETE FROM persons WHERE id=?", (pid,))
        if destino == pid:
            c.execute("UPDATE persons SET name=? WHERE id=?", (nombre if nombre else None, pid))
        resultado["person_id"] = destino
    _db_write(c, _w)
    return jsonify(ok=True, person_id=resultado["person_id"])

@app.route("/api/persona/quitar", methods=["POST"])
def quitar_cara():
    d = request.get_json()
    face_id = int(d["face_id"])
    c = con()
    def _w(c):
        row = c.execute("SELECT person_id FROM faces WHERE id=?", (face_id,)).fetchone()
        c.execute("UPDATE faces SET person_id=NULL WHERE id=?", (face_id,))
        if row and row["person_id"]:
            c.execute("UPDATE persons SET face_count=face_count-1 WHERE id=?", (row["person_id"],))
    _db_write(c, _w)
    return jsonify(ok=True)


@app.route("/foto/<int:pid>/caras")
def foto_caras(pid):
    persona_actual = request.args.get("persona", type=int)
    c = con()
    foto = c.execute("SELECT id, file_path FROM files WHERE id=?", (pid,)).fetchone()
    if not foto or not os.path.exists(foto["file_path"]):
        abort(404)
    caras = c.execute("""
        SELECT f.id as face_id, f.bbox_json, f.person_id, p.name
        FROM faces f LEFT JOIN persons p ON f.person_id = p.id
        WHERE f.photo_id = ?
    """, (pid,)).fetchall()
    caras_data = [{"face_id": r["face_id"], "bbox": json.loads(r["bbox_json"]),
                   "person_id": r["person_id"], "name": r["name"]} for r in caras]
    nombres = [r["name"] for r in c.execute(
        "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL ORDER BY name")]
    return render_template("foto_caras.html", pid=pid, caras=caras_data, nombres=nombres,
                           persona_actual=persona_actual)


@app.route("/caras/sueltas")
def caras_sueltas():
    page = request.args.get("page", 1, type=int)
    limit = 100
    offset = (page - 1) * limit
    c = con()
    filas = c.execute("""
        SELECT f.id as face_id, f.photo_id
        FROM faces f
        WHERE f.person_id IS NULL
        ORDER BY f.id
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    total = c.execute("SELECT COUNT(*) n FROM faces WHERE person_id IS NULL").fetchone()["n"]
    faces_data = [dict(r) for r in filas]
    return render_template("caras_sueltas.html", faces=faces_data, page=page, limit=limit, total=total)


@app.route("/marca-de-agua")
def marca_de_agua():
    page = request.args.get("page", 1, type=int)
    limit = 120
    offset = (page - 1) * limit
    c = con()
    tiene_tabla_ocr = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ocr_marca_agua'").fetchone()
    donde = """COALESCE(borrado,0)=0 AND (file_path LIKE '%watermark%'
               OR file_path LIKE '%MarcaDeAgua%' OR file_path LIKE '%Marca_de_Agua%'
               OR file_path LIKE '%Marca De Agua%'"""
    if tiene_tabla_ocr:
        donde += " OR id IN (SELECT photo_id FROM ocr_marca_agua WHERE tiene_marca=1)"
    donde += ")"
    total = c.execute(f"SELECT COUNT(*) n FROM files WHERE {donde}").fetchone()["n"]
    fotos = [dict(r) for r in c.execute(
        f"SELECT * FROM files WHERE {donde} ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset))]
    return render_template("marca_de_agua.html", fotos=fotos, total=total,
                           page=page, limit=limit)


@app.route("/api/cara/asignar", methods=["POST"])
def asignar_cara():
    d = request.get_json()
    face_id = int(d["face_id"])
    nombre = (d.get("nombre") or "").strip()
    c = con()
    resultado = {}
    def _w(c):
        old = c.execute("SELECT person_id FROM faces WHERE id=?", (face_id,)).fetchone()["person_id"]
        if not nombre:
            nuevo_pid = None
        else:
            row = c.execute("SELECT id FROM persons WHERE name=?", (nombre,)).fetchone()
            if row:
                nuevo_pid = row["id"]
            else:
                cur = c.execute("INSERT INTO persons (name, face_count) VALUES (?, 0)", (nombre,))
                nuevo_pid = cur.lastrowid
        if old != nuevo_pid:
            c.execute("UPDATE faces SET person_id=? WHERE id=?", (nuevo_pid, face_id))
            if old is not None:
                c.execute("UPDATE persons SET face_count=face_count-1 WHERE id=?", (old,))
            if nuevo_pid is not None:
                c.execute("UPDATE persons SET face_count=face_count+1 WHERE id=?", (nuevo_pid,))
        resultado["person_id"] = nuevo_pid
    _db_write(c, _w)
    return jsonify(ok=True, person_id=resultado["person_id"], nombre=nombre or None)


@app.route("/duplicados")
def duplicados():
    c = con()
    # Reimporta cuando la Fase 5 reescribio el CSV, no solo con la tabla vacia:
    # con el `if not hay` de antes, un CSV nuevo no entraba nunca.
    global _CSV_IMPORTADO
    if os.path.exists(CSV_DUPLICADOS):
        mtime = os.path.getmtime(CSV_DUPLICADOS)
        if mtime != _CSV_IMPORTADO:
            importar_csv(c)
            _CSV_IMPORTADO = mtime

    tipo = request.args.get("tipo") or "todos"
    dec = request.args.get("dec") or "pendiente"
    score_max = request.args.get("score_max") or "todos"
    pag = max(1, int(request.args.get("p", 1)))

    # Si CUALQUIERA de las dos ya fue al basurero, el par no tiene nada que decidir
    donde = ["""NOT EXISTS (SELECT 1 FROM files f
                            WHERE f.id IN (duplicados.id_descartar, duplicados.id_conservar)
                              AND f.borrado=1)""",
             # Y si alguna de las dos ya no esta en files, tampoco: el archivo se
             # borro de verdad y el par quedo sin nada que decidir. Sin esto la
             # cola mostraba 59.839 pares fantasma (46.579 de un CSV viejo con
             # id NULL, el resto descartes ya borrados de otro par).
             """id_descartar IN (SELECT id FROM files)
                AND id_conservar IN (SELECT id FROM files)"""]
    args_sql = []
    if tipo != "todos":
        donde.append("tipo=?"); args_sql.append(tipo)
    if dec != "todas":
        donde.append("decision=?"); args_sql.append(dec)
    if score_max == "sin":
        # Los EXACTO tambien tienen score -1 (no hay pHash que comparar), pero
        # NO son "sin verificar": el SHA256 identico ya los confirma. Meterlos
        # aca llenaba el filtro con 133 mil pares que no hay que mirar.
        donde.append("score < 0 AND tipo <> 'EXACTO'")
    elif score_max != "todos":
        # el `score >= 0` es lo que importa: SIN_SCORE (-1) significa "no se
        # pudo comparar", y sin esto pasaba por debajo de todos los umbrales
        # como si fuera el par mas identico de todos.
        donde.append("score >= 0 AND score <= ?"); args_sql.append(int(score_max))
    w = "WHERE " + " AND ".join(donde)

    n = c.execute(f"SELECT COUNT(*) n FROM duplicados {w}", args_sql).fetchone()["n"]
    filas = [dict(r) for r in c.execute(
        f"SELECT * FROM duplicados {w} ORDER BY score ASC, tipo, id LIMIT ? OFFSET ?",
        (*args_sql, 40, (pag - 1) * 40))]
    # EXACTO ya está resuelto por script (mismos bytes = mismo formato/edición,
    # no aporta nada mostrarlo); para el resto sí importa para decidir a mano.
    # Un solo SELECT para el software de toda la página, en vez de abrir los
    # archivos uno por uno.
    ids_pag = [i for f in filas
               for i in (f["id_conservar"], f["id_descartar"]) if i]
    soft = {}
    if ids_pag:
        qs = ",".join("?" * len(ids_pag))
        soft = {r["id"]: r["software"] for r in
                c.execute(f"SELECT id, software FROM files WHERE id IN ({qs})", ids_pag)}
    for f in filas:
        if f["tipo"] != "EXACTO":
            f["info_conservar"] = info_archivo(f["ruta_conservar"],
                                               soft.get(f["id_conservar"]))
            f["info_descartar"] = info_archivo(f["ruta_descartar"],
                                               soft.get(f["id_descartar"]))

    resumen = c.execute("""SELECT tipo, decision, COUNT(*) n, ROUND(SUM(mb),1) mb
                           FROM duplicados GROUP BY tipo, decision""").fetchall()
    porb = {}
    for r in resumen:
        d = porb.setdefault(r["tipo"], {"n": 0, "mb": 0.0, "pendiente": 0, "descartar": 0})
        d["n"] += r["n"]; d["mb"] += r["mb"] or 0
        if r["decision"] in d:
            d[r["decision"]] += r["n"]
    ahorro = c.execute("""SELECT ROUND(SUM(mb),1) mb, COUNT(*) n FROM duplicados
                          WHERE decision='descartar'""").fetchone()
    # Los tipos salen de la tabla y no de una lista fija: si un chequeo saca
    # pares de EXACTO a otro tipo, tienen que poder filtrarse igual.
    tipos = [r["tipo"] for r in c.execute(
        "SELECT DISTINCT tipo FROM duplicados WHERE tipo IS NOT NULL ORDER BY tipo")]
    return render_template("duplicados.html", filas=filas, n=n, pag=pag,
                           paginas=max(1, -(-n // 40)), tipo=tipo, dec=dec,
                           porb=porb, ahorro=ahorro, cuarentena=str(CUARENTENA),
                           score_max=score_max, tipos=tipos)


def _par(a, b):
    """Clave de un par de duplicados, sin importar cual quedo como conservar."""
    return tuple(sorted((a.lower(), b.lower())))


def importar_csv(c):
    """Carga duplicados.csv sin pisar lo ya decidido. Devuelve cuantos agrego.

    Solo inserta los pares que no estan en la tabla. Sin esto, reimportar un CSV
    nuevo mete de vuelta cada par ya resuelto como 'pendiente' y entierra las
    decisiones viejas debajo de filas repetidas.
    """
    import csv as _csv
    ids = {r["file_path"].lower(): r["id"]
           for r in c.execute("SELECT id, file_path FROM files")}
    ya = {_par(r["ruta_conservar"] or "", r["ruta_descartar"] or "")
          for r in c.execute("SELECT ruta_conservar, ruta_descartar FROM duplicados")}
    malos = _phash_degenerados(c)
    filas = []
    with open(CSV_DUPLICADOS, encoding="utf-8-sig", newline="") as fh:
        for r in _csv.DictReader(fh):
            cons, desc = r.get("conservar", ""), r.get("descartar", "")
            if _par(cons, desc) in ya:
                continue
            ya.add(_par(cons, desc))  # el propio CSV puede repetir un par
            ic, idd = ids.get(cons.lower()), ids.get(desc.lower())
            # Un CSV viejo trae pares de fotos que ya no estan en la base. Sin
            # esto entraban con id NULL: el curador los mostraba como pendientes
            # y /duplicados/grupos reventaba al ordenarlos. Fueron 65.828 pares
            # de los _edited borrados el 2026-08-03, resucitados por un CSV de
            # esa misma manana.
            if ic is None or idd is None:
                continue
            filas.append((r.get("tipo"), r.get("razon"),
                          float(r.get("mb_descartado") or 0), cons, desc,
                          ic, idd,
                          # el CSV ya trae los dos phash: el score sale de ahi,
                          # sin volver a leer los archivos del disco.
                          _score(r.get("phash_conservar"),
                                 r.get("phash_descartar"), malos)))
    c.executemany("""INSERT INTO duplicados
                     (tipo, razon, mb, ruta_conservar, ruta_descartar,
                      id_conservar, id_descartar, score) VALUES (?,?,?,?,?,?,?,?)""", filas)
    c.commit()
    return len(filas)


@app.route("/api/duplicado/decidir", methods=["POST"])
def decidir():
    d = request.get_json(force=True)
    dec = d.get("decision", "pendiente")
    c = con()
    if "todos_score_max" in d:
        smax = str(d["todos_score_max"])
        # 'sin' (los que no se pudieron verificar) no se marca en masa a proposito:
        # son justo los que hay que mirar de a uno.
        if smax != "todos" and not smax.isdigit():
            return jsonify(ok=False, error="ese filtro no se puede marcar en masa"), 400

        def _w(c):
            args = [dec]
            where = ["decision='pendiente'"]
            if d.get("tipo") and d["tipo"] != "todos":
                where.append("tipo=?")
                args.append(d["tipo"])
            if smax != "todos":
                # mismo criterio que el listado: sin `score >= 0` esto marcaba
                # de una todos los pares que nunca se pudieron verificar.
                where.append("score >= 0 AND score <= ?")
                args.append(int(smax))
            # No marcar los que ya están borrados o descartados
            where.append("""NOT EXISTS (SELECT 1 FROM files f
                                        WHERE f.id IN (duplicados.id_descartar, duplicados.id_conservar)
                                          AND f.borrado=1)""")
            c.execute(f"UPDATE duplicados SET decision=? WHERE {' AND '.join(where)}", args)
        _db_write(c, _w)
    elif d.get("todos_del_tipo"):
        def _w(c):
            c.execute("UPDATE duplicados SET decision=? WHERE tipo=? AND decision='pendiente'",
                      (dec, d["todos_del_tipo"]))
        _db_write(c, _w)
    else:
        ids = [int(i) for i in d.get("ids", [])]
        if not ids:
            return jsonify(ok=False, error="nada seleccionado"), 400
        def _w(c):
            c.executemany("UPDATE duplicados SET decision=? WHERE id=?",
                          [(dec, i) for i in ids])
        _db_write(c, _w)
    n = c.execute("SELECT COUNT(*) n FROM duplicados WHERE decision='descartar'").fetchone()["n"]
    return jsonify(ok=True, marcados=n)


@app.route("/api/duplicado/invertir", methods=["POST"])
def invertir_duplicado():
    """Da vuelta cuál lado es 'se conserva' y cuál 'se descarta' de un par.
    Para EXACTO no aplica (son bytes idénticos, no importa cuál)."""
    d = request.get_json(force=True)
    i = int(d["id"])
    c = con()
    def _w(c):
        c.execute("""UPDATE duplicados SET
                       ruta_conservar=ruta_descartar, ruta_descartar=ruta_conservar,
                       id_conservar=id_descartar, id_descartar=id_conservar
                     WHERE id=?""", (i,))
    _db_write(c, _w)
    return jsonify(ok=True)


@app.route("/duplicados/corruptos")
def duplicados_corruptos():
    """Pares donde los dos lados dieron truncados/corruptos al verificar el
    marcador de fin de archivo: ninguno es automaticamente 'el bueno', asi
    que se revisan a mano en vez de intentar adivinar."""
    c = con()
    pag = max(1, int(request.args.get("p", 1)))
    donde = """ambos_corruptos=1 AND decision='pendiente'
                AND NOT EXISTS (SELECT 1 FROM files f
                                WHERE f.id IN (duplicados.id_descartar, duplicados.id_conservar)
                                  AND f.borrado=1)"""
    n = c.execute(f"SELECT COUNT(*) n FROM duplicados WHERE {donde}").fetchone()["n"]
    filas = [dict(r) for r in c.execute(
        f"SELECT * FROM duplicados WHERE {donde} ORDER BY id LIMIT ? OFFSET ?",
        (40, (pag - 1) * 40))]
    return render_template("duplicados_corruptos.html", filas=filas, n=n, pag=pag,
                           paginas=max(1, -(-n // 40)))


@app.route("/api/duplicado/elegir", methods=["POST"])
def elegir_duplicado():
    """Para un par donde ambos lados estan corruptos: el usuario elige a mano
    cual de los dos conservar (el otro queda marcado para el basurero)."""
    d = request.get_json(force=True)
    i = int(d["id"])
    lado = d.get("lado")  # 'conservar' o 'descartar' (segun estan mostrados hoy)
    if lado not in ("conservar", "descartar"):
        return jsonify(ok=False, error="lado invalido"), 400
    c = con()
    def _w(c):
        if lado == "descartar":
            c.execute("""UPDATE duplicados SET
                           ruta_conservar=ruta_descartar, ruta_descartar=ruta_conservar,
                           id_conservar=id_descartar, id_descartar=id_conservar
                         WHERE id=?""", (i,))
        c.execute("UPDATE duplicados SET decision='descartar' WHERE id=?", (i,))
    _db_write(c, _w)
    return jsonify(ok=True)


_ESTADO_APLICAR = {"corriendo": False, "total": 0, "hechos": 0, "movidos": 0,
                   "fallos": 0, "frenados": 0}


def _frenar_sin_copia_viva(conn):
    """Devuelve a 'pendiente' los pares que dejarían la foto sin ninguna copia.

    El chequeo existía como script suelto (verificar_copia_mejor.py) y había que
    acordarse de correrlo antes de apretar el botón. Corriéndolo acá adentro no
    hay forma de saltearlo: de los 11.096 marcados en agosto 2026, 642 habrían
    borrado la única copia sana.
    """
    from verificar_copia_mejor import verificar
    malos = [p["id"] for p in verificar(conn) if p["decision"] == "descartar"]
    if malos:
        conn.executemany("UPDATE duplicados SET decision='pendiente' WHERE id=?",
                         [(i,) for i in malos])
        conn.commit()
    return len(malos)


def _sacar_de_circulacion(conn, photo_id):
    """La foto que se fue a cuarentena sale del evento y queda en el basurero.

    Sin el borrado=1 el archivo seguía apareciendo en 'sin evento' y en la
    próxima corrida de la Fase 5, y sobre todo no había manera de terminar el
    trabajo: la cuarentena se vaciaba a mano desde el explorador. Ahora se
    borra de verdad desde /basurero, que es reversible hasta ese último paso.
    """
    conn.execute("DELETE FROM event_photos WHERE photo_id=?", (photo_id,))
    conn.execute(f"UPDATE files SET borrado=1 WHERE id=? AND {SQL_NO_ES_CONSERVAR_PENDIENTE}",
                 (photo_id,))


def _aplicar_cuarentena_bg():
    """Corre en background: commits incrementales cada 25 archivos (no todo o
    nada al final) para que matar el proceso a mitad de camino no deje la
    base y el disco en estados distintos, y para poder mostrar progreso real."""
    import shutil
    conn = sqlite3.connect(DB, timeout=180)
    conn.row_factory = sqlite3.Row
    try:
        frenados = _frenar_sin_copia_viva(conn)
        # ruta_descartar en duplicados es una copia cacheada de cuando se
        # detecto el par: si el archivo ya se movio desde entonces (otra
        # corrida, otra herramienta) esa ruta queda vieja. files.file_path
        # es la que manda siempre.
        filas = conn.execute("""SELECT d.id, f.file_path as ruta, d.id_descartar
                                FROM duplicados d JOIN files f ON f.id = d.id_descartar
                                WHERE d.decision='descartar'""").fetchall()
        _ESTADO_APLICAR.update(corriendo=True, total=len(filas), hechos=0,
                               movidos=0, fallos=0, frenados=frenados)
        for i, f in enumerate(filas, 1):
            src = f["ruta"]
            try:
                if str(Path(src)).lower().startswith(str(CUARENTENA).lower()) and os.path.exists(src):
                    # ya estaba en cuarentena de una corrida anterior que no
                    # llego a actualizar decision: no hay nada que mover, pero
                    # si hay que sacarlo del evento y del archivo igual.
                    _sacar_de_circulacion(conn, f["id_descartar"])
                    conn.execute("UPDATE duplicados SET decision='movido', ruta_descartar=? WHERE id=?",
                                 (src, f["id"]))
                    _ESTADO_APLICAR["movidos"] += 1
                elif src and os.path.exists(src):
                    rel = Path(src).as_posix().replace(":", "", 1).lstrip("/")
                    dest = CUARENTENA / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(src, str(dest))
                    conn.execute("UPDATE files SET file_path=? WHERE id=?",
                                 (str(dest), f["id_descartar"]))
                    _sacar_de_circulacion(conn, f["id_descartar"])
                    conn.execute("UPDATE duplicados SET decision='movido', ruta_descartar=? WHERE id=?",
                                 (str(dest), f["id"]))
                    _ESTADO_APLICAR["movidos"] += 1
                else:
                    # no existe en ningun lado: ya fue resuelto por otra via
                    # (basurero, limpieza manual, etc), no queda nada pendiente
                    conn.execute("UPDATE duplicados SET decision='movido' WHERE id=?", (f["id"],))
            except Exception:
                _ESTADO_APLICAR["fallos"] += 1
            _ESTADO_APLICAR["hechos"] = i
            if i % 25 == 0:
                conn.commit()
        conn.commit()
    finally:
        conn.close()
        _ESTADO_APLICAR["corriendo"] = False


@app.route("/duplicados/aplicar", methods=["POST"])
def aplicar_duplicados():
    """Mueve a cuarentena lo marcado. NO borra: se puede revertir a mano."""
    if not _ESTADO_APLICAR["corriendo"]:
        threading.Thread(target=_aplicar_cuarentena_bg, daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/duplicados/aplicar/estado")
def estado_aplicar():
    return jsonify(**_ESTADO_APLICAR)


def _carpeta_de_evento(eid, ev):
    """`123-liga-uandes-fecha-9`. El id adelante para que sea unico aunque dos
    eventos se llamen igual, y el nombre atras para poder encontrarla a ojo."""
    crudo = (ev["name"] or ev["suggested_name"] or "").strip()
    limpio = re.sub(r"[^A-Za-z0-9]+", "-", crudo).strip("-").lower()[:50]
    return f"{eid}-{limpio}" if limpio else str(eid)


@app.route("/evento/<int:eid>/subir", methods=["POST"])
def subir_a_evento(eid):
    """Una foto por request, y queda en ESTE evento.

    De a una porque Cloudflare corta cualquier pedido de mas de 100 MB: una
    fecha entera en un solo POST rebota siempre.

    Si la foto ya existe en el archivo (mismo sha256) no se duplica: se borra la
    copia recien subida y se cuelga del evento la que ya estaba.
    """
    from PIL import Image
    from utils_images import compute_sha256, read_exif_info, is_image_file
    from db import upsert_file_nocommit

    c = con()
    ev = c.execute("SELECT id, name, suggested_name FROM events WHERE id=?",
                   (eid,)).fetchone()
    if ev is None:
        abort(404)

    f = request.files.get("archivo")
    if f is None or not f.filename:
        return jsonify(error="no llego ningun archivo"), 400
    nombre = secure_filename(f.filename)
    if not nombre or not is_image_file(nombre):
        return jsonify(error=f"{f.filename}: formato no aceptado"), 400

    carpeta = SUBIDAS_EVENTO / _carpeta_de_evento(eid, ev)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / nombre
    n = 2
    while destino.exists():  # no pisar lo que ya subiste
        destino = carpeta / f"{Path(nombre).stem}-{n}{Path(nombre).suffix}"
        n += 1
    f.save(destino)

    # Que se llame .jpg no lo hace un jpg: si no abre, no se queda en el disco.
    # Los RAW van por rawpy: PIL no los conoce y rechazaba todos los .ARW.
    # Basta con leer el thumb embebido, que no decodifica el mosaico entero.
    try:
        if destino.suffix.lower() in RAW_EXT:
            import rawpy
            with rawpy.imread(str(destino)) as r:
                r.extract_thumb()
        else:
            with Image.open(destino) as im:
                im.verify()
    except Exception as e:
        destino.unlink(missing_ok=True)
        return jsonify(error=f"{f.filename}: no se pudo leer ({type(e).__name__})"), 400

    sha = compute_sha256(str(destino))
    ya = c.execute("SELECT id, file_path FROM files WHERE file_hash_sha256=?",
                   (sha,)).fetchone()
    if ya:
        destino.unlink(missing_ok=True)
        pid, repetida = ya["id"], ya["file_path"]
        # el temporal ya no existe: se informa la que quedo, no la que se borro
        destino = Path(ya["file_path"])
    else:
        fecha, camara = read_exif_info(str(destino))
        cur = c.cursor()
        # needs_analysis=1: cuando corras el analisis le va a poner etiquetas
        upsert_file_nocommit(cur, str(destino), fecha, camara, sha)
        pid = c.execute("SELECT id FROM files WHERE file_path=?",
                        (str(destino),)).fetchone()["id"]
        repetida = None

    c.execute("INSERT OR IGNORE INTO event_photos (event_id, photo_id) VALUES (?,?)",
              (eid, pid))
    c.commit()
    return jsonify(ok=True, id=pid, nombre=Path(destino).name, repetida=repetida)


@app.route("/evento/<int:eid>/borrar", methods=["POST"])
def borrar_evento(eid):
    c = con()
    # Los sub-eventos suben un nivel. Si se quedaran apuntando al padre borrado
    # desaparecerían de la portada (que solo lista los que no tienen padre) y
    # solo se llegaría a ellos buscándolos por nombre.
    abuelo = c.execute("SELECT parent_id FROM events WHERE id=?", (eid,)).fetchone()
    c.execute("UPDATE events SET parent_id=? WHERE parent_id=?",
              (abuelo["parent_id"] if abuelo else None, eid))
    c.execute("DELETE FROM event_photos WHERE event_id=?", (eid,))
    c.execute("DELETE FROM events WHERE id=?", (eid,))
    if abuelo and abuelo["parent_id"]:
        _fechas_desde_hijos(c, abuelo["parent_id"])
        _subir_fechas(c, abuelo["parent_id"])
    c.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--db", default=DB)
    # Por defecto sigue en loopback: el curador no tiene login y muestra el
    # archivo entero. Solo se abre a la interfaz de Tailscale cuando se pide a
    # mano, y ahi solo lo ven los dispositivos de la tailnet.
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 (default), 0.0.0.0 para verlo del telefono, "
                         "o la IP de Tailscale")
    ap.add_argument("--token", help="clave de acceso; si se abre a la red y no "
                                    "se da ninguna, se genera una sola vez")
    ap.add_argument("--rehacer-tags", action="store_true",
                    help="reconstruye tag_foto y sale. Hace falta despues de un "
                         "reanalisis que reescriba scene_tags de fotos ya indexadas")
    args = ap.parse_args()
    DB = args.db

    if args.rehacer_tags:
        c = sqlite3.connect(DB, timeout=30)
        c.row_factory = sqlite3.Row
        asegurar_tag_foto(c, forzar=True)
        c.close()
        sys.exit(0)
    # Salir de loopback sin token dejaria el archivo entero abierto al wifi.
    if args.host not in ("127.0.0.1", "localhost"):
        TOKEN = args.token or secrets.token_urlsafe(9)
    elif args.token:
        TOKEN = args.token
    migrar()
    # Las vistas `tema:*` salen de la tabla, que puede haber cambiado desde la
    # ultima corrida. Necesita contexto de app porque consulta con con().
    with app.app_context():
        refrescar_vistas_tema()
    # No bloquea el arranque: ordenar por peso se va habilitando solo.
    threading.Thread(target=rellenar_metadatos, daemon=True).start()

    print(f"\n  Curador en  http://{args.host}:{args.port}")
    if TOKEN:
        for ip in _mis_ips() if args.host == "0.0.0.0" else [args.host]:
            print(f"    desde otro equipo:  http://{ip}:{args.port}/?t={TOKEN}")
        print("\n  Ese link entra una vez y deja una cookie por 30 dias.")
    print()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
