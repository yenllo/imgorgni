"""Para archivos de 'files' cuya ruta guardada ya no existe (la carpeta se
reorganizo/reinombro a mano), busca el archivo real en disco por su
SHA256 (ya calculado en Fase 1) y corrige file_path a la ubicacion actual.

No hashea todo el disco: primero filtra candidatos por tamano exacto
(barato, solo os.path.getsize) y solo hashea de verdad los que coinciden
en tamano -- una coincidencia de tamano exacto entre fotos reales
distintas es rarisima, asi que ese filtro deja pocos candidatos.

    python reubicar_por_hash.py                # dry-run, solo reporta
    python reubicar_por_hash.py --aplicar       # corrige la BD de verdad
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
import hashlib
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = "C:/Renzo/Proyectos/IMGorgni/photos_v2.db"

# Donde buscar los archivos movidos. Ajustar si hace falta mas alcance.
CARPETAS_BUSQUEDA = [
    r"E:\fotos",
    r"E:\Fotos_Organizadas",
    r"E:\GDrive_MiDriveYcomputadoras",
    r"E:\Fotos_F_rescatadas",
    r"G:\Fotos_F_rescatadas",
]


def sha256_de(fp, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row

    perdidos = c.execute("""
        SELECT id, file_path, file_hash_sha256, bytes FROM files
        WHERE file_hash_sha256 IS NOT NULL AND COALESCE(borrado,0)=0
    """).fetchall()
    perdidos = [f for f in perdidos if not os.path.exists(f["file_path"])]
    print(f"archivos con ruta rota: {len(perdidos):,}")
    if not perdidos:
        return

    por_tamano_faltante = defaultdict(list)
    for f in perdidos:
        if f["bytes"]:
            por_tamano_faltante[f["bytes"]].append(f)
    print(f"con tamano conocido para filtrar: {sum(len(v) for v in por_tamano_faltante.values()):,}")

    print("indexando candidatos por tamano (puede tardar)...", flush=True)
    candidatos_por_tamano = defaultdict(list)
    vistas = 0
    for carpeta in CARPETAS_BUSQUEDA:
        if not os.path.isdir(carpeta):
            continue
        for root, _dirs, archivos in os.walk(carpeta):
            for nombre in archivos:
                fp = os.path.join(root, nombre)
                try:
                    tam = os.path.getsize(fp)
                except OSError:
                    continue
                vistas += 1
                if tam in por_tamano_faltante:
                    candidatos_por_tamano[tam].append(fp)
    print(f"archivos escaneados: {vistas:,}, tamanos con candidatos: {len(candidatos_por_tamano):,}")

    encontrados = []
    sin_candidato = 0
    for tam, perdidos_tam in por_tamano_faltante.items():
        candidatos = candidatos_por_tamano.get(tam)
        if not candidatos:
            sin_candidato += len(perdidos_tam)
            continue
        hashes_candidatos = {}
        for cand in candidatos:
            try:
                hashes_candidatos[sha256_de(cand)] = cand
            except OSError:
                continue
        for f in perdidos_tam:
            nueva = hashes_candidatos.get(f["file_hash_sha256"])
            if nueva:
                encontrados.append((f["id"], f["file_path"], nueva))

    print()
    print(f"RELOCALIZADOS por hash exacto: {len(encontrados):,}")
    print(f"sin candidato de mismo tamano: {sin_candidato:,}")
    print(f"con tamano pero sin match de hash: "
          f"{sum(len(v) for v in por_tamano_faltante.values()) - len(encontrados) - sin_candidato:,}")

    for fid, vieja, nueva in encontrados[:15]:
        print(f"  {fid}: {vieja}  ->  {nueva}")

    if args.aplicar and encontrados:
        for fid, vieja, nueva in encontrados:
            c.execute("UPDATE files SET file_path=? WHERE id=?", (nueva, fid))
            c.execute("UPDATE duplicados SET ruta_conservar=? WHERE id_conservar=?", (nueva, fid))
            c.execute("UPDATE duplicados SET ruta_descartar=? WHERE id_descartar=?", (nueva, fid))
        c.commit()
        print(f"\naplicado: {len(encontrados)} rutas corregidas en la base")
    elif encontrados:
        print("\n(dry-run: corre con --aplicar para corregir la base de verdad)")


if __name__ == "__main__":
    main()
