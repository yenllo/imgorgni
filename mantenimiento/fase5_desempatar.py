#!/usr/bin/env python3
"""Fase 5, cierre: desempatar por SHA-256 las huerfanas con varios candidatos.

El reapuntado solo acepta huerfanas con UN candidato por (nombre, tamaño).
Quedaron ~9.000 con varios: la misma foto repetida en Galeria, AOrdenar y
_Telefono. Si la fila trae su sha256 (la base lo guarda), el empate se
resuelve solo: gana el candidato cuyo archivo tiene ese mismo sha.

    python fase5_desempatar.py simulacro
    python fase5_desempatar.py ejecutar --hacerlo
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
import hashlib
import os
import re
import sqlite3
import sys

from fase4_ejecucion_y_auditoria import _cache, anotar

DB = os.path.join(str(_ROOT), "photos_v2.db")
HEXA = re.compile(r"^[0-9a-f]{64}$")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(hacer):
    cache = _cache()
    disco, shas = {}, {}
    for p, h, s in cache.execute("SELECT ruta, h, sha FROM huella"):
        disco.setdefault((os.path.basename(p).lower(), int(h.split(":")[0])), []).append(p)
        if s:
            shas[p] = s
    c = sqlite3.connect(DB, timeout=120)
    ocupadas = {p for (p,) in c.execute("SELECT file_path FROM files")}
    filas = c.execute("SELECT id, file_path, bytes, file_hash_sha256 FROM files "
                      "WHERE COALESCE(borrado,0) = 0").fetchall()
    resueltas, sin_sha, sin_match, nuevos = [], 0, 0, 0
    for i, p, b, h in filas:
        if os.path.exists(p):
            continue
        cands = disco.get((os.path.basename(p.replace("\\", "/")).lower(), int(b or 0)), [])
        if len(cands) < 2:
            continue
        if not (h and HEXA.match(h.lower())):
            sin_sha += 1
            continue
        gana = []
        for q in cands:
            if q not in shas:
                try:
                    shas[q] = sha(q)
                    nuevos += 1
                    cache.execute("UPDATE huella SET sha = ? WHERE ruta = ?", (shas[q], q))
                except OSError:
                    shas[q] = None
            if shas[q] == h.lower():
                gana.append(q)
        if len(gana) == 1 and gana[0] not in ocupadas:
            resueltas.append((gana[0], i))
            ocupadas.add(gana[0])
        elif not gana:
            sin_match += 1
    cache.commit()
    anotar("desempate: resueltas por sha %d | sin sha en la fila %d | "
           "ningun candidato con ese sha %d | sha calculados %d"
           % (len(resueltas), sin_sha, sin_match, nuevos))
    if hacer and resueltas:
        c.executemany("UPDATE files SET file_path = ? WHERE id = ?", resueltas)
        c.commit()
        vivas = sum(1 for (q,) in c.execute("SELECT file_path FROM files")
                    if os.path.exists(q))
        anotar("desempate: filas que apuntan a un archivo real: %d" % vivas)
    c.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "simulacro"
    main(cmd == "ejecutar" and "--hacerlo" in sys.argv)
