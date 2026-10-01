r"""
Audita TODO lo que el export mandaría a "_Telefono (sin organizar)" y separa
lo que en realidad es de cámara.

El export usa `clasificar_dispositivo(...) != "camara"`, así que ahí cae tanto
'telefono' como 'desconocido'. Este script mira los dos grupos por separado y
busca, dentro de ellos, nombres inequívocos de cámara (DSC_, _DSC, DSCF, ...).

    python auditar_telefono.py                 # informe
    python auditar_telefono.py --csv x.csv     # además, lista completa
"""
import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_analisis import clasificar_dispositivo

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"

# Nombres que solo produce una cámara dedicada. IMG_ queda AFUERA a propósito:
# lo usan tanto Canon como el iPhone, así que no prueba nada por sí solo.
CAMARA_RE = re.compile(r"^(_?DSC[_N]?\d|DSCF\d|CRW_\d|MVI_\d|PICT\d|IMGP\d|R\d{7})",
                       re.IGNORECASE)
# Nombres inequívocos de teléfono / mensajería
TELEFONO_RE = re.compile(r"(screenshot|captura|-wa\d|whatsapp|received_|^PXL_|"
                         r"^IMG-\d{8}|^VID-|^Screenshot)", re.IGNORECASE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--csv", help="volcar el detalle de los sospechosos")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
    filas = conn.execute("SELECT file_path, camera_model FROM files").fetchall()
    print(f"fotos en la BD: {len(filas):,}\n")

    grupos = Counter()
    sospechosos = []          # irían a teléfono pero el nombre dice cámara
    por_modelo = Counter()
    con_exif_telefono = Counter()   # nombre de cámara pero EXIF de teléfono
    for fp, cm in filas:
        disp = clasificar_dispositivo(cm, fp)
        grupos[disp] += 1
        if disp == "camara":
            continue
        nombre = Path(fp).name
        if not CAMARA_RE.match(nombre) or TELEFONO_RE.search(nombre):
            continue
        # Si el EXIF dice explícitamente que es un teléfono, le creemos al EXIF:
        # el nombre pudo quedar de un renombrado o de una app. El error real son
        # las que NO tienen EXIF y encima se llaman como una cámara.
        if disp == "telefono":
            con_exif_telefono[(cm or "?").strip()[:38]] += 1
            continue
        sospechosos.append((fp, cm or "", disp))
        por_modelo[(cm or "(sin EXIF)").strip()[:38]] += 1

    print("clasificación actual:")
    for k in ("camara", "telefono", "desconocido"):
        marca = "  -> va a la carpeta de teléfono" if k != "camara" else ""
        print(f"   {k:<12} {grupos[k]:>8,}{marca}")
    a_telefono = grupos["telefono"] + grupos["desconocido"]
    print(f"\n   total a '_Telefono (sin organizar)': {a_telefono:,}")

    if con_exif_telefono:
        print(f"\n   (descartadas {sum(con_exif_telefono.values()):,} con nombre de "
              f"cámara pero EXIF de teléfono — el EXIF manda)")

    print(f"\n❗ MAL CLASIFICADAS: {len(sospechosos):,}"
          f"  ({100*len(sospechosos)/max(a_telefono,1):.1f}% de esa carpeta)")
    if por_modelo:
        print("\n   por camera_model del EXIF:")
        for m, n in por_modelo.most_common(12):
            print(f"     {m:<40} {n:>7,}")
        print("\n   ejemplos:")
        for fp, cm, disp in sospechosos[:8]:
            print(f"     [{disp}] {Path(fp).name:<28} model={cm or '(vacío)'}")

    if args.csv and sospechosos:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file_path", "camera_model", "clasificacion"])
            w.writerows(sospechosos)
        print(f"\n   detalle completo -> {args.csv}")


if __name__ == "__main__":
    main()
