#!/usr/bin/env python3
"""Recorridos del auto — corre en la Pi, detras de Caddy.

El ESP32 del auto (GPS + MicroSD) manda sus filas nuevas cuando tiene
internet, y esta app las guarda y las muestra como viajes.

  - POST /auto/ingesta   publica, la cuida un token (Caddy: handle)
  - /panel/auto/...      la pagina, detras de Cloudflare Access y basic_auth
                         (Caddy: handle_path, que le saca el prefijo). Por eso
                         los links y estaticos son RELATIVOS: la app no sabe
                         que vive bajo /panel/auto.

En la base van solo los datos crudos; viajes, desnivel, detenciones y
frenadas se calculan al mostrar.

Uso:
    python3 auto.py servir     # el servicio (systemctl --user ... auto)
    python3 auto.py test       # self-check
"""
import os
import sys
import hmac
import json
import math
import sqlite3
import secrets
import tempfile
import threading
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import (Flask, g, request, abort, jsonify, render_template_string,
                   send_from_directory)

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("AUTO_DB", BASE / "auto.db"))
TOKEN = Path(os.environ.get("AUTO_TOKEN", BASE / ".token"))
ESTATICO = BASE / "estatico"
TZ = ZoneInfo("America/Santiago")
FMT = "%Y-%m-%d %H:%M:%S"

MAX_BODY = 64 * 1024          # contrato con el firmware: 413 si pasa de esto
HUECO_VIAJE = 300             # s sin puntos = corte (como HUECO_VISITA)
# Perillas de calibracion, no constantes sagradas: el GPS real no es el ideal.
EN_MOVIMIENTO = float(os.environ.get("AUTO_EN_MOVIMIENTO", 3.0))   # km/h
HDOP_MAX = float(os.environ.get("AUTO_HDOP_MAX", 5.0))       # peor = mala señal
ACEL_FUERTE = float(os.environ.get("AUTO_ACEL_FUERTE", 2.5))  # m/s²
ACEL_DT_MAX = 1.5             # s; con mas hueco entre fixes no es aceleracion
DETENCION_MIN = 3.0           # s quieto para contar una detencion
# La altura del GPS baila un par de metros: sumar cada diferencia a 2 Hz
# inventa cientos de metros de subida. Solo cuenta cuando se movio esto.
DESNIVEL_UMBRAL = float(os.environ.get("AUTO_DESNIVEL_UMBRAL", 3.0))  # m
# Colores por velocidad: (desde km/h, color). La leyenda sale de aca.
TRAMOS = ((0, "#3b82f6"), (20, "#10b981"), (50, "#eab308"),
          (80, "#f97316"), (110, "#dc2626"))
GRIS = "#9ca3af"

# Las 13 columnas que siguen a ts_utc, en el orden del CSV del firmware:
# (nombre, tipo, minimo, maximo).
CAMPOS = (("lat", float, -90, 90), ("lon", float, -180, 180),
          ("alt_m", float, -500, 9000), ("kmh", float, 0, 300),
          ("rumbo", float, 0, 360), ("sats", int, 0, 50),
          ("sats_vistos", int, 0, 100), ("hdop", float, 0, 99.99),
          ("pdop", float, 0, 99.99), ("vdop", float, 0, 99.99),
          ("snr_prom", float, 0, 99), ("fix", int, 0, 8), ("viaje", int, 0, 1))
COLUMNAS = ("ts_utc",) + tuple(c[0] for c in CAMPOS)
INSERTAR = "INSERT OR IGNORE INTO posicion (%s) VALUES (%s)" % (
    ",".join(COLUMNAS), ",".join("?" * len(COLUMNAS)))

# Futuro OBD-II: otra tabla (obd, ts_utc TEXT PRIMARY KEY, rpm, maf...) con
# su propia ruta de ingesta; se cruza con posicion por ts_utc, que por eso se
# guarda SIEMPRE normalizado a centesimas ("...:32.50"). Nada de aca hay que
# cambiarlo para eso, solo agregar.
#
# La tabla nacio con 5 columnas; migrar() le agrega las nuevas. Una base nueva
# pasa por el mismo camino, asi el test ejercita la migracion de verdad.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS posicion (
    ts_utc  TEXT PRIMARY KEY,     -- 'YYYY-MM-DD HH:MM:SS.cc', siempre UTC
    lat     REAL NOT NULL,
    lon     REAL NOT NULL,
    kmh     REAL NOT NULL,
    sats    INTEGER NOT NULL
);
"""
NUEVAS = (("alt_m", "REAL"), ("rumbo", "REAL"), ("sats_vistos", "INTEGER"),
          ("hdop", "REAL"), ("pdop", "REAL"), ("vdop", "REAL"),
          ("snr_prom", "REAL"), ("fix", "INTEGER"), ("viaje", "INTEGER"))

# t en segundos desde 1970, calculado por SQLite (strptime por fila en Python
# era lo mas caro de recorrer la tabla). 'subsec' es exacto al milisegundo;
# julianday() pierde ~50 us a esta escala y las cuentas a 2 Hz lo notan.
SELECCION = ("SELECT ts_utc, unixepoch(ts_utc, 'subsec') AS t, "
             "lat, lon, kmh, alt_m, hdop, viaje FROM posicion")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY


def conectar():
    cx = sqlite3.connect(DB, timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode = WAL")
    cx.execute("PRAGMA busy_timeout = 30000")
    cx.executescript(ESQUEMA)
    return cx


def migrar():
    """Agrega las columnas que falten. Se corre una vez al arrancar, no por
    request: dos hilos haciendo el mismo ALTER chocan."""
    cx = conectar()
    hay = {r["name"] for r in cx.execute("PRAGMA table_info(posicion)")}
    with cx:
        for nombre, tipo in NUEVAS:
            if nombre not in hay:
                cx.execute("ALTER TABLE posicion ADD COLUMN %s %s" % (nombre, tipo))
    cx.close()


def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


@app.teardown_appcontext
def cerrar(_):
    cx = g.pop("db", None)
    if cx is not None:
        cx.close()


def crear_token():
    """Una sola vez: O_EXCL para no pisar uno que ya este en el firmware."""
    try:
        fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as fh:
        fh.write(secrets.token_urlsafe(32))


def token_ok(cabecera):
    # se lee en cada request: rotar el token no obliga a reiniciar
    try:
        esperado = TOKEN.read_text().strip()
    except OSError:
        return False
    if not esperado or not cabecera.startswith("Bearer "):
        return False
    return hmac.compare_digest(cabecera[7:].strip().encode(), esperado.encode())


# ---------------------------------------------------------------- ingesta

def leer_ts(s):
    """'...:32.50' o '...:32' -> datetime, o None."""
    for fmt in (FMT + ".%f", FMT):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def clave_ts(ts):
    """La forma unica de la PRIMARY KEY: siempre dos decimales."""
    return ts.strftime(FMT) + ".%02d" % (ts.microsecond // 10000)


def parsear(linea, ahora):
    """Una linea del CSV -> tupla en el orden de COLUMNAS, o None."""
    partes = linea.split(",")
    if len(partes) != 1 + len(CAMPOS):
        return None
    ts = leer_ts(partes[0].strip())
    if ts is None or ts > ahora + timedelta(days=1):
        return None
    fila = [clave_ts(ts)]
    for texto, (_nombre, tipo, minimo, maximo) in zip(partes[1:], CAMPOS):
        try:
            v = tipo(texto)
        except ValueError:
            return None
        # NaN no pasa ninguna comparacion, asi que cae solo
        if not minimo <= v <= maximo:
            return None
        fila.append(v)
    return tuple(fila)


def ingerir(cuerpo, ahora=None):
    ahora = ahora or datetime.now(timezone.utc).replace(tzinfo=None)
    validas, invalidas = [], 0
    for linea in cuerpo.splitlines():
        linea = linea.strip()
        if not linea:
            continue                    # el \n final no es una linea mala
        fila = parsear(linea, ahora)
        if fila is None:
            invalidas += 1
        else:
            validas.append(fila)
    cx = db()
    aceptadas = 0
    with cx:
        for fila in validas:
            aceptadas += cx.execute(INSERTAR, fila).rowcount
    # ultimo_ts: la fila mas nueva de ESTE lote que ya quedo en la base
    # (aceptada o duplicada). Es lo que el firmware puede dar por guardado.
    return {"aceptadas": aceptadas, "duplicadas": len(validas) - aceptadas,
            "invalidas": invalidas,
            "ultimo_ts": max((f[0] for f in validas), default=None)}


@app.post("/auto/ingesta")
def ingesta():
    # el token antes que el cuerpo: sin token no se lee nada
    if not token_ok(request.headers.get("Authorization", "")):
        return jsonify(error="token invalido"), 401
    cuerpo = request.get_data(cache=False).decode("utf-8", errors="replace")
    return jsonify(ingerir(cuerpo))


@app.errorhandler(413)
def muy_grande(_):
    return jsonify(error="maximo %d bytes por request" % MAX_BODY), 413


# ---------------------------------------------------------------- viajes

def haversine(lat1, lon1, lat2, lon2):
    """Metros entre dos puntos."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def en_viaje(p):
    return p["viaje"] == 1          # NULL (filas de 5 columnas) = estacionado


def bueno(p):
    return p["hdop"] is None or p["hdop"] <= HDOP_MAX


def partir(filas):
    """Filas ordenadas -> tramos: rachas del mismo `viaje`, cortadas tambien
    por un hueco de mas de HUECO_VIAJE (corte de energia)."""
    tramo, prev = [], None
    for f in filas:
        if tramo and (en_viaje(f) != en_viaje(prev) or f["t"] - prev["t"] > HUECO_VIAJE):
            yield tramo
            tramo = []
        tramo.append(f)
        prev = f
    if tramo:
        yield tramo


def resumir(tramo):
    """Lo que va en la lista. Los puntos con mala señal no entran al calculo."""
    ok = [p for p in tramo if bueno(p)]
    r = {"tipo": "viaje" if en_viaje(tramo[0]) else "estacionado",
         "inicio": tramo[0]["ts_utc"], "fin": tramo[-1]["ts_utc"],
         "segundos": tramo[-1]["t"] - tramo[0]["t"],
         "puntos": len(tramo), "malos": len(tramo) - len(ok)}
    if r["tipo"] == "estacionado":
        donde = ok or tramo             # sin ningun punto bueno, mejor que nada
        r["lat"] = statistics.median(p["lat"] for p in donde)
        r["lon"] = statistics.median(p["lon"] for p in donde)
        return r
    dist = 0.0
    for a, b in zip(ok, ok[1:]):
        # el tramo cuenta salvo que el auto este quieto en las dos puntas
        if a["kmh"] >= EN_MOVIMIENTO or b["kmh"] >= EN_MOVIMIENTO:
            dist += haversine(a["lat"], a["lon"], b["lat"], b["lon"])
    moviendo = [p["kmh"] for p in ok if p["kmh"] >= EN_MOVIMIENTO]
    r.update(km=dist / 1000, max=max((p["kmh"] for p in ok), default=0.0),
             # ponytail: promedio de muestras, no ponderado por tiempo; a 2 Hz
             # fijos en viaje es lo mismo
             prom=sum(moviendo) / len(moviendo) if moviendo else 0.0)
    return r


def desnivel(alturas):
    """(subida, bajada) en m, con histeresis de DESNIVEL_UMBRAL."""
    sube = baja = 0.0
    ref = None
    for a in alturas:
        if a is None:
            continue
        if ref is None:
            ref = a
        elif abs(a - ref) >= DESNIVEL_UMBRAL:
            if a > ref:
                sube += a - ref
            else:
                baja += ref - a
            ref = a
    return sube, baja


def detenciones(ok):
    """(segundos quieto, cuantas detenciones). La racha quieta del arranque
    y la del final suman tiempo pero no cuentan como detencion."""
    total, n, ini = 0.0, 0, None
    for i, p in enumerate(ok):
        quieto = p["kmh"] < EN_MOVIMIENTO
        if quieto and ini is None:
            ini = i
        elif not quieto and ini is not None:
            dur = p["t"] - ok[ini]["t"]
            total += dur
            if ini > 0 and dur >= DETENCION_MIN:
                n += 1
            ini = None
    if ini is not None:
        total += ok[-1]["t"] - ok[ini]["t"]
    return total, n


def eventos(ok):
    """Aceleraciones y frenadas fuertes. Varios fixes seguidos sobre el umbral
    son UN evento; se marca donde fue mas fuerte. -> [(lat, lon, m/s², t)]"""
    lista, actual = [], None
    for a, b in zip(ok, ok[1:]):
        dt = b["t"] - a["t"]
        acc = (b["kmh"] - a["kmh"]) / 3.6 / dt if 0 < dt <= ACEL_DT_MAX else 0.0
        if abs(acc) <= ACEL_FUERTE:
            actual = None
            continue
        if actual and (actual["acc"] > 0) == (acc > 0):
            if abs(acc) > abs(actual["acc"]):
                actual.update(acc=acc, p=b)
            continue
        actual = {"acc": acc, "p": b}
        lista.append(actual)
    return [(e["p"]["lat"], e["p"]["lon"], round(e["acc"], 2), e["p"]["t"]) for e in lista]


def analizar(tramo):
    """Lo extra del detalle de un viaje."""
    ok = [p for p in tramo if bueno(p)]
    sube, baja = desnivel(p["alt_m"] for p in ok)
    quieto, paradas = detenciones(ok)
    evs = eventos(ok)
    return {"sube": sube, "baja": baja, "quieto": quieto, "paradas": paradas,
            "eventos": evs, "acel": sum(e[2] > 0 for e in evs),
            "fren": sum(e[2] < 0 for e in evs)}


_TRAMOS = {"corte": None, "antes": 0, "hechos": []}
_CANDADO = threading.Lock()


def tramos():
    """Todos los tramos (viajes y estacionamientos) resumidos, en orden.

    Los datos llegan cada ~30 s mientras se maneja, y a 2 Hz un año son
    millones de filas: no se puede recorrer todo en cada vista. Lo que ya se
    resumio antes del ultimo tramo no cambia mientras no aparezcan filas
    ANTES de el, y eso se ve contando. Entonces solo se relee desde el
    comienzo del ultimo tramo.

    Medido en la Pi con 1M de filas (~4 meses): la primera vuelta 14,5 s (por
    eso servir la precalienta), despues 0,36 s con o sin lote nuevo.

    ponytail: el COUNT(*) del indice es O(n) y un borrado + insert que dejen
    la misma cuenta no se notarian. Si duele, guardar un contador de cambios
    en la base, o materializar los resumenes.
    """
    with _CANDADO:
        cx, c = db(), _TRAMOS
        antes = "SELECT COUNT(*) FROM posicion WHERE ts_utc < ?"
        if c["corte"] is not None and cx.execute(antes, (c["corte"],)).fetchone()[0] == c["antes"]:
            hechos = c["hechos"]
            filas = cx.execute(SELECCION + " WHERE ts_utc >= ? ORDER BY ts_utc", (c["corte"],))
        else:
            hechos = []
            filas = cx.execute(SELECCION + " ORDER BY ts_utc")
        todos = hechos + [resumir(t) for t in partir(filas)]
        corte = todos[-1]["inicio"] if todos else None
        c.update(corte=corte, hechos=todos[:-1],
                 antes=cx.execute(antes, (corte,)).fetchone()[0] if corte else 0)
        return todos


def puntos_de(inicio):
    """Los puntos del viaje que arranca justo en `inicio`, o []."""
    cx = db()
    pts = next(partir(cx.execute(SELECCION + " WHERE ts_utc >= ? ORDER BY ts_utc",
                                 (inicio,))), [])
    # una fecha cualquiera no es un viaje: tiene que haber un punto de viaje
    # justo ahi, y el anterior tiene que ser de otro tramo
    if not pts or pts[0]["ts_utc"] != inicio or not en_viaje(pts[0]):
        return []
    prev = cx.execute(SELECCION + " WHERE ts_utc < ? ORDER BY ts_utc DESC LIMIT 1",
                      (inicio,)).fetchone()
    if prev and next(partir([prev, pts[0]])) != [prev]:
        return []                       # es la mitad de un viaje
    return pts


# ---------------------------------------------------------------- paginas

def local(ts, fmt="%d/%m/%Y %H:%M"):
    return leer_ts(ts).replace(tzinfo=timezone.utc).astimezone(TZ).strftime(fmt)


def duracion(s):
    if s < 60:
        return "%d s" % round(s)
    h, m = divmod(round(s / 60), 60)
    return "%d h %02d min" % (h, m) if h else "%d min" % m


def dec(x, n=1):
    """Coma decimal, como se escribe en Chile."""
    return ("%.*f" % (n, x)).replace(".", ",")


app.jinja_env.filters.update(local=local, duracion=duracion, dec=dec)

CABEZA = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ titulo }}</title>
<style>
 :root{color-scheme:light dark;--fondo:#f6f6f4;--tarjeta:#fff;--texto:#1c1c1c;--suave:#6b6b6b;--borde:#e3e3df}
 @media (prefers-color-scheme:dark){:root{--fondo:#151515;--tarjeta:#202020;--texto:#ececec;--suave:#a0a0a0;--borde:#333}}
 body{margin:0;background:var(--fondo);color:var(--texto);font:16px/1.4 system-ui,sans-serif}
 main{max-width:900px;margin:0 auto;padding:16px}
 h1{font-size:1.3rem;margin:0 0 12px} h2{font-size:1.05rem;margin:22px 0 10px} a{color:inherit}
 .tarjeta{display:block;text-decoration:none;background:var(--tarjeta);border:1px solid var(--borde);
        border-radius:10px;padding:12px 14px;margin:0 0 10px}
 .tarjeta b{display:block} .datos{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--suave);font-size:.92rem}
 .vacio,.nota{color:var(--suave)} .nota{font-size:.85rem}
 .cifras{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin:0 0 12px}
 .cifras div{background:var(--tarjeta);border:1px solid var(--borde);border-radius:10px;padding:8px 10px}
 .cifras small{display:block;color:var(--suave);font-size:.8rem} .cifras b{font-size:1.05rem}
 #mapa{height:55vh;min-height:300px;border-radius:10px}
 .leyenda{display:flex;flex-wrap:wrap;gap:6px 14px;margin:8px 0 16px;font-size:.88rem}
 .leyenda i{display:inline-block;width:18px;height:6px;border-radius:3px;margin-right:5px;vertical-align:middle}
 .leyenda i.punto{width:10px;height:10px;border-radius:50%}
 .grafico{background:var(--tarjeta);border-radius:10px;padding:6px 0;margin:0 0 12px}
</style>"""

LISTA = CABEZA + """
<main><h1>El auto</h1>
<p class="datos"><a href="#viajes">Viajes ({{ viajes|length }})</a>
 <a href="#estacionado">Estacionado ({{ quieto|length }})</a></p>
<h2 id="viajes">Viajes</h2>
{% for v in viajes %}
<a class="tarjeta" href="viaje/{{ v.inicio|replace(' ', 'T') }}">
 <b>{{ v.inicio|local }} → {{ v.fin|local('%H:%M') if v.inicio|local('%d%m%Y') == v.fin|local('%d%m%Y') else v.fin|local }}</b>
 <span class="datos"><span>{{ v.segundos|duracion }}</span><span>{{ v.km|dec }} km</span>
 <span>máx {{ v.max|round|int }} km/h</span><span>prom {{ v.prom|round|int }} km/h en movimiento</span></span>
</a>
{% else %}
<p class="vacio">Todavía no hay viajes. Cuando el ESP32 tenga internet, aparecen acá.</p>
{% endfor %}
<h2 id="estacionado">Estacionado</h2>
{% for e in quieto %}
<a class="tarjeta" href="https://www.openstreetmap.org/?mlat={{ '%.6f'|format(e.lat) }}&amp;mlon={{ '%.6f'|format(e.lon) }}#map=17/{{ '%.6f'|format(e.lat) }}/{{ '%.6f'|format(e.lon) }}" target="_blank" rel="noopener">
 <b>Desde {{ e.inicio|local }} hasta {{ e.fin|local('%H:%M') if e.inicio|local('%d%m%Y') == e.fin|local('%d%m%Y') else e.fin|local }}</b>
 <span class="datos"><span>{{ e.segundos|duracion }}</span><span>{{ '%.5f'|format(e.lat) }}, {{ '%.5f'|format(e.lon) }} · ver en el mapa</span></span>
</a>
{% else %}
<p class="vacio">Nada todavía.</p>
{% endfor %}
</main>"""

DETALLE = CABEZA + """
<link rel="stylesheet" href="../estatico/leaflet.css">
<link rel="stylesheet" href="../estatico/uPlot.min.css">
<main><p><a href="../">← Todos los viajes</a></p>
<h1>{{ v.inicio|local }} → {{ v.fin|local('%H:%M') }}</h1>
<div class="cifras">
 <div><small>Duración</small><b>{{ v.segundos|duracion }}</b></div>
 <div><small>Distancia</small><b>{{ v.km|dec }} km</b></div>
 <div><small>Velocidad máxima</small><b>{{ v.max|round|int }} km/h</b></div>
 <div><small>Promedio en movimiento</small><b>{{ v.prom|round|int }} km/h</b></div>
 <div><small>Detenido</small><b>{{ a.quieto|duracion }} · {{ a.paradas }} {{ 'detención' if a.paradas == 1 else 'detenciones' }}</b></div>
 <div><small>Aceleraciones / frenadas fuertes</small><b>{{ a.acel }} / {{ a.fren }}</b></div>
 <div><small>Subida / bajada</small><b>{{ a.sube|round|int }} m / {{ a.baja|round|int }} m</b></div>
 <div><small>Puntos</small><b>{{ v.puntos }}</b>{% if v.malos %} <small>{{ v.malos }} con mala señal</small>{% endif %}</div>
</div>
<div id="mapa"></div>
<div class="leyenda">{% for desde, color in colores %}<span><i style="background:{{ color }}"></i>
 {%- if loop.last %}{{ desde }}+ km/h{% else %}{{ desde }}–{{ colores[loop.index][0] }} km/h{% endif %}</span>{% endfor %}
 <span><i style="background:{{ gris }}"></i>mala señal (HDOP &gt; {{ hdop_max|round|int }})</span>
 <span><i class="punto" style="background:#7c3aed"></i>aceleración fuerte</span>
 <span><i class="punto" style="background:#db2777"></i>frenada fuerte</span></div>
<div class="grafico" id="velocidad"></div>
<div class="grafico" id="altura"></div>
<p class="nota">Fuerte = más de {{ acel|dec }} m/s². Los puntos con mala señal se ven en gris y no entran a ningún cálculo.</p>
</main>
<script src="../estatico/leaflet.js"></script>
<script src="../estatico/uPlot.iife.min.js"></script>
<script>
// P: [t, lat, lon, km/h, altura, mala señal]   EV: [lat, lon, m/s², t]
const P = {{ puntos|tojson }}, EV = {{ a.eventos|tojson }}, TRAMOS = {{ colores|tojson }}, GRIS = {{ gris|tojson }};
const color = k => { let c = TRAMOS[0][1]; for (const [d, col] of TRAMOS) if (k >= d) c = col; return c; };
const hora = t => new Date(t * 1e3).toLocaleTimeString("es-CL", {timeZone: "America/Santiago"});

const mapa = L.map("mapa");
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(mapa);
// un polyline por racha del mismo color, no uno por tramo
let racha = [], c0 = null;
const cerrar = () => { if (racha.length > 1) L.polyline(racha, {color: c0, weight: 5}).addTo(mapa); };
for (let i = 1; i < P.length; i++) {
  const c = (P[i][5] || P[i-1][5]) ? GRIS : color(P[i][3]);
  if (c !== c0) { cerrar(); racha = [[P[i-1][1], P[i-1][2]]]; c0 = c; }
  racha.push([P[i][1], P[i][2]]);
}
cerrar();
const punto = (ll, relleno, txt) => L.circleMarker(ll, {radius: 7, color: "#fff", weight: 2,
  fillColor: relleno, fillOpacity: 1}).addTo(mapa).bindTooltip(txt);
punto([P[0][1], P[0][2]], "#16a34a", "Inicio " + hora(P[0][0]));
punto([P.at(-1)[1], P.at(-1)[2]], "#111", "Fin " + hora(P.at(-1)[0]));
for (const [lat, lon, a, t] of EV)
  punto([lat, lon], a > 0 ? "#7c3aed" : "#db2777",
        (a > 0 ? "Aceleración " : "Frenada ") + Math.abs(a).toFixed(1).replace(".", ",") + " m/s² · " + hora(t));
mapa.fitBounds(L.latLngBounds(P.map(p => [p[1], p[2]])), {padding: [20, 20]});

// la mala señal queda como hueco en los graficos, igual que en los calculos
const x = P.map(p => p[0]);
const tinta = getComputedStyle(document.body).color;
function grafico(id, etiqueta, valores, trazo) {
  const el = document.getElementById(id);
  const u = new uPlot({width: el.clientWidth, height: 200, cursor: {sync: {key: "viaje"}},
    tzDate: t => uPlot.tzDate(new Date(t * 1e3), "America/Santiago"),
    series: [{}, {label: etiqueta, stroke: trazo, width: 1.5}],
    axes: [{stroke: tinta}, {stroke: tinta}]}, [x, valores], el);
  addEventListener("resize", () => u.setSize({width: el.clientWidth, height: 200}));
}
grafico("velocidad", "km/h", P.map(p => p[5] ? null : p[3]), "#dc2626");
grafico("altura", "altura (m)", P.map(p => p[5] ? null : p[4]), "#0f766e");
</script>"""


@app.get("/")
def inicio():
    todos = tramos()[::-1]                       # lo mas nuevo arriba
    return render_template_string(
        LISTA, titulo="El auto", viajes=[t for t in todos if t["tipo"] == "viaje"],
        quieto=[t for t in todos if t["tipo"] == "estacionado"])


@app.get("/viaje/<inicio>")
def viaje(inicio):
    inicio = inicio.replace("T", " ")
    if leer_ts(inicio) is None:
        abort(404)
    pts = puntos_de(inicio)
    if not pts:
        abort(404)
    return render_template_string(
        DETALLE, titulo="Viaje " + local(inicio), v=resumir(pts), a=analizar(pts),
        colores=TRAMOS, gris=GRIS, hdop_max=HDOP_MAX, acel=ACEL_FUERTE,
        puntos=[[round(p["t"], 2), p["lat"], p["lon"], p["kmh"], p["alt_m"],
                 0 if bueno(p) else 1] for p in pts])


@app.get("/estatico/<path:nombre>")
def estatico(nombre):
    return send_from_directory(ESTATICO, nombre, max_age=86400)


# ---------------------------------------------------------------- test

def linea(ts, lat=-33.4067901, lon=-70.5063931, alt=612.4, kmh=37.25, rumbo=184.3,
          sats=9, vistos=14, hdop=0.9, pdop=1.6, vdop=1.3, snr=35.8, fix=1, viaje=1):
    return ",".join(str(v) for v in (ts, lat, lon, alt, kmh, rumbo, sats, vistos,
                                     hdop, pdop, vdop, snr, fix, viaje))


def test():
    global DB, TOKEN
    tmp = Path(tempfile.mkdtemp())
    DB, TOKEN = tmp / "auto.db", tmp / ".token"

    # la base vieja, de 5 columnas, con una fila: migrar() no la pierde
    cx = conectar()
    with cx:
        cx.execute("INSERT INTO posicion VALUES ('2026-10-08 11:29:32',-33.4,-70.5,0.0,9)")
    cx.close()
    migrar()
    migrar()                                     # dos veces no rompe
    cx = conectar()
    cols = [r["name"] for r in cx.execute("PRAGMA table_info(posicion)")]
    assert cols == ["ts_utc", "lat", "lon", "kmh", "sats"] + [n for n, _ in NUEVAS], cols
    vieja = cx.execute("SELECT * FROM posicion").fetchall()
    assert len(vieja) == 1 and vieja[0]["viaje"] is None
    with cx:
        cx.execute("DELETE FROM posicion")
    cx.close()
    print("  migracion: 5 -> 14 columnas, la fila vieja sigue (viaje NULL)")

    crear_token()
    assert oct(TOKEN.stat().st_mode & 0o777) == "0o600", "el token no quedo en 600"
    tok = TOKEN.read_text()
    crear_token()
    assert TOKEN.read_text() == tok, "crear_token piso el token existente"

    c = app.test_client()
    bien = {"Authorization": "Bearer " + tok, "Content-Type": "text/csv"}
    una = linea("2026-10-08 11:29:32.50") + "\n"

    assert c.post("/auto/ingesta", data=una).status_code == 401, "sin token"
    for malo in ("Bearer x", "Bearer " + tok + "x", tok, "Basic " + tok, "Bearer ñ"):
        r = c.post("/auto/ingesta", data=una, headers={"Authorization": malo})
        assert r.status_code == 401, "token malo paso: %r" % malo
    TOKEN.write_text("")
    r = c.post("/auto/ingesta", data=una, headers={"Authorization": "Bearer "})
    assert r.status_code == 401, "token vacio en disco deja pasar todo"
    TOKEN.write_text(tok)
    print("  401 con token malo, ausente o vacio")

    r = c.post("/auto/ingesta", data=b"x" * (MAX_BODY + 1), headers=bien)
    assert r.status_code == 413, r.status_code
    r = c.post("/auto/ingesta", data=(una * 1000)[:MAX_BODY], headers=bien)
    assert r.status_code == 200, "64 KB justos tienen que entrar"
    db_vaciar()
    print("  413 con 64 KB + 1, 200 con 64 KB justos")

    # el ejemplo del contrato, tal cual
    ej = "2026-10-08 11:29:32.50,-33.4067901,-70.5063931,612.4,37.25,184.3,9,14,0.90,1.60,1.30,35.8,1,1"
    validas = [
        ej,
        ej,                                                      # repetida en el lote
        linea("2026-10-08 11:29:33.00", alt=-500, rumbo=360, hdop=99.99, pdop=0,
              vdop=99.99, snr=99, fix=8, viaje=0, sats=50, vistos=100),  # bordes
        linea("2026-10-08 11:29:34", lat=90, lon=-180, kmh=300, snr=0, fix=0),
        linea("2026-10-08 11:29:34.00"),                         # la misma, normalizada
        linea("2026-10-08 11:29:35.5"),                          # un decimal -> .50
    ]
    invalidas = [
        linea("2026-10-08 11:29:40.00", lat=-90.0001),
        linea("2026-10-08 11:29:40.00", lon=180.5),
        linea("2026-10-08 11:29:40.00", alt=9000.1),
        linea("2026-10-08 11:29:40.00", alt=-500.1),
        linea("2026-10-08 11:29:40.00", kmh=-0.1),
        linea("2026-10-08 11:29:40.00", kmh=300.01),
        linea("2026-10-08 11:29:40.00", rumbo=360.1),
        linea("2026-10-08 11:29:40.00", sats=51),
        linea("2026-10-08 11:29:40.00", sats=9.0),               # no entero
        linea("2026-10-08 11:29:40.00", vistos=101),
        linea("2026-10-08 11:29:40.00", hdop=100),
        linea("2026-10-08 11:29:40.00", pdop=-0.1),
        linea("2026-10-08 11:29:40.00", vdop=99.991),
        linea("2026-10-08 11:29:40.00", snr=99.5),
        linea("2026-10-08 11:29:40.00", fix=9),
        linea("2026-10-08 11:29:40.00", viaje=2),
        linea("2026-10-08 11:29:40.00", viaje="1.0"),
        linea("2026-10-08 11:29:40.00", hdop="nan"),
        linea("2026-10-08 11:29:40.00", lat=""),                 # campo vacio
        linea("2099-01-01 00:00:00.00"),                         # futuro
        linea("2026-13-08 11:29:40.00"),                         # mes 13
        linea("2026-10-08 11:29:40.00") + ",1",                  # 15 columnas
        linea("2026-10-08 11:29:40.00").rsplit(",", 1)[0],       # 13 columnas
        "2026-10-08 11:29:40,-33.4,-70.5,0.0,10",                # el formato viejo
        ",".join(COLUMNAS),                                      # encabezado
        "basura",
    ]
    lote = "\n".join(validas[:3] + invalidas[:13] + validas[3:] + invalidas[13:] + [""]) + "\r\n"
    r = c.post("/auto/ingesta", data=lote, headers=bien)
    assert r.status_code == 200
    d = r.get_json()
    esperado = {"aceptadas": 4, "duplicadas": 2, "invalidas": len(invalidas),
                "ultimo_ts": "2026-10-08 11:29:35.50"}
    assert d == esperado, d
    print("  lote de 14 columnas:", json.dumps(d))

    r = c.post("/auto/ingesta", data=lote, headers=bien).get_json()
    assert r == dict(esperado, aceptadas=0, duplicadas=6), r
    with app.app_context():
        filas = db().execute("SELECT * FROM posicion ORDER BY ts_utc").fetchall()
    assert [f["ts_utc"] for f in filas] == ["2026-10-08 11:29:32.50", "2026-10-08 11:29:33.00",
                                            "2026-10-08 11:29:34.00", "2026-10-08 11:29:35.50"]
    f0 = dict(filas[0])
    assert f0 == {"ts_utc": "2026-10-08 11:29:32.50", "lat": -33.4067901, "lon": -70.5063931,
                  "kmh": 37.25, "sats": 9, "alt_m": 612.4, "rumbo": 184.3, "sats_vistos": 14,
                  "hdop": 0.9, "pdop": 1.6, "vdop": 1.3, "snr_prom": 35.8, "fix": 1,
                  "viaje": 1}, f0
    print("  reenvio idempotente:", json.dumps(r), "- siguen 4 filas, las 14 columnas en su lugar")

    r = c.post("/auto/ingesta", data="basura\n", headers=bien).get_json()
    assert r["ultimo_ts"] is None and r["invalidas"] == 1, r
    ahora = datetime(2026, 10, 8, 12, 0, 0)
    assert parsear(linea("2026-10-09 12:00:00.00"), ahora)
    assert parsear(linea("2026-10-09 12:00:00.01"), ahora) is None
    db_vaciar()

    # --- separacion: estacionado, viaje, hueco de 4 min (no corta), viaje,
    # corte de energia de 6 min, viaje, estacionado
    t0 = datetime(2026, 10, 6, 12, 0, 0)

    def en(s):
        return clave_ts(t0 + timedelta(seconds=s))

    ls = [linea(en(60 * i), kmh=0, viaje=0) for i in range(3)]            # 12:00-12:02
    ls += [linea(en(150 + i / 2), lat=-33.40 - 0.001 * i, kmh=40) for i in range(11)]
    ls += [linea(en(155 + 240 + i / 2), kmh=40) for i in range(4)]       # hueco 4 min
    ls += [linea(en(1000 + i / 2), kmh=40) for i in range(4)]            # hueco 6 min
    ls += [linea(en(1100 + 60 * i), lat=-33.5, lon=-70.6, kmh=0, viaje=0) for i in range(3)]
    assert c.post("/auto/ingesta", data="\n".join(ls), headers=bien).get_json()["aceptadas"] == 25
    with app.app_context():
        ts = tramos()
    assert [t["tipo"] for t in ts] == ["estacionado", "viaje", "viaje", "estacionado"], ts
    v1, v2, e2 = ts[1], ts[2], ts[3]
    assert v1["puntos"] == 15 and v2["puntos"] == 4, (v1["puntos"], v2["puntos"])
    assert v1["inicio"] == "2026-10-06 12:02:30.00"
    # 10 tramos de 0,001 grado de latitud = 111,195 m c/u, mas el salto de
    # vuelta a la latitud de base al final del hueco
    salto = haversine(-33.41, -70.5063931, -33.4067901, -70.5063931)
    assert abs(v1["km"] * 1000 - (10 * 111.195 + salto)) < 1, v1["km"]
    assert (e2["lat"], e2["lon"]) == (-33.5, -70.6) and abs(e2["segundos"] - 120) < 1e-3
    print("  separacion: estacionado | viaje (4 min de hueco no corta) | viaje (6 min si) | estacionado")

    # el cache incremental tiene que dar lo mismo que recorrer todo de nuevo
    extra = [linea(en(1300 + i / 2), kmh=10) for i in range(3)]
    c.post("/auto/ingesta", data="\n".join(extra), headers=bien)
    with app.app_context():
        incremental = tramos()
        _TRAMOS["corte"] = None
        assert tramos() == incremental, "el cache incremental difiere"
        c.post("/auto/ingesta", data=linea(en(30), kmh=0, viaje=0), headers=bien)  # en el pasado
        tras = tramos()
        _TRAMOS["corte"] = None
        assert tramos() == tras and tras[0]["puntos"] == 4, "no se entero de una fila vieja"
    assert [t["tipo"] for t in incremental][-1] == "viaje"
    print("  cache incremental = recorrido completo, tambien con una fila en el pasado")
    db_vaciar()

    # --- un viaje armado a mano, a 2 Hz, para el detalle
    kmh = ([0, 0, 0, 0] + [10, 20, 30, 40] + [40] * 4 + [200] + [40, 40] + [30, 20]
           + [18, 16, 14, 12, 10, 8, 6, 4] + [2, 1, 1, 1, 1, 1, 1, 1] + [4, 6, 8, 10, 12])
    alt = [600, 601] * 4 + [610] * 13 + [620] * 10 + [615] * 7
    assert len(kmh) == len(alt) == 38
    t1 = datetime(2026, 10, 7, 9, 0, 0)
    ls = [linea(clave_ts(t1 + timedelta(seconds=i / 2)), lat=-33.40 + 0.00005 * i,
                kmh=k, alt=900 if i == 12 else a, hdop=9.5 if i == 12 else 0.8)
          for i, (k, a) in enumerate(zip(kmh, alt))]
    c.post("/auto/ingesta", data="\n".join(ls), headers=bien)
    with app.app_context():
        pts = puntos_de("2026-10-07 09:00:00.00")
        v, a = resumir(pts), analizar(pts)
    assert len(pts) == 38 and v["malos"] == 1
    assert v["max"] == 40, "el punto con hdop 9,5 (200 km/h) entro al calculo"
    assert (a["sube"], a["baja"]) == (20, 5), (a["sube"], a["baja"])
    assert abs(a["quieto"] - 6.0) < 1e-3 and a["paradas"] == 1, (a["quieto"], a["paradas"])
    assert (a["acel"], a["fren"]) == (1, 1), a["eventos"]
    assert abs(a["eventos"][0][2] - 5.56) < 0.01 and abs(a["eventos"][1][2] + 5.56) < 0.01
    print("  detalle: subida %d m / bajada %d m, %s s detenido en %d detencion, "
          "%d aceleracion y %d frenada fuertes, el punto con hdop 9,5 fuera"
          % (a["sube"], a["baja"], dec(a["quieto"]), a["paradas"], a["acel"], a["fren"]))

    # la pagina: links relativos, porque Caddy le saca /panel/auto
    c.post("/auto/ingesta", data=linea(clave_ts(t1 + timedelta(hours=1)), kmh=0, viaje=0),
           headers=bien)
    html = c.get("/").get_data(as_text=True)
    assert 'href="viaje/2026-10-07T09:00:00.00"' in html, html
    assert "openstreetmap.org/?mlat=-33.406790" in html
    assert 'href="/' not in html and 'src="/' not in html
    r = c.get("/viaje/2026-10-07T09:00:00.00")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "../estatico/leaflet.js" in html
    assert 'href="/' not in html and 'src="/' not in html
    assert "07/10/2026 06:00" in html, "la hora no salio en America/Santiago (UTC-3)"
    assert "1 detención" in html and "20 m / 5 m" in html and "1 con mala señal" in html
    assert c.get("/viaje/" + clave_ts(t1 + timedelta(hours=1)).replace(" ", "T")).status_code == 404, \
        "un estacionamiento no tiene pagina de viaje"
    assert c.get("/viaje/no-es-fecha").status_code == 404
    assert c.get("/viaje/2020-01-01T00:00:00.00").status_code == 404
    assert c.get("/viaje/2026-10-07T09:00:00.50").status_code == 404, "mitad de un viaje"
    if ESTATICO.exists():
        for f in ("leaflet.js", "leaflet.css", "uPlot.iife.min.js", "uPlot.min.css"):
            assert c.get("/estatico/" + f).status_code == 200, f
    assert c.get("/estatico/../auto.py").status_code == 404
    assert c.get("/auto/ingesta").status_code == 405
    print("  paginas: links relativos, hora local, estacionamientos aparte, estaticos servidos")

    db_vaciar()
    _TRAMOS["corte"] = None
    assert "Todavía no hay viajes" in app.test_client().get("/").get_data(as_text=True)
    print("ok")


def db_vaciar():
    with app.app_context():
        with db() as cx:
            cx.execute("DELETE FROM posicion")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "servir"
    if cmd == "test":
        test()
    elif cmd == "servir":
        crear_token()
        migrar()

        def precalentar():
            with app.app_context():
                tramos()
        threading.Thread(target=precalentar, daemon=True).start()
        # ponytail: servidor de desarrollo de Flask (threaded), detras de
        # Caddy, igual que galeria.py. Un auto y un telefono no son carga.
        app.run(host="127.0.0.1", port=8010)
    else:
        sys.exit(__doc__)
