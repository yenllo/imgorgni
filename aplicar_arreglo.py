"""Aplica lo que encontro fix_corruptos.py:
- con hermano sano: reemplaza el archivo roto por una copia del sano (atomico,
  vía archivo temporal + os.replace, para no volver a truncar nada a medias).
- sin hermano: se sacan del alcance del programa, movidos a una cuarentena en
  E: que ningun root_dir escanea, y se borra su fila de la base.
Deja un log de cada accion en log_arreglo_corruptos.csv.
"""
import csv
import os
import re
import shutil
import sqlite3
from collections import defaultdict

from web_curador import DB, _medidas

RAW_EXT = {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}
CUARENTENA = r"E:\_cuarentena_rotos_sin_backup"


def normalizar(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"\s*\(\d{1,2}\)$", "", stem)
    stem = re.sub(r"_\d{1,2}$", "", stem)
    stem = re.sub(r"-HDR$|-Pano$", "", stem, flags=re.I)
    return stem.lower()


def reemplazar_atomico(origen, destino):
    tmp = destino + ".tmp_reemplazo"
    shutil.copy2(origen, tmp)
    os.replace(tmp, destino)   # atomico: nunca queda a medio escribir


def main():
    c = sqlite3.connect(DB, timeout=180)
    rotos = c.execute("SELECT id, file_path, bytes FROM files WHERE ancho = -1").fetchall()

    todos = c.execute("SELECT id, file_path, ancho FROM files").fetchall()
    por_nombre = defaultdict(list)
    for id_, fp, ancho in todos:
        por_nombre[normalizar(fp)].append((id_, fp, ancho))

    log = []
    n_reemplazados = n_movidos = n_ya_no_existe = n_error = 0

    for id_, fp, bytes_ in rotos:
        candidatos = []
        if os.path.splitext(fp)[1].lower() in RAW_EXT:
            candidatos = [(hid, hfp) for hid, hfp, hancho in por_nombre[normalizar(fp)]
                          if hid != id_ and hancho not in (None, -1) and os.path.exists(hfp)]

        if candidatos and os.path.exists(fp):
            hid, hfp = candidatos[0]
            try:
                reemplazar_atomico(hfp, fp)
            except Exception as e:
                log.append((id_, fp, "ERROR_REEMPLAZO", str(e)))
                n_error += 1
                continue
            w, h = _medidas(fp, os.path.getsize(fp))
            c.execute("UPDATE files SET ancho=?, alto=?, bytes=? WHERE id=?",
                      (w or -1, h or -1, os.path.getsize(fp), id_))
            log.append((id_, fp, "REEMPLAZADO", f"copiado desde #{hid} {hfp}"))
            n_reemplazados += 1
            continue

        # sin hermano: cuarentena
        if not os.path.exists(fp):
            c.execute("DELETE FROM files WHERE id=?", (id_,))
            log.append((id_, fp, "YA_NO_EXISTE", ""))
            n_ya_no_existe += 1
            continue

        os.makedirs(CUARENTENA, exist_ok=True)
        destino = os.path.join(CUARENTENA, f"{id_}_{os.path.basename(fp)}")
        try:
            shutil.move(fp, destino)
        except Exception as e:
            log.append((id_, fp, "ERROR_MOVER", str(e)))
            n_error += 1
            continue
        c.execute("DELETE FROM files WHERE id=?", (id_,))
        log.append((id_, fp, "MOVIDO_A_CUARENTENA", destino))
        n_movidos += 1

    c.commit()
    c.close()

    with open("log_arreglo_corruptos.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "ruta_original", "accion", "detalle"])
        wr.writerows(log)

    print(f"reemplazados en su lugar : {n_reemplazados:,}")
    print(f"movidos a cuarentena     : {n_movidos:,}")
    print(f"ya no existian (limpiados de la bd): {n_ya_no_existe:,}")
    print(f"errores                  : {n_error:,}")
    print(f"cuarentena en            : {CUARENTENA}")
    print(f"log completo en          : log_arreglo_corruptos.csv")


if __name__ == "__main__":
    main()
