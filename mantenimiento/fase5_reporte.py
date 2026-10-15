#!/usr/bin/env python3
"""=== REPORTE FASE 5 ===: arbol de K:, espacio y estado de photos_v2.db."""

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
import shutil
import sqlite3
import sys

K = "K:\\"
DB = os.path.join(str(_ROOT), "photos_v2.db")
SALTAR = {"$RECYCLE.BIN", "System Volume Information", "found.000"}


def medir(raiz):
    n = tam = 0
    for _d, _s, fs in os.walk(raiz):
        for f in fs:
            try:
                tam += os.path.getsize(os.path.join(_d, f))
                n += 1
            except OSError:
                pass
    return n, tam


print("=== REPORTE FASE 5 ===\n")
print("ARBOL DE K: (primer y segundo nivel)")
tot_n = tot_t = 0
for nivel1 in sorted(os.listdir(K)):
    p1 = os.path.join(K, nivel1)
    if nivel1 in SALTAR or not os.path.isdir(p1):
        continue
    n1, t1 = medir(p1)
    tot_n += n1
    tot_t += t1
    print("  %-34s %8.1f GB  %7d archivos" % (nivel1, t1 / 1e9, n1))
    hijos = sorted(x for x in os.listdir(p1) if os.path.isdir(os.path.join(p1, x)))
    for nivel2 in hijos:
        n2, t2 = medir(os.path.join(p1, nivel2))
        print("      %-30s %8.1f GB  %7d" % (nivel2[:30], t2 / 1e9, n2))
sueltos = [f for f in os.listdir(K) if os.path.isfile(os.path.join(K, f))]
if sueltos:
    print("  (archivos sueltos en la raiz: %d)" % len(sueltos))
print("  %-34s %8.1f GB  %7d archivos" % ("TOTAL", tot_t / 1e9, tot_n))

libre = shutil.disk_usage(K)
print("\nESPACIO EN K:  %.0f GB usados | %.0f GB libres | %.0f GB totales"
      % (libre.used / 1e9, libre.free / 1e9, libre.total / 1e9))

c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
filas = c.execute("SELECT file_path, COALESCE(borrado,0) FROM files").fetchall()
vivas = sum(1 for p, b in filas if not b and os.path.exists(p))
vivas_borradas = sum(1 for p, b in filas if b and os.path.exists(p))
huerfanas = sum(1 for p, b in filas if not b and not os.path.exists(p))
marcadas = sum(1 for _p, b in filas if b)
print("\nphotos_v2.db: %d filas" % len(filas))
print("  activas que apuntan a un archivo real ... %d" % vivas)
print("  marcadas borrado=1 ....................... %d (de esas, %d tienen archivo)"
      % (marcadas, vivas_borradas))
print("  activas HUERFANAS (sin archivo) .......... %d" % huerfanas)
pref = {}
for p, b in filas:
    if b or os.path.exists(p):
        continue
    pref["/".join(p.replace("\\", "/").split("/")[:3])] = pref.get(
        "/".join(p.replace("\\", "/").split("/")[:3]), 0) + 1
print("  de donde son las huerfanas que quedan:")
for k, n in sorted(pref.items(), key=lambda x: -x[1])[:8]:
    print("     %7d  %s" % (n, k))
