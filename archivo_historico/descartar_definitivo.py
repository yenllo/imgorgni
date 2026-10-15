r"""
Saca de la base, para siempre, las fotos que estan en una carpeta de descarte.

Es el paso final del apartado: los archivos ya estan todos juntos en
K:\_Telefono_revisar, ya se revisaron, y no vuelven al proyecto. Este script:

  1. deja un CSV con TODO lo que va a borrar (id, ruta, hash, bytes, fecha).
     Es el unico registro que queda: un DELETE no se deshace sin el backup.
  2. borra las filas de files y de las seis tablas que la referencian.
     foreign_keys esta en 0, o sea que ON DELETE CASCADE no corre y hay que
     hacerlo a mano o quedan huerfanos.
  3. renombra la carpeta a su nombre final.
  4. agrega esa carpeta a `exclude_dirs` en config.json, para que la Fase 1
     no la vuelva a indexar y las fotos no reaparezcan en la proxima corrida.

Ojo con `duplicados`: hay filas que nombran una foto de telefono como
`id_conservar` frente a una de camara. Si se borrara la foto sin borrar la
fila, quedaria una decision de descarte con un keeper que ya no existe. Por eso
se borra toda fila que toque un id que se va, de cualquiera de los dos lados.

    python descartar_definitivo.py                  # informe, no toca nada
    python descartar_definitivo.py --aplicar
"""
import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
CARPETA = r"K:\_Telefono_revisar"
FINAL = r"K:\_Fuera_del_proyecto"

# (tabla, columna) de todo lo que apunta a files.id
REFERENCIAS = [("photo_features", "photo_id"), ("event_photos", "photo_id"),
               ("faces", "photo_id"), ("face_scan_done", "photo_id"),
               ("ocr_marca_agua", "photo_id"),
               ("duplicados", "id_conservar"), ("duplicados", "id_descartar")]


def respaldar(db):
    dest = f"{db}.backup-antes-descartar-definitivo-{dt.datetime.now():%Y%m%d-%H%M%S}"
    o = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=180)
    d = sqlite3.connect(dest)
    with d:
        o.backup(d)
    d.close()
    o.close()
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--carpeta", default=CARPETA)
    ap.add_argument("--final", default=FINAL)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    c = sqlite3.connect(a.db, timeout=180)
    filas = c.execute(
        "SELECT id, file_path, file_hash_sha256, bytes, capture_date FROM files "
        "WHERE file_path LIKE ?", (a.carpeta + "\\%",)).fetchall()
    if not filas:
        print(f"no hay ninguna foto de la base en {a.carpeta}. Nada que hacer.")
        return
    ids = [f[0] for f in filas]
    q = ",".join(map(str, ids))
    gb = sum(f[3] or 0 for f in filas) / 1024 ** 3
    print(f"fotos a sacar de la base: {len(ids):,}   ({gb:.1f} GB en disco)")

    print("\nfilas que se borran ademas de files:")
    for t, col in REFERENCIAS:
        n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IN ({q})").fetchone()[0]
        if n:
            print(f"  {t}.{col}: {n:,}")

    sueltas = len(os.listdir(a.carpeta)) if os.path.isdir(a.carpeta) else 0
    print(f"\narchivos en {a.carpeta}: {sueltas:,}")
    if sueltas != len(ids):
        print(f"  OJO: la base dice {len(ids):,}. Reviso antes de seguir "
              f"(la diferencia son archivos que no estan en la base, o al reves).")

    if not a.aplicar:
        print("\ndry-run: no se borro nada. --aplicar para hacerlo.")
        return

    print(f"\nbackup: {respaldar(a.db)}")

    registro = f"descartadas_definitivo_{dt.datetime.now():%Y%m%d-%H%M%S}.csv"
    with open(registro, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "file_path", "sha256", "bytes", "capture_date"])
        w.writerows(filas)
    print(f"registro de lo borrado: {registro}")

    for t, col in REFERENCIAS:
        c.execute(f"DELETE FROM {t} WHERE {col} IN ({q})")
    c.execute(f"DELETE FROM files WHERE id IN ({q})")
    c.commit()
    print(f"borradas de la base: {len(ids):,}")

    if os.path.isdir(a.carpeta):
        if os.path.exists(a.final):
            print(f"  {a.final} ya existe: dejo la carpeta como esta.")
        else:
            os.rename(a.carpeta, a.final)
            print(f"carpeta renombrada: {a.carpeta} -> {a.final}")

    # Que la Fase 1 no la vuelva a indexar. exclude_dirs compara subcadenas
    # contra la ruta completa, asi que alcanza con el nombre de la carpeta.
    cfg_path = "config.json"
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    marca = Path(a.final).name
    ex = cfg.setdefault("exclude_dirs", [])
    if marca not in ex:
        ex.append(marca)
        json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"config.json: '{marca}' agregado a exclude_dirs")


if __name__ == "__main__":
    main()
