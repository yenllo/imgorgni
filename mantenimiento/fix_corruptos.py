"""Repasa los archivos marcados ancho=-1 ('no reintentar'): a varios les fallo
la lectura una sola vez, de pura casualidad (rescate de F: escribiendo el
archivo justo en ese momento), y siguen sanos. A los que de verdad estan
truncados los cruza por nombre de archivo contra hermanos sanos (mismo disparo,
otra extension/version) para sugerir un reemplazo. No copia ni borra nada:
solo actualiza `ancho`/`alto` de los que resultaron sanos y deja un reporte.
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
from collections import defaultdict

from web_curador import DB, _medidas

RAW_EXT = {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}


def normalizar(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"\s*\(\d{1,2}\)$", "", stem)   # (2), (3)
    stem = re.sub(r"_\d{1,2}$", "", stem)          # _1, _2 (no toca timestamps largos)
    stem = re.sub(r"-HDR$|-Pano$", "", stem, flags=re.I)
    return stem.lower()


def main():
    c = sqlite3.connect(DB, timeout=180)
    rotos = c.execute(
        "SELECT id, file_path, bytes FROM files WHERE ancho = -1"
    ).fetchall()
    print(f"revisando {len(rotos):,} archivos marcados como dañados…")

    resucitados = []
    siguen_rotos = []
    for id_, fp, bytes_ in rotos:
        if not os.path.exists(fp):
            siguen_rotos.append((id_, fp))
            continue
        w, h = _medidas(fp, bytes_)
        if w:
            resucitados.append((w, h, id_))
        else:
            siguen_rotos.append((id_, fp))

    if resucitados:
        c.executemany("UPDATE files SET ancho=?, alto=? WHERE id=?", resucitados)
        c.commit()
    print(f"  resucitados (falla vieja, archivo sano hoy): {len(resucitados):,}")
    print(f"  siguen dañados de verdad: {len(siguen_rotos):,}")

    # Para los que siguen rotos: buscar hermano sano por nombre normalizado,
    # solo entre RAW (donde vive el problema del rescate de F:).
    todos = c.execute("SELECT id, file_path, ancho FROM files").fetchall()
    por_nombre = defaultdict(list)
    for id_, fp, ancho in todos:
        por_nombre[normalizar(fp)].append((id_, fp, ancho))

    print("\nreemplazos sugeridos (roto -> hermano sano), no aplicados:")
    con_reemplazo = 0
    reporte = []
    for id_, fp in siguen_rotos:
        if os.path.splitext(fp)[1].lower() not in RAW_EXT:
            continue
        candidatos = [
            (hid, hfp) for hid, hfp, hancho in por_nombre[normalizar(fp)]
            if hid != id_ and hancho not in (None, -1) and os.path.exists(hfp)
        ]
        if candidatos:
            con_reemplazo += 1
            reporte.append((id_, fp, candidatos))

    for id_, fp, candidatos in reporte[:30]:
        print(f"  #{id_} {fp}")
        for hid, hfp in candidatos[:2]:
            print(f"      -> #{hid} {hfp}")
    if len(reporte) > 30:
        print(f"  … y {len(reporte) - 30} más")

    print(f"\ncon reemplazo disponible: {con_reemplazo:,} de {len(siguen_rotos):,} sin arreglo")
    c.close()


if __name__ == "__main__":
    main()
