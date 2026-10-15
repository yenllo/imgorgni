r"""
Borra los _edited de Lightroom del disco y de la base.

Un _edited es el revelado que exporta Lightroom junto al archivo original.
Medido sobre 64.262: el 100% tiene su original en la misma carpeta, y en 22.561
casos ese original es un RAW. Borrarlos NO pierde ninguna foto -- pierde el
revelado, que habria que rehacer desde el RAW.

Aun asi no se confia en ese 100% agregado: se comprueba archivo por archivo que
el original exista antes de tocar nada. El que no lo tenga se saltea y queda
anotado.

    python borrar_edited.py                # simulacro, no borra nada
    python borrar_edited.py --aplicar      # borra de verdad

Limpia TODAS las tablas que referencian la foto (files, photo_features,
event_photos, faces, face_scan_done, ocr_marca_agua y los pares de duplicados),
para no dejar filas huerfanas apuntando a un id que ya no existe.

Cada borrado queda en un CSV con ruta, tamaño y cual era su original.
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
FOTO = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic",
        ".arw", ".dng", ".cr2", ".nef", ".rw2", ".raf")
RE_ED = re.compile(r"^(?P<base>.+?)_edited(?:-\d+)?$", re.I)
# tabla -> columna que apunta a files.id
REFS = [("photo_features", "photo_id"), ("event_photos", "photo_id"),
        ("faces", "photo_id"), ("face_scan_done", "photo_id"),
        ("ocr_marca_agua", "photo_id")]


def original_de(ruta):
    """Ruta REAL del original de un _edited, o None si no esta en el disco.

    Se listan los nombres de la carpeta en vez de probar `exists()` por
    extension: en Windows exists() ignora mayusculas, asi que devolvia
    'otra.arw' cuando el archivo se llama 'otra.ARW' -- funciona para borrar,
    pero deja mal el registro de que original respaldaba a cada borrado.
    """
    carpeta, nombre = os.path.split(ruta)
    raiz, _ = os.path.splitext(nombre)
    m = RE_ED.match(raiz)
    if not m:
        return None
    base = m.group("base").lower()
    try:
        reales = os.listdir(carpeta)
    except OSError:
        return None
    for n in reales:
        r, e = os.path.splitext(n)
        if r.lower() == base and e.lower() in FOTO:
            return os.path.join(carpeta, n)
    return None


def tablas_existentes(c):
    hay = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return [(t, col) for t, col in REFS if t in hay]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--csv", default="edited_borrados.csv")
    ap.add_argument("-n", type=int, help="limitar (para probar)")
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=180)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    refs = tablas_existentes(c)

    # El tamaño se mide del disco y no de files.bytes: esa columna la llena el
    # curador (rellenar_metadatos), no la Fase 1, asi que todo lo indexado
    # anoche la tiene vacia y el total daba 0,0 GB.
    filas = []
    for r in c.execute("""SELECT id, file_path FROM files
                          WHERE LOWER(file_path) LIKE '%_edited%'"""):
        if not RE_ED.match(os.path.splitext(os.path.basename(r["file_path"]))[0]):
            continue
        try:
            n = os.path.getsize(r["file_path"])
        except OSError:
            n = 0
        filas.append({"id": r["id"], "file_path": r["file_path"], "bytes": n})
    if args.n:
        filas = filas[:args.n]
    print(f"candidatos con _edited en el nombre: {len(filas):,}")
    print("MODO: " + ("BORRANDO" if args.aplicar else "simulacro") + "\n")

    fh = open(args.csv, "w", encoding="utf-8", newline="")
    w = csv.writer(fh)
    w.writerow(["id", "ruta", "bytes", "original", "resultado"])

    borrados = bytes_lib = sin_orig = no_estaba = fallos = 0
    lote, t0 = [], time.time()
    for i, r in enumerate(filas, 1):
        ruta = r["file_path"]
        orig = original_de(ruta)
        if orig is None:
            # sin original localizable: no se toca
            sin_orig += 1
            w.writerow([r["id"], ruta, r["bytes"], "", "SALTEADO sin original"])
            continue
        if args.aplicar:
            try:
                if os.path.exists(ruta):
                    os.remove(ruta)
                    bytes_lib += r["bytes"]
                    res = "borrado"
                else:
                    no_estaba += 1
                    res = "no estaba en el disco (fila igual eliminada)"
                lote.append(r["id"])
                borrados += 1
            except OSError as e:
                fallos += 1
                res = f"FALLO {e}"
            w.writerow([r["id"], ruta, r["bytes"], orig, res])
            if len(lote) >= 500:
                _purgar(c, lote, refs)
                lote.clear()
                fh.flush()
        else:
            borrados += 1
            bytes_lib += r["bytes"]
            w.writerow([r["id"], ruta, r["bytes"], orig, "se borraria"])
        if i % 5000 == 0:
            print(f"  {i:,}/{len(filas):,}  ({time.time()-t0:.0f}s)", file=sys.stderr)

    if args.aplicar and lote:
        _purgar(c, lote, refs)
    fh.close()

    print(f"terminado en {(time.time()-t0)/60:.1f} min\n")
    print(f"  {'borrados' if args.aplicar else 'se borrarian'}: {borrados:,}"
          f"   ({bytes_lib/1024**3:.1f} GB)")
    if sin_orig:
        print(f"  SALTEADOS por no encontrar su original: {sin_orig:,}")
    if no_estaba:
        print(f"  ya no estaban en el disco: {no_estaba:,}")
    if fallos:
        print(f"  fallos al borrar: {fallos:,}")
    print(f"  detalle: {args.csv}")
    if not args.aplicar:
        print("\n  simulacro: no se toco nada. Con --aplicar se borra.")
    c.close()


def _purgar(c, ids, refs):
    """Saca la foto de la base entera: las tablas que la referencian y los
    pares de duplicados de los dos lados, para no dejar huerfanos."""
    qs = ",".join("?" * len(ids))
    for tabla, col in refs:
        c.execute(f"DELETE FROM {tabla} WHERE {col} IN ({qs})", ids)
    c.execute(f"DELETE FROM duplicados WHERE id_conservar IN ({qs}) "
              f"OR id_descartar IN ({qs})", ids + ids)
    c.execute(f"DELETE FROM files WHERE id IN ({qs})", ids)
    c.commit()


if __name__ == "__main__":
    main()
