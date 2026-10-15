
import sys
from pathlib import Path as _Path

# Este script vive en mantenimiento/ pero los modulos (db.py, utils_*) y los
# datos (photos_v2.db, _salidas_locales/) viven en la raiz del repo.
_ROOT = _Path(__file__).resolve().parent.parent
_HERE = _Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
r"""
Pone al dia los SHA256 de la base que quedaron viejos.

El barrido de integridad encontro ~20.500 archivos sanos cuyo hash guardado no
corresponde al archivo actual (rutas remapeadas, copias rehechas). Eso hace que
toda la deteccion de duplicados EXACTO apoye sobre datos desactualizados: 127
de 959 pares marcados "SHA256 identico" ya no lo eran.

QUE SE TOCA Y QUE NO
  sano + hash distinto      -> se refresca (el archivo esta bien)
  sano + hash OK            -> nada que hacer
  truncado (roto)           -> NO se toca nunca. Refrescarle el hash borraria
                               justamente la evidencia de que el archivo cambio
                               o esta dañado.

Los RAW no tienen marcador de fin, asi que no hay forma independiente de
confirmar que esten sanos: si un RAW se corrompio despues de indexarlo, cae en
"hash distinto" igual que uno remapeado. Por eso NADA se pisa a ciegas: cada
cambio queda en un CSV con el hash viejo y el nuevo, y se hace una copia de la
base antes de escribir. Todo es reversible.

    python refrescar_hashes.py                 # simulacro, no escribe en la BD
    python refrescar_hashes.py --aplicar       # escribe (con backup previo)
    python refrescar_hashes.py --solo-jpeg     # solo los verificables
"""
import argparse
import collections
import csv
import os
import shutil
import sqlite3
import sys
import time

from verificar_integridad import (HASH_VIEJO, JPEG, ROTO_ANTES, ROTO_DESPUES,
                                  SANO_OK, categoria, leer)

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
LOTE = 500


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="escribir en la base (por defecto solo simula)")
    ap.add_argument("--csv", default="hashes_refrescados.csv")
    ap.add_argument("--solo-jpeg", action="store_true",
                    help="solo archivos con marcador de fin verificable")
    ap.add_argument("-n", type=int, help="limitar a N archivos (para probar)")
    args = ap.parse_args()

    if args.aplicar:
        copia = DB + f".backup-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(DB, copia)
        print(f"backup de la base en {copia}\n")

    c = sqlite3.connect(DB, timeout=120)
    filas = [r for r in c.execute(
        """SELECT id, file_path, file_hash_sha256, COALESCE(bytes,0) FROM files
           WHERE file_hash_sha256 IS NOT NULL AND file_hash_sha256 <> ''
             AND COALESCE(borrado,0)=0""")
        if not args.solo_jpeg or os.path.splitext(r[1])[1].lower() in JPEG]
    if args.n:
        filas = filas[:args.n]
    total_bytes = sum(b for _, _, _, b in filas) or 1
    print(f"a revisar: {len(filas):,} archivos ({total_bytes/1024**3:,.0f} GB)")
    print("MODO: " + ("ESCRIBIENDO en la base" if args.aplicar else "simulacro (no escribe)"))
    print()

    cont = collections.Counter()
    pend, t0, leido = [], time.time(), 0
    fcsv = open(args.csv, "w", encoding="utf-8", newline="")
    w = csv.writer(fcsv)
    w.writerow(["id", "ruta", "hash_viejo", "hash_nuevo", "accion"])

    def volcar():
        """Aplica el lote pendiente. Commit por lote: si esto se corta a las
        dos horas, lo ya escrito queda firme y el CSV lo refleja."""
        if pend and args.aplicar:
            c.executemany("UPDATE files SET file_hash_sha256=? WHERE id=?", pend)
            c.commit()
        pend.clear()

    try:
        for i, (pid, ruta, sha_db, nbytes) in enumerate(filas, 1):
            leido += nbytes
            try:
                sha, cola = leer(ruta)
            except FileNotFoundError:
                cont["no esta"] += 1
                continue
            except OSError:
                cont["ilegible"] += 1
                continue
            cat, _, _ = categoria(ruta, sha_db, sha, cola)
            cont[cat] += 1
            if cat == HASH_VIEJO:
                es_raw = os.path.splitext(ruta)[1].lower() not in JPEG
                cont["  de esos, RAW (sin verificar)" if es_raw
                     else "  de esos, JPEG sano confirmado"] += 1
                w.writerow([pid, ruta, sha_db, sha, "refrescado"])
                pend.append((sha, pid))
                if len(pend) >= LOTE:
                    volcar(); fcsv.flush()
            elif cat in (ROTO_ANTES, ROTO_DESPUES):
                # queda el hash nuevo anotado por si algun dia hace falta,
                # pero la base NO se toca: es la prueba de que cambio.
                w.writerow([pid, ruta, sha_db, sha, "NO tocado (dañado)"])
            if i % 500 == 0:
                seg = time.time() - t0
                falta = seg / max(leido, 1) * (total_bytes - leido)
                print(f"  {i:,}/{len(filas):,}  {leido/1024**3:.0f}/{total_bytes/1024**3:.0f} GB"
                      f"  {leido/1024**2/max(seg,1):.0f} MB/s  faltan ~{falta/60:.0f} min",
                      file=sys.stderr)
        volcar()
    finally:
        volcar()
        fcsv.close()
        c.close()

    print(f"\nterminado en {(time.time()-t0)/60:.0f} min\n")
    for k in (SANO_OK, HASH_VIEJO, "  de esos, JPEG sano confirmado",
              "  de esos, RAW (sin verificar)", ROTO_ANTES, ROTO_DESPUES,
              "no esta", "ilegible"):
        if cont[k]:
            print(f"  {cont[k]:7,}  {k}")
    print(f"\ndetalle en {args.csv}")
    if not args.aplicar:
        print("simulacro: la base NO se modifico. Con --aplicar se escribe.")


if __name__ == "__main__":
    main()
