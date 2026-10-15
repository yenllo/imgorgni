#!/usr/bin/env python3
"""Fase 5, cierre: reapuntar lo que queda buscando por file_hash_sha256.

Las pasadas anteriores buscaban por (nombre, tamaño): eso deja afuera a las
que tienen varios candidatos y a las que cambiaron de nombre (sufijos _1, -2).
Esta busca al reves: toma el sha256 que la fila ya trae y lo busca en el
indice del disco. Si ese sha vive en varias rutas, gana la de mas arriba en
este orden, que es el que pidio Renzo:

    Fotos/Galeria > Fotos/Lightroom_extraido > Fotos/AOrdenar >
    Fotos/_Telefono > Fotos/DCIM_Jpeg_2026 > el resto

Si la ruta ganadora ya la ocupa otra fila viva, la huerfana se marca
borrado = 1: son la misma foto y file_path es unico.

    python fase5_cierre.py simulacro
    python fase5_cierre.py ejecutar --hacerlo
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
import os
import re
import sqlite3
import sys

from fase4_ejecucion_y_auditoria import _cache, anotar

DB = os.path.join(str(_ROOT), "photos_v2.db")
HEXA = re.compile(r"^[0-9a-f]{64}$")
ORDEN = ("k:\\fotos\\galeria\\", "k:\\fotos\\lightroom_extraido\\",
         "k:\\fotos\\aordenar\\", "k:\\fotos\\_telefono\\",
         "k:\\fotos\\dcim_jpeg_2026\\")


def prioridad(p):
    bajo = p.lower()
    for i, pref in enumerate(ORDEN):
        if bajo.startswith(pref):
            return (i, len(p), p)
    return (len(ORDEN), len(p), p)


def main(hacer):
    cache = _cache()
    por_sha = {}
    for p, s in cache.execute("SELECT ruta, sha FROM huella WHERE sha IS NOT NULL"):
        por_sha.setdefault(s, []).append(p)
    anotar("cierre: el indice del disco tiene %d sha distintos" % len(por_sha))

    cx = sqlite3.connect(DB, timeout=180)
    vivas = {p for (p,) in cx.execute(
        "SELECT file_path FROM files WHERE COALESCE(borrado,0) = 0")}
    ocupadas = {p for (p,) in cx.execute("SELECT file_path FROM files")}
    mover, marcar = [], []
    sin_sha = sin_rastro = 0
    for i, p, h in cx.execute("SELECT id, file_path, file_hash_sha256 FROM files "
                              "WHERE COALESCE(borrado,0) = 0"):
        if os.path.exists(p):
            continue
        if not (h and HEXA.match(h.lower())):
            sin_sha += 1
            continue
        cands = [q for q in por_sha.get(h.lower(), []) if os.path.exists(q)]
        if not cands:
            sin_rastro += 1
            continue
        gana = sorted(cands, key=prioridad)[0]
        if gana in ocupadas:
            marcar.append((i,))               # la foto ya tiene su fila: esta sobra
        else:
            mover.append((gana, i))
            ocupadas.add(gana)
            vivas.add(gana)
    anotar("cierre: reapuntables por sha %d | redundantes a marcar borrado=1 %d | "
           "sin sha en la fila %d | con sha pero sin rastro en el disco %d"
           % (len(mover), len(marcar), sin_sha, sin_rastro))

    # separadores: db.py guarda lo que se le pasa; hoy la base es 100% '\'
    mezcla = cx.execute("SELECT COUNT(*) FROM files WHERE file_path LIKE '%/%'").fetchone()[0]
    anotar("cierre: filas con barra normal en la ruta: %d (el resto usa '\\')" % mezcla)

    if not hacer:
        anotar("simulacro: no se toco la base")
        return
    cx.executemany("UPDATE files SET file_path = ? WHERE id = ?", mover)
    cx.executemany("UPDATE files SET borrado = 1 WHERE id = ?", marcar)
    cx.commit()
    cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cx.execute("VACUUM") if False else None       # VACUUM no: 3,5 GB y no hace falta
    vivas_reales = sum(1 for (q,) in cx.execute(
        "SELECT file_path FROM files WHERE COALESCE(borrado,0) = 0") if os.path.exists(q))
    anotar("cierre: filas activas que apuntan a un archivo real: %d" % vivas_reales)
    cx.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "simulacro"
    main(cmd == "ejecutar" and "--hacerlo" in sys.argv)
