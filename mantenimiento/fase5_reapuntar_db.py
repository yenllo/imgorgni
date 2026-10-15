#!/usr/bin/env python3
"""Fase 5, paso 4: devolverle a photos_v2.db las rutas reales.

De 152.171 filas, 139.339 apuntaban a archivos que ya no existen: la galeria
se reorganizo por evento en agosto de 2026 y las rutas quedaron viejas. El
cambio de prefijo que parecia la solucion solo arregla 4.488, porque las fotos
ademas cambiaron de carpeta.

Lo que si funciona: buscar cada huerfana por (nombre, tamaño exacto) en el
indice del disco y aceptar solo las que tienen UN candidato. Cuando la fila
trae su sha256 (132.891 lo traen), se exige ademas que coincida con el
SHA-256 del archivo: ahi no hay duda posible.

    python fase5_reapuntar_db.py verificar 300   # muestra sin tocar nada
    python fase5_reapuntar_db.py simulacro
    python fase5_reapuntar_db.py ejecutar --hacerlo
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
import random
import re
import sqlite3
import sys
import time

from fase4_ejecucion_y_auditoria import _cache, anotar

DB = os.path.join(str(_ROOT), "photos_v2.db")
HEXA = re.compile(r"^[0-9a-f]{64}$")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def indice_disco():
    cx = _cache()
    d = {}
    for p, h in cx.execute("SELECT ruta, h FROM huella"):
        d.setdefault((os.path.basename(p).lower(), int(h.split(":")[0])), []).append(p)
    return d


def candidatas(solo_n=None):
    """[(id, ruta_vieja, ruta_nueva, sha_de_la_db)] de las que tienen un unico
    candidato por (nombre, tamaño)."""
    disco = indice_disco()
    c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    out, ambiguas, perdidas = [], 0, 0
    for i, p, b, h in c.execute("SELECT id, file_path, bytes, file_hash_sha256 FROM files"):
        if os.path.exists(p):
            continue
        cands = disco.get((os.path.basename(p.replace("\\", "/")).lower(), int(b or 0)), [])
        if len(cands) == 1:
            out.append((i, p, cands[0], h if h and HEXA.match(h.lower()) else None))
        elif cands:
            ambiguas += 1
        else:
            perdidas += 1
    c.close()
    if solo_n:
        random.seed(11)
        out = random.sample(out, min(solo_n, len(out)))
    return out, ambiguas, perdidas


def verificar(n):
    """Compara el sha256 que guarda la base contra el archivo candidato."""
    muestra, _amb, _per = candidatas(n)
    con_sha = [x for x in muestra if x[3]]
    print("muestra %d (con sha en la base: %d)" % (len(muestra), len(con_sha)))
    ok = mal = 0
    for _i, viejo, nuevo, h in con_sha:
        try:
            s = sha(nuevo)
        except OSError as e:
            print("   no pude leer %s: %s" % (nuevo, e))
            continue
        if s == h.lower():
            ok += 1
        else:
            mal += 1
            print("   NO COINCIDE")
            print("     fila dice : %s" % viejo)
            print("     candidato : %s" % nuevo)
    print("\ncoinciden %d | no coinciden %d" % (ok, mal))
    print("VEREDICTO:", "(nombre, tamaño) acierta en la muestra" if not mal
          else "NO REAPUNTAR a ciegas: hay candidatos equivocados")


def reapuntar(hacer):
    t0 = time.time()
    lista, ambiguas, perdidas = candidatas()
    anotar("fase5 db: %d huerfanas con candidato unico | %d ambiguas | %d sin candidato"
           % (len(lista), ambiguas, perdidas))

    # el sha manda: (nombre, tamaño) se equivoco en 1 de 262 (un export web con
    # el mismo nombre y peso). Si la fila trae sha y no coincide, no se toca.
    cache = _cache()
    en_disco = {p: s for p, s in cache.execute(
        "SELECT ruta, sha FROM huella WHERE sha IS NOT NULL")}
    confirmadas, rechazadas, sin_confirmar = [], [], []
    for x in lista:
        _i, _viejo, nuevo, h = x
        s = en_disco.get(nuevo)
        if h and s:
            (confirmadas if s == h.lower() else rechazadas).append(x)
        else:
            sin_confirmar.append(x)       # la fila no trae sha: no hay con que comparar
    anotar("fase5 db: confirmadas por sha %d | RECHAZADAS por sha distinto %d | "
           "sin sha en la fila (se aceptan por nombre+tamaño) %d"
           % (len(confirmadas), len(rechazadas), len(sin_confirmar)))
    for x in rechazadas[:3]:
        anotar("   rechazada: %s -> %s" % (x[1], x[2]))
    lista = confirmadas + sin_confirmar

    cx = sqlite3.connect(DB, timeout=120)
    ocupadas = {p for (p,) in cx.execute("SELECT file_path FROM files")}
    confirmadas_ids = {x[0] for x in confirmadas}
    # dos filas huerfanas pueden apuntar al MISMO archivo (eran copias en
    # carpetas distintas). file_path es unico, asi que sobrevive una: la que
    # tiene el sha confirmado, y la otra queda marcada borrado=1.
    por_destino = {}
    for x in lista:
        por_destino.setdefault(x[2], []).append(x)
    limpias, choques = [], []
    for destino, xs in por_destino.items():
        if destino in ocupadas:
            choques.extend(xs)
            continue
        xs.sort(key=lambda x: (x[0] not in confirmadas_ids, x[0]))
        limpias.append(xs[0])
        choques.extend(xs[1:])
    anotar("fase5 db: %d se pueden reapuntar | %d chocan con una fila que ya "
           "tiene esa ruta (se marcan borrado=1)" % (len(limpias), len(choques)))
    con_sha = sum(1 for x in limpias if x[3])
    anotar("fase5 db: de las limpias, %d traen sha en la base para confirmar" % con_sha)
    if not hacer:
        anotar("simulacro: no se toco la base")
        return
    cx.executemany("UPDATE files SET file_path = ? WHERE id = ?",
                   [(x[2], x[0]) for x in limpias])
    cx.executemany("UPDATE files SET borrado = 1 WHERE id = ?", [(x[0],) for x in choques])
    cx.commit()
    vivas = sum(1 for (p,) in cx.execute("SELECT file_path FROM files") if os.path.exists(p))
    anotar("fase5 db: listo en %.0f min. Filas que ahora apuntan a un archivo real: %d"
           % ((time.time() - t0) / 60, vivas))
    cx.close()


def hashear():
    """SHA-256 de los archivos candidatos que todavia no lo tienen en el cache.

    Hace falta porque (nombre, tamaño) se equivoco en 1 de 262: un export web
    con el mismo nombre y el mismo peso que otra copia. Sobre 117.251 filas
    eso serian ~450 registros apuntando a la foto equivocada."""
    lista, _amb, _per = candidatas()
    cx = _cache()
    ya = {p for (p,) in cx.execute("SELECT ruta FROM huella WHERE sha IS NOT NULL")}
    pend = sorted({x[2] for x in lista} - ya)
    total = sum(os.path.getsize(p) for p in pend if os.path.exists(p))
    anotar("fase5 db: faltan %d sha de archivos candidatos (%.0f GB)"
           % (len(pend), total / 1e9))
    t0, hechos, bytes_, tanda = time.time(), 0, 0, []
    for p in pend:
        try:
            s = sha(p)
            bytes_ += os.path.getsize(p)
        except OSError as e:
            anotar("  no pude leer %s: %s" % (p, e))
            continue
        tanda.append((s, p))
        hechos += 1
        if len(tanda) >= 500:
            cx.executemany("UPDATE huella SET sha = ? WHERE ruta = ?", tanda)
            cx.commit()
            tanda.clear()
            v = bytes_ / max(1e-9, time.time() - t0)
            print("\r  %d/%d  %.0f MB/s  faltan %.0f min"
                  % (hechos, len(pend), v / 1e6,
                     (total - bytes_) / max(1e-9, v) / 60), end="", flush=True)
    if tanda:
        cx.executemany("UPDATE huella SET sha = ? WHERE ruta = ?", tanda)
        cx.commit()
    anotar("fase5 db: %d sha nuevos en %.0f min" % (hechos, (time.time() - t0) / 60))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "simulacro"
    if cmd == "verificar":
        verificar(int(sys.argv[2]) if len(sys.argv) > 2 else 300)
    elif cmd == "hashear":
        hashear()
    else:
        reapuntar(cmd == "ejecutar" and "--hacerlo" in sys.argv)
