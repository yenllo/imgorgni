#!/usr/bin/env python3
"""Exporta de photos_v2.db el catalogo minimo que necesita el panel de la Pi.

photos_v2.db son 3,4 GB, pero el grueso es photo_features, etiqueta_score,
duplicados y faces: embeddings y scores que el panel no mira. Lo que el panel
necesita para mostrar eventos es chico —quienes son los eventos y que fotos
tiene cada uno— y las miniaturas la Pi las genera sola, porque el 8TB con los
originales esta colgado de ella.

Abre photos_v2.db en SOLO LECTURA. No la modifica nunca.

    python exportar_catalogo.py                # escribe catalogo.db
    python exportar_catalogo.py --enviar       # y lo copia a la Pi
    python exportar_catalogo.py --test         # self-check
"""

import sys
from pathlib import Path as _Path

# Este script vive en mantenimiento/ pero los modulos (db.py, utils_*) y los
# datos (photos_v2.db, _salidas_locales/) viven en la raiz del repo.
_ROOT = _Path(__file__).resolve().parent.parent
_HERE = _Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
ORIGEN = BASE / "photos_v2.db"
DESTINO = BASE / "catalogo.db"

# Donde vive el catalogo en la Pi, y por donde se manda. Va por Tailscale para
# no depender de estar en la misma red que la Pi.
# el alias de ~/.ssh/config, que ya trae la clave dedicada
PI = os.environ.get("CATALOGO_PI", "fotosnas")
PI_RUTA = os.environ.get("CATALOGO_PI_RUTA", "/home/yenllo/galeria/catalogo.db")

ESQUEMA = """
CREATE TABLE evento (
    id        INTEGER PRIMARY KEY,
    nombre    TEXT,
    inicio    TEXT,
    fin       TEXT,
    coleccion TEXT,
    tipo      TEXT,
    parent_id INTEGER,
    fotos     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_evento_coleccion ON evento(coleccion, inicio);
CREATE TABLE foto (
    id     INTEGER PRIMARY KEY,
    ruta   TEXT NOT NULL,
    fecha  TEXT,
    camara TEXT,
    ancho  INTEGER,
    alto   INTEGER
);
CREATE TABLE evento_foto (
    evento_id INTEGER NOT NULL,
    foto_id   INTEGER NOT NULL,
    PRIMARY KEY (evento_id, foto_id)
);
CREATE INDEX ix_ef_evento ON evento_foto(evento_id);
"""


def exportar():
    if DESTINO.exists():
        DESTINO.unlink()
    src = sqlite3.connect(f"file:{ORIGEN}?mode=ro", uri=True)
    dst = sqlite3.connect(DESTINO)
    dst.executescript(ESQUEMA)

    # Solo lo que el panel puede llegar a mostrar: nada borrado ni dañado.
    vivas = ("SELECT id, file_path, capture_date, camera_model, ancho, alto "
             "FROM files WHERE COALESCE(borrado,0)=0 AND COALESCE(danado,0)=0")
    dst.executemany("INSERT INTO foto VALUES (?,?,?,?,?,?)", src.execute(vivas))

    dst.executemany(
        "INSERT INTO evento (id,nombre,inicio,fin,coleccion,tipo,parent_id) "
        "VALUES (?,?,?,?,?,?,?)",
        src.execute("SELECT id, COALESCE(name, suggested_name), start_time, "
                    "end_time, coleccion, coleccion_tipo, parent_id FROM events"))

    # el INSERT OR IGNORE cubre las fotos que quedaron fuera por borradas
    dst.executemany(
        "INSERT OR IGNORE INTO evento_foto VALUES (?,?)",
        src.execute("SELECT ep.event_id, ep.photo_id FROM event_photos ep "
                    "JOIN files f ON f.id = ep.photo_id "
                    "WHERE COALESCE(f.borrado,0)=0 AND COALESCE(f.danado,0)=0"))

    # el conteo se guarda hecho: el panel no tiene que contar 138 mil filas
    dst.execute("UPDATE evento SET fotos = ("
                "SELECT COUNT(*) FROM evento_foto WHERE evento_id = evento.id)")
    dst.execute("DELETE FROM evento WHERE fotos = 0")
    dst.commit()

    n = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
         for t in ("evento", "foto", "evento_foto")}
    dst.execute("VACUUM")
    dst.close()
    src.close()
    return n


def enviar():
    destino = f"{PI}:{PI_RUTA}"
    print(f"copiando a {destino} ...")
    r = subprocess.run(["scp", "-q", str(DESTINO), destino])
    if r.returncode:
        sys.exit(f"scp fallo con codigo {r.returncode}")
    print("enviado")


def test():
    """ponytail: un solo check, que el catalogo no pierda ni invente eventos."""
    assert ORIGEN.exists(), f"no encuentro {ORIGEN}"
    n = exportar()
    src = sqlite3.connect(f"file:{ORIGEN}?mode=ro", uri=True)
    dst = sqlite3.connect(DESTINO)

    # ningun evento con fotos vivas puede faltar en el catalogo
    esperados = src.execute(
        "SELECT COUNT(DISTINCT ep.event_id) FROM event_photos ep "
        "JOIN files f ON f.id = ep.photo_id "
        "WHERE COALESCE(f.borrado,0)=0 AND COALESCE(f.danado,0)=0").fetchone()[0]
    assert n["evento"] == esperados, f"eventos: {n['evento']} != {esperados}"

    # toda foto referida existe: sin esto el panel muestra eventos vacios
    huerfanas = dst.execute(
        "SELECT COUNT(*) FROM evento_foto ef "
        "LEFT JOIN foto f ON f.id = ef.foto_id WHERE f.id IS NULL").fetchone()[0]
    assert huerfanas == 0, f"{huerfanas} referencias a fotos que no estan"

    # las rutas tienen que servirle a la Pi, o sea venir de K:
    ruta = dst.execute("SELECT ruta FROM foto LIMIT 1").fetchone()[0]
    assert ruta, "ruta vacia"
    src.close()
    dst.close()
    print("ok —", n, f"{DESTINO.stat().st_size / 1048576:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enviar", action="store_true", help="copiar a la Pi al terminar")
    ap.add_argument("--test", action="store_true", help="self-check")
    a = ap.parse_args()

    if a.test:
        test()
    else:
        t0 = time.perf_counter()
        n = exportar()
        mb = DESTINO.stat().st_size / 1048576
        print(f"{DESTINO.name}: {mb:.1f} MB en {time.perf_counter() - t0:.1f}s")
        print(f"  eventos {n['evento']}  fotos {n['foto']}  relaciones {n['evento_foto']}")
        if a.enviar:
            enviar()
