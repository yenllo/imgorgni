#!/usr/bin/env python3
"""Verifica con SHA-256 COMPLETO una muestra de los duplicados que la Fase 4
piensa borrar. La huella rapida es (tamaño + 64 KB de cada punta): alcanza para
buscar candidatos, no para borrar 30 mil archivos sin mirar.

Para cada candidato toma su gemelo en Fotos/Galeria y compara los dos enteros.
Un solo desacuerdo significa que la huella rapida no sirve para esto.

    python fase4_verificar_muestra.py 300
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
import sqlite3
import sys
from pathlib import Path

from fase4_ejecucion_y_auditoria import (GALERIA, IA_V3, SECUNDARIAS, TEL_DEST,
                                         TEL_SIN, _cache, archivos_bajo)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


cx = _cache()
print("armando el indice por huella de Galeria y _Telefono...", flush=True)
gemelo = {}
for raiz in (GALERIA, TEL_DEST):
    for p, h in archivos_bajo(cx, raiz):
        gemelo.setdefault(h, p)

candidatos = []
for raiz in [IA_V3] + SECUNDARIAS:
    if not Path(raiz).exists():
        continue
    for p, h in archivos_bajo(cx, raiz):
        if p.lower().endswith(".zip"):
            continue
        if h in gemelo:
            candidatos.append((p, gemelo[h], h, str(raiz)))
print("candidatos a borrar por huella: %d" % len(candidatos))

random.seed(42)
muestra = random.sample(candidatos, min(N, len(candidatos)))
iguales = distintos = 0
leidos = 0
for p, g, h, raiz in muestra:
    try:
        a, b = sha(p), sha(g)
        leidos += os.path.getsize(p)
    except OSError as e:
        print("  no pude leer: %s (%s)" % (p, e))
        continue
    if a == b:
        iguales += 1
    else:
        distintos += 1
        print("  DISTINTOS con la misma huella:")
        print("    candidato %s" % p)
        print("    gemelo    %s" % g)

print("\nmuestra de %d: identicos %d | distintos %d | %.1f GB leidos"
      % (len(muestra), iguales, distintos, leidos / 1e9))
print("VEREDICTO:", "la huella rapida no se equivoco en la muestra"
      if not distintos else "NO BORRAR: la huella rapida iguala archivos distintos")
