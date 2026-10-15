#!/usr/bin/env python3
"""SHA-256 completo de los candidatos a borrar y de sus gemelos.

Por que: la huella rapida (tamaño + 64 KB de cada punta) se equivoco en 35 de
400 casos (8,75 %) medido el 14/10/2026. Son fotos del mismo disparo
reprocesadas: mismo tamaño exacto, mismos bordes, contenido distinto. Borrar
por esa huella habria perdido ~2.800 archivos unicos.

Hashear los 4,5 TB del disco serian horas. Solo hace falta el hash completo de
lo que la huella rapida marco como repetido (candidatos) y de su gemelo, mas
SD_Jpeg_2026 y DCIM Jpeg 2026, que son el paso 1.

    python fase4_hash_completo.py          # llena los que falten (se puede cortar)
    python fase4_hash_completo.py cuentas  # recuento con el hash completo
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
import sys
import time
from pathlib import Path

from fase4_ejecucion_y_auditoria import (DCIM, GALERIA, IA_V3, SD, SECUNDARIAS,
                                         TEL_DEST, TEL_SIN, _cache, anotar,
                                         archivos_bajo, borrables,
                                         rutas_del_curador)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def preparar(cx):
    if "sha" not in {r[1] for r in cx.execute("PRAGMA table_info(huella)")}:
        cx.execute("ALTER TABLE huella ADD COLUMN sha TEXT")
        cx.execute("CREATE INDEX IF NOT EXISTS ix_sha ON huella(sha)")
        cx.commit()


def que_hashear(cx):
    """Candidatos (lo que la huella rapida marco repetido) + gemelos + paso 1."""
    gemelo = {}
    for raiz in (GALERIA, TEL_DEST):
        for p, h in archivos_bajo(cx, raiz):
            gemelo.setdefault(h, []).append(p)
    falta = set()
    for raiz in [IA_V3] + SECUNDARIAS:
        if not Path(raiz).exists():
            continue
        for p, h in archivos_bajo(cx, raiz):
            if p.lower().endswith(".zip"):
                continue
            if h in gemelo:
                falta.add(p)
                falta.update(gemelo[h])      # todos los gemelos posibles
    for raiz in (SD, DCIM):                  # el paso 1 tambien se confirma entero
        if Path(raiz).exists():
            falta.update(p for p, _h in archivos_bajo(cx, raiz))
    ya = {r[0] for r in cx.execute("SELECT ruta FROM huella WHERE sha IS NOT NULL")}
    return sorted(falta - ya)


def hashear(cx):
    preparar(cx)
    pend = que_hashear(cx)
    total = sum(os.path.getsize(p) for p in pend if os.path.exists(p))
    anotar("hash completo: faltan %d archivos (%.1f GB)" % (len(pend), total / 1e9))
    t0, hechos, bytes_ = time.time(), 0, 0
    tanda = []
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
    anotar("hash completo listo: %d archivos, %.1f GB en %.0f min"
           % (hechos, bytes_ / 1e9, (time.time() - t0) / 60))


def _sha_bajo(cx, raiz):
    pref = str(raiz).rstrip("\\/") + os.sep
    like = pref.replace("^", "^^").replace("%", "^%").replace("_", "^_") + "%"
    return cx.execute("SELECT ruta, sha FROM huella WHERE sha IS NOT NULL "
                      "AND ruta LIKE ? ESCAPE '^'", (like,)).fetchall()


def cuentas(cx):
    """Lo mismo que el simulacro, pero exigiendo hash completo igual."""
    en_curador = rutas_del_curador()
    gal = {s for _p, s in _sha_bajo(cx, GALERIA)}
    tel = {s for _p, s in _sha_bajo(cx, TEL_DEST)}
    print("\n=== RECUENTO CON HASH COMPLETO ===")
    print("Galeria con sha: %d | _Telefono con sha: %d" % (len(gal), len(tel)))

    sd = {os.path.relpath(p, SD).lower(): s for p, s in _sha_bajo(cx, SD)}
    dc = {os.path.relpath(p, DCIM).lower(): s for p, s in _sha_bajo(cx, DCIM)}
    iguales = sum(1 for r, s in sd.items() if dc.get(r) == s)
    print("paso 1: SD %d | DCIM %d | identicos por sha y misma ruta: %d | distintos: %d"
          % (len(sd), len(dc), iguales, len(sd) - iguales))

    for etiqueta, raices, contra in (
            ("paso 2 (_Telefono sin organizar)", [TEL_SIN], gal | tel),
            ("paso 3 (resto de IA_v3)", [IA_V3], gal),
            ("paso 4 (secundarias)", SECUNDARIAS, gal)):
        repes, falsos, sin_sha = [], 0, 0
        for raiz in raices:
            if not Path(raiz).exists():
                continue
            for p, s in archivos_bajo(cx, raiz):
                if p.lower().endswith(".zip"):
                    continue
                if etiqueta.startswith("paso 3") and str(p).lower().startswith(str(TEL_SIN).lower()):
                    continue
                fila = cx.execute("SELECT sha FROM huella WHERE ruta = ?", (p,)).fetchone()
                if fila and fila[0]:
                    if fila[0] in contra:
                        repes.append(p)
                    else:
                        falsos += 1          # la huella rapida decia repetido, el sha no
                else:
                    sin_sha += 1
        ok, frenados = borrables(repes, en_curador)
        print("%s: repetidos de verdad %d (borrables %d, frenados por la base %d) "
              "| falsos positivos de la huella rapida %d | unicos sin hashear %d"
              % (etiqueta, len(repes), len(ok), len(frenados), falsos, sin_sha))


if __name__ == "__main__":
    cx = _cache()
    preparar(cx)
    if len(sys.argv) > 1 and sys.argv[1] == "cuentas":
        cuentas(cx)
    else:
        hashear(cx)
        cuentas(cx)
