#!/usr/bin/env python3
"""Cierre quirurgico: hashear solo lo imprescindible y purgar las copias de mas.

1. Dos archivos no pueden compartir SHA-256 si no pesan lo mismo. Asi que, de
   los archivos de Galeria que todavia no tienen sha, se hashean UNICAMENTE
   los que pesan exactamente lo que pesa alguna huerfana. De 120.000 quedan
   unos pocos miles, minutos en vez de dos horas.
2. Las filas marcadas borrado=1 cuyo archivo sigue en disco son la copia de
   mas: si su sha esta confirmado en un archivo vivo de Fotos\\Galeria y la
   copia NO vive en una carpeta intocable, se borra la copia.

    python fase6_quirurgico.py simulacro
    python fase6_quirurgico.py ejecutar --hacerlo
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
import time

from fase4_ejecucion_y_auditoria import GALERIA, _cache, anotar, protegida
from fase5_cierre import prioridad

DB = os.path.join(str(_ROOT), "photos_v2.db")
HEXA = re.compile(r"^[0-9a-f]{64}$")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def paso1(cache, cx, hacer):
    """Hashear por tamaño y reapuntar lo que aparezca."""
    huerfanas = [(i, p, b, h) for i, p, b, h in cx.execute(
        "SELECT id, file_path, bytes, file_hash_sha256 FROM files "
        "WHERE COALESCE(borrado,0) = 0")
        if not os.path.exists(p) and h and HEXA.match(h.lower()) and b]
    tallas = {int(b) for _i, _p, b, _h in huerfanas}
    anotar("paso 1: %d huerfanas con sha | %d tallas distintas" % (len(huerfanas), len(tallas)))

    faltan = [(p, int(h.split(":")[0])) for p, h, s in cache.execute(
        "SELECT ruta, h, sha FROM huella WHERE sha IS NULL")]
    candidatos = [p for p, tam in faltan if tam in tallas]
    peso = sum(t for p, t in faltan if t in tallas)
    anotar("paso 1: sin sha en el indice %d | candidatos por talla %d (%.1f GB a leer)"
           % (len(faltan), len(candidatos), peso / 1e9))
    if hacer and candidatos:
        t0, n, tanda = time.time(), 0, []
        for p in candidatos:
            try:
                tanda.append((sha(p), p))
            except OSError:
                continue
            n += 1
            if len(tanda) >= 300:
                cache.executemany("UPDATE huella SET sha = ? WHERE ruta = ?", tanda)
                cache.commit()
                tanda.clear()
                print("\r  hasheando %d/%d" % (n, len(candidatos)), end="", flush=True)
        if tanda:
            cache.executemany("UPDATE huella SET sha = ? WHERE ruta = ?", tanda)
            cache.commit()
        anotar("paso 1: %d hasheados en %.0f min" % (n, (time.time() - t0) / 60))

    por_sha = {}
    for p, s in cache.execute("SELECT ruta, sha FROM huella WHERE sha IS NOT NULL"):
        por_sha.setdefault(s, []).append(p)
    ocupadas = {p for (p,) in cx.execute("SELECT file_path FROM files")}
    mover, marcar = [], []
    for i, p, _b, h in huerfanas:
        cands = [q for q in por_sha.get(h.lower(), []) if os.path.exists(q)]
        if not cands:
            continue
        gana = sorted(cands, key=prioridad)[0]
        if gana in ocupadas:
            marcar.append((i,))
        else:
            mover.append((gana, i))
            ocupadas.add(gana)
    anotar("paso 1: rescatadas %d | redundantes a marcar %d" % (len(mover), len(marcar)))
    if hacer:
        cx.executemany("UPDATE files SET file_path = ? WHERE id = ?", mover)
        cx.executemany("UPDATE files SET borrado = 1 WHERE id = ?", marcar)
        cx.commit()
    return len(mover)


def paso2(cache, cx, hacer):
    """Borrar la copia fisica de las filas borrado=1 que todavia tienen archivo."""
    shas = {p: s for p, s in cache.execute(
        "SELECT ruta, sha FROM huella WHERE sha IS NOT NULL")}
    en_galeria = set()
    for p, s in shas.items():
        if p.lower().startswith(str(GALERIA).lower() + os.sep) and os.path.exists(p):
            en_galeria.add(s)
    sobran = [(i, p) for i, p in cx.execute(
        "SELECT id, file_path FROM files WHERE COALESCE(borrado,0) = 1")
        if os.path.exists(p)]
    borrables, frenadas, sin_confirmar = [], 0, 0
    for i, p in sobran:
        if protegida(p):
            frenadas += 1
            continue
        s = shas.get(p)
        if s and s in en_galeria:
            borrables.append(p)
        else:
            sin_confirmar += 1
    anotar("paso 2: filas borrado=1 con archivo %d | borrables (gemelo vivo confirmado "
           "en Galeria) %d | en carpeta intocable %d | sin confirmar %d"
           % (len(sobran), len(borrables), frenadas, sin_confirmar))
    if hacer:
        n = 0
        for p in borrables:
            try:
                os.unlink(p)
                cache.execute("DELETE FROM huella WHERE ruta = ?", (p,))
                n += 1
            except OSError as e:
                anotar("  no pude borrar %s: %s" % (p, e))
        cache.commit()
        anotar("paso 2: borradas %d copias de mas" % n)
    return len(borrables)


def hay_disco():
    """El gabinete USB se desconecto solo el 15/10/2026 a las 07:42 y, como
    os.path.exists() daba falso para todo, el script creyo que no quedaba un
    solo archivo vivo. Mejor cortar que decidir sobre un disco ausente."""
    if not (GALERIA.exists() and any(GALERIA.iterdir())):
        anotar("ABORTA: K: no responde (Fotos\\Galeria vacia o ausente). "
               "Revisa el cable antes de seguir.")
        sys.exit(2)


def main(hacer):
    cache = _cache()
    cx = sqlite3.connect(DB, timeout=180)
    anotar("=== CIERRE QUIRURGICO: %s ===" % ("EJECUTANDO" if hacer else "SIMULACRO"))
    hay_disco()
    paso1(cache, cx, hacer)
    hay_disco()
    paso2(cache, cx, hacer)
    hay_disco()
    if hacer:
        cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        vivas = sum(1 for (q,) in cx.execute(
            "SELECT file_path FROM files WHERE COALESCE(borrado,0) = 0") if os.path.exists(q))
        huerf = sum(1 for (q,) in cx.execute(
            "SELECT file_path FROM files WHERE COALESCE(borrado,0) = 0") if not os.path.exists(q))
        anotar("final: activas con archivo %d | huerfanas %d" % (vivas, huerf))
    cx.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "simulacro"
    main(cmd == "ejecutar" and "--hacerlo" in sys.argv)
