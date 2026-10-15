r"""Borra de la cuarentena SOLO lo que quedo probado que es seguro borrar.

Se guia unicamente por `verificacion_cuarentena.csv`, que salio de comparar los
68.272 archivos byte a byte contra la biblioteca viva. NUNCA por la tabla
`duplicados`, que tiene 47.985 filas 'movido' de las que solo 642 apuntaban a
donde el archivo realmente estaba.

Borra dos cosas:
  1. SEGURO      — tiene un gemelo vivo, identico byte a byte (465,7 GB)
  2. SIN_GEMELO pero marcado borrado=1 en la base — los que mandaste al
     basurero a proposito (10,3 GB)

NO toca:
  - SIN_GEMELO que la base cuenta como vivos (597): borrarlos dejaria a la
    biblioteca apuntando a la nada
  - SIN_GEMELO que no figuran en la base (1.015): nadie sabe que existen, y
    borrarlos no dejaria ni rastro

Antes de borrar cada SEGURO vuelve a comprobar que el gemelo siga en su lugar y
con el mismo tamaño: si en el medio alguien lo movio, ese archivo se saltea.

    python borrar_cuarentena.py               # muestra que haria, no borra
    python borrar_cuarentena.py --confirmar   # borra de verdad
"""
import argparse
import csv
import json
import os
import sqlite3
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
CSV = r"C:\Renzo\Proyectos\IMGorgni\verificacion_cuarentena.csv"
LOG = r"C:\Renzo\Proyectos\IMGorgni\borrado_cuarentena.csv"
RESPALDO_FILAS = r"C:\Renzo\Proyectos\IMGorgni\borrado_cuarentena_filas.json"
# El PRAGMA foreign_keys esta en 0, asi que el ON DELETE CASCADE declarado no
# dispara: las tablas que cuelgan de files.id se limpian a mano.
TABLAS = ["photo_features", "event_photos", "faces", "face_scan_done",
          "ocr_marca_agua", "etiqueta_veredicto"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirmar", action="store_true")
    a = ap.parse_args()

    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row

    aborrar = []          # (ruta, motivo, fila_id_o_None)
    saltados = []
    for f in csv.DictReader(open(CSV, encoding="utf-8")):
        ruta, v = f["ruta"], f["veredicto"]
        r = c.execute("SELECT id, borrado FROM files WHERE file_path=?",
                      (ruta,)).fetchone()
        if v == "SEGURO":
            gem = f["gemelo"]
            # el gemelo tiene que seguir estando: es la unica copia que queda
            if not gem or not os.path.exists(gem):
                saltados.append((ruta, "el gemelo ya no esta"))
                continue
            try:
                if os.path.getsize(gem) != int(f["bytes"]):
                    saltados.append((ruta, "el gemelo cambio de tamaño"))
                    continue
            except OSError:
                saltados.append((ruta, "no pude leer el gemelo"))
                continue
            aborrar.append((ruta, "tiene gemelo verificado", r["id"] if r else None))
        elif v == "SIN_GEMELO" and r and r["borrado"]:
            aborrar.append((ruta, "lo mandaste al basurero", r["id"]))

    bytes_tot = 0
    for ruta, _m, _i in aborrar:
        try:
            bytes_tot += os.path.getsize(ruta)
        except OSError:
            pass
    conf = [x for x in aborrar if x[1].startswith("tiene")]
    bas = [x for x in aborrar if x[1].startswith("lo mandaste")]
    print("a borrar: %d archivos, %.1f GB" % (len(aborrar), bytes_tot / 1e9))
    print("   con gemelo verificado : %d" % len(conf))
    print("   del basurero          : %d" % len(bas))
    print("   filas de la base a limpiar: %d" % sum(1 for x in aborrar if x[2]))
    if saltados:
        print("   SALTEADOS (el gemelo ya no esta): %d" % len(saltados))
    if not a.confirmar:
        print("\nesto fue una simulacion. Para borrar de verdad:")
        print("   python borrar_cuarentena.py --confirmar")
        return

    # Respaldo de las filas de la base antes de tocarlas
    ids = [x[2] for x in aborrar if x[2]]
    if ids:
        q = ",".join("?" * len(ids))
        resp = {"files": [dict(r) for r in c.execute(
            "SELECT * FROM files WHERE id IN (%s)" % q, ids)]}
        for t in TABLAS:
            resp[t] = [dict(r) for r in c.execute(
                "SELECT * FROM %s WHERE photo_id IN (%s)" % (t, q), ids)]
        with open(RESPALDO_FILAS, "w", encoding="utf-8") as fh:
            json.dump(resp, fh, ensure_ascii=False)
        print("respaldo de %d filas -> %s" % (len(resp["files"]), RESPALDO_FILAS))

    t0 = time.time()
    borrados = fallos = 0
    liberado = 0
    with open(LOG, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ruta", "bytes", "motivo", "resultado"])
        for i, (ruta, motivo, _fid) in enumerate(aborrar):
            try:
                n = os.path.getsize(ruta)
                os.remove(ruta)
                borrados += 1
                liberado += n
                w.writerow([ruta, n, motivo, "borrado"])
            except OSError as e:
                fallos += 1
                w.writerow([ruta, -1, motivo, "ERROR %s" % repr(e)[:60]])
            if (i + 1) % 2000 == 0:
                print("   %6d/%d  %.1f GB liberados  %.0f s"
                      % (i + 1, len(aborrar), liberado / 1e9, time.time() - t0))
                fh.flush()

    # Limpieza de la base: las filas que apuntaban a archivos que ya no existen
    if ids:
        q = ",".join("?" * len(ids))
        for t in TABLAS:
            c.execute("DELETE FROM %s WHERE photo_id IN (%s)" % (t, q), ids)
        c.execute("DELETE FROM files WHERE id IN (%s)" % q, ids)
        c.commit()
        print("filas de la base borradas: %d" % len(ids))

    print()
    print("borrados %d | fallos %d | %.1f GB liberados | %.0f s"
          % (borrados, fallos, liberado / 1e9, time.time() - t0))
    print("detalle ->", LOG)


if __name__ == "__main__":
    main()
