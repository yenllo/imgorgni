
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
Separa los JPEG que perdieron datos de los que solo perdieron el marcador de fin.

El barrido de integridad marcó como "truncado" a todo JPEG sin el marcador ffd9.
Eso detecta una anomalía real -- el archivo se cortó al final durante el rescate
de F: -- pero NO significa que se haya perdido imagen: medido sobre 600, el 99,8%
abre perfecto y le faltan 0 bytes de datos. Solo faltan los 2 bytes que dicen
"acá termina el JPEG".

Contarlos como dañados infló la cifra 10x (13.733 en vez de ~1.400) y mandaba a
re-descargar 9.469 archivos que están intactos. Acá se distingue de verdad,
intentando decodificar cada uno:

    danado=0  sano
    danado=1  perdió datos: hay que recuperarlo de otro lado
    danado=2  solo le falta el marcador de fin: abre igual, no urge

Los RAW no se tocan: su veredicto lo da verificar_raw.py desempaquetando el
sensor, que es una prueba mucho más fuerte que abrir un JPEG.

    python reclasificar_danados.py            # simulacro
    python reclasificar_danados.py --aplicar
"""
import argparse
import sqlite3
import sys
import time

from PIL import Image, ImageFile

# Sube el tope de PIL a 300 Mpx. Sin esto las 114 fotos del modo alta
# resolucion del telefono (199,8 Mpx) levantan DecompressionBombError y este
# script las marcaria danado=1 siendo que estan perfectas.
from utils_images import MAX_PIXELES_FOTO

Image.MAX_IMAGE_PIXELS = MAX_PIXELES_FOTO

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"

SANO = 0
PERDIDA = 1
MENOR = 2
M_PERDIDA = "JPEG ilegible (perdió datos)"
M_MENOR = "solo falta el marcador de fin (abre igual)"


def revisar(ruta):
    """(nivel, motivo). Sin LOAD_TRUNCATED_IMAGES: queremos que falle si de
    verdad faltan datos, no que rellene el hueco en silencio."""
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(ruta) as im:
            im.load()
        return MENOR, M_MENOR
    except Exception:
        return PERDIDA, M_PERDIDA


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("-n", type=int)
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=120)
    c.row_factory = sqlite3.Row
    filas = c.execute("""SELECT id, file_path FROM files
                         WHERE danado=1 AND danado_motivo LIKE 'JPEG%'""").fetchall()
    if args.n:
        filas = filas[:args.n]
    print(f"JPEG a reclasificar: {len(filas):,}")
    print("MODO: " + ("ESCRIBIENDO" if args.aplicar else "simulacro") + "\n")

    lote, cont, t0 = [], {MENOR: 0, PERDIDA: 0, "falta": 0}, time.time()
    for i, r in enumerate(filas, 1):
        try:
            nivel, motivo = revisar(r["file_path"])
        except (FileNotFoundError, OSError):
            cont["falta"] += 1
            continue
        cont[nivel] += 1
        lote.append((nivel, motivo, r["id"]))
        if args.aplicar and len(lote) >= 500:
            c.executemany("UPDATE files SET danado=?, danado_motivo=? WHERE id=?", lote)
            c.commit()
            lote.clear()
        if i % 500 == 0:
            seg = time.time() - t0
            print(f"  {i:,}/{len(filas):,}  {i/seg:.0f}/s  "
                  f"faltan ~{(len(filas)-i)/max(i/seg,.01)/60:.0f} min", file=sys.stderr)
    if args.aplicar and lote:
        c.executemany("UPDATE files SET danado=?, danado_motivo=? WHERE id=?", lote)
        c.commit()

    rev = cont[MENOR] + cont[PERDIDA]
    print(f"\nterminado en {(time.time()-t0)/60:.0f} min\n")
    print(f"  {cont[MENOR]:6,}  abren igual (danado=2)"
          + (f"   {cont[MENOR]/rev*100:.1f}%" if rev else ""))
    print(f"  {cont[PERDIDA]:6,}  perdieron datos (danado=1)"
          + (f"   {cont[PERDIDA]/rev*100:.1f}%" if rev else ""))
    if cont["falta"]:
        print(f"  {cont['falta']:6,}  no están en el disco")

    if args.aplicar:
        print("\nreparto final de files.danado:")
        for r in c.execute("""SELECT danado, COALESCE(danado_motivo,'-') m, COUNT(*) n
                              FROM files WHERE COALESCE(danado,0)<>0
                              GROUP BY danado, m ORDER BY n DESC"""):
            print(f"  {r['n']:7,}  danado={r['danado']}  {r['m']}")
    else:
        print("\nsimulacro: no se escribió nada. Con --aplicar se guarda.")
    c.close()


if __name__ == "__main__":
    main()
