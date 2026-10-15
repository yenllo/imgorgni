
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
Guarda el tag EXIF "Software" de cada foto en files.software.

La pantalla de duplicados lo usa para distinguir el original de cámara de una
copia ya editada (Lightroom, Photoshop...). Lo leía abriendo los dos archivos
de cada par al dibujar la página: hasta 80 aperturas del disco externo por
página, con 441 páginas por revisar. Precomputado, la revisión no toca K:.

Solo lee el encabezado EXIF, no la imagen, así que es rápido comparado con
generar una miniatura.

    python precomputar_software.py                 # solo lo que falta
    python precomputar_software.py --duplicados    # solo pares pendientes (más rápido)
    python precomputar_software.py --rehacer       # recalcular todo

Reanudable: guarda cada 500 y saltea lo ya hecho.
"""
import argparse
import os
import sqlite3
import sys
import time

from web_curador import leer_software  # noqa: E402

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
# marcador para "ya lo busqué y no tiene": evita reintentarlo en cada corrida
VACIO = ""


def pendientes(c, solo_duplicados, rehacer):
    donde = "" if rehacer else "AND f.software IS NULL"
    if solo_duplicados:
        return c.execute(f"""
            SELECT DISTINCT f.id, f.file_path FROM files f
            JOIN duplicados d ON f.id IN (d.id_conservar, d.id_descartar)
            WHERE d.decision='pendiente' AND COALESCE(f.borrado,0)=0 {donde}""").fetchall()
    return c.execute(f"""SELECT f.id, f.file_path FROM files f
                         WHERE COALESCE(f.borrado,0)=0 {donde}""").fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duplicados", action="store_true",
                    help="solo los archivos de pares pendientes")
    ap.add_argument("--rehacer", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=120)
    if "software" not in {r[1] for r in c.execute("PRAGMA table_info(files)")}:
        c.execute("ALTER TABLE files ADD COLUMN software TEXT")
        c.commit()
        print("  + files.software")

    filas = pendientes(c, args.duplicados, args.rehacer)
    print(f"a leer: {len(filas):,}\n")
    lote, con, t0 = [], 0, time.time()
    for i, (pid, ruta) in enumerate(filas, 1):
        s = leer_software(ruta) if os.path.exists(ruta) else None
        con += bool(s)
        lote.append((s if s else VACIO, pid))
        if len(lote) >= 500:
            c.executemany("UPDATE files SET software=? WHERE id=?", lote)
            c.commit(); lote.clear()
        if i % 500 == 0:
            seg = time.time() - t0
            print(f"  {i:,}/{len(filas):,}  {i/seg:.0f}/s  "
                  f"faltan ~{(len(filas)-i)/max(i/seg,.01)/60:.0f} min", file=sys.stderr)
    if lote:
        c.executemany("UPDATE files SET software=? WHERE id=?", lote)
        c.commit()

    print(f"terminado en {(time.time()-t0)/60:.1f} min")
    print(f"  {con:,} tienen tag Software; {len(filas)-con:,} no")
    print("\n  los más comunes:")
    for r in c.execute("""SELECT software, COUNT(*) n FROM files
                          WHERE software IS NOT NULL AND software <> ''
                          GROUP BY software ORDER BY n DESC LIMIT 8"""):
        print(f"    {r[1]:7,}  {r[0][:60]}")
    c.close()


if __name__ == "__main__":
    main()
