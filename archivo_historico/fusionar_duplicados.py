r"""Funde las copias exactas de cada foto en una sola y borra las sobrantes.

Se agrupa por `files.file_hash_sha256`: si dos filas vivas comparten hash son el
mismo archivo byte a byte, asi que la verificacion ya viene hecha. No se usa la
tabla `duplicados`, que quedo desactualizada.

Fusiona ANTES de borrar, que es lo que evita perder trabajo: cada copia es una
fila distinta y puede tener su propia pertenencia a eventos, veredictos de
etiquetas y caras. Medido sobre esta base: en 12.757 grupos la pertenencia a
eventos estaba repartida entre varias copias. Elegir una y borrar el resto —sin
fusionar— habria sacado esas fotos de sus eventos.

Sobrevive la de ruta mas corta. Da igual cual sea: el contenido es identico y el
curador es el visor, no depende de la ruta.

    python fusionar_duplicados.py               # listado, no toca nada
    python fusionar_duplicados.py --confirmar   # funde, borra y limpia la base
"""
import argparse
import collections
import csv
import json
import os
import sqlite3
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
PLAN = r"C:\Renzo\Proyectos\IMGorgni\plan_fusion_duplicados.csv"
LOG = r"C:\Renzo\Proyectos\IMGorgni\fusion_duplicados.csv"
RESPALDO = r"C:\Renzo\Proyectos\IMGorgni\fusion_duplicados_respaldo.json"


def grupos_de(c):
    filas = c.execute("""SELECT id, file_path, bytes, file_hash_sha256 h FROM files
                          WHERE COALESCE(borrado,0)=0 AND bytes > 0
                            AND file_hash_sha256 IS NOT NULL
                            AND file_hash_sha256 <> ''""").fetchall()
    ph = collections.defaultdict(list)
    for r in filas:
        ph[r["h"]].append(r)
    salida = []
    for h, v in ph.items():
        if len(v) < 2:
            continue
        # sobrevive la de ruta mas corta; el desempate por texto lo hace estable
        orden = sorted(v, key=lambda r: (len(r["file_path"]), r["file_path"]))
        salida.append((orden[0], orden[1:]))
    return salida


def fusionar(c, vive, muere):
    """Pasa a `vive` todo lo que tenga `muere`. Devuelve que se movio."""
    movido = collections.Counter()
    # eventos: PK (event_id, photo_id), asi que OR IGNORE resuelve el choque
    n = c.execute("""INSERT OR IGNORE INTO event_photos (event_id, photo_id)
                     SELECT event_id, ? FROM event_photos WHERE photo_id=?""",
                  (vive, muere)).rowcount
    movido["eventos"] += max(n, 0)
    # veredictos: si la que sobrevive ya tiene uno para esa etiqueta, gana el
    # mas nuevo. Un veredicto viejo no deberia pisar una correccion reciente.
    for r in c.execute("SELECT etiqueta, ok, ts FROM etiqueta_veredicto WHERE photo_id=?",
                       (muere,)).fetchall():
        prev = c.execute("""SELECT ts FROM etiqueta_veredicto
                             WHERE photo_id=? AND etiqueta=?""",
                         (vive, r["etiqueta"])).fetchone()
        if prev is None or (r["ts"] or "") > (prev["ts"] or ""):
            c.execute("""INSERT INTO etiqueta_veredicto (photo_id, etiqueta, ok, ts)
                         VALUES (?,?,?,?)
                         ON CONFLICT(photo_id, etiqueta) DO UPDATE
                           SET ok=excluded.ok, ts=excluded.ts""",
                      (vive, r["etiqueta"], r["ok"], r["ts"]))
            movido["veredictos"] += 1
    # caras: solo si la que sobrevive no tiene ninguna. Son la misma imagen, asi
    # que duplicarlas seria contar dos veces a la misma persona.
    tiene = c.execute("SELECT 1 FROM faces WHERE photo_id=? LIMIT 1", (vive,)).fetchone()
    if not tiene:
        n = c.execute("UPDATE faces SET photo_id=? WHERE photo_id=?",
                      (vive, muere)).rowcount
        movido["caras"] += max(n, 0)
    for t in ("face_scan_done", "ocr_marca_agua"):
        c.execute("INSERT OR IGNORE INTO %s (photo_id) SELECT ? WHERE EXISTS "
                  "(SELECT 1 FROM %s WHERE photo_id=?)" % (t, t), (vive, muere))
    return movido


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirmar", action="store_true")
    a = ap.parse_args()
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row

    grupos = grupos_de(c)
    sobran = [r for _v, m in grupos for r in m]
    gb = sum(r["bytes"] for r in sobran) / 1e9
    print("grupos duplicados : %s" % "{:,}".format(len(grupos)))
    print("copias a borrar   : %s  (%.1f GB)" % ("{:,}".format(len(sobran)), gb))

    with open(PLAN, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sha256", "accion", "id", "bytes", "ruta"])
        for vive, muere in grupos:
            w.writerow([vive["h"], "CONSERVAR", vive["id"], vive["bytes"], vive["file_path"]])
            for r in muere:
                w.writerow([r["h"], "borrar", r["id"], r["bytes"], r["file_path"]])
    print("plan completo -> %s" % PLAN)

    if not a.confirmar:
        print("\nesto fue una simulacion. Revisa el plan y despues:")
        print("   python fusionar_duplicados.py --confirmar")
        return

    ids = [r["id"] for r in sobran]
    q = ",".join("?" * 900)
    resp = {"files": [], "event_photos": [], "etiqueta_veredicto": [], "faces": []}
    for i in range(0, len(ids), 900):
        lote = ids[i:i + 900]
        qq = ",".join("?" * len(lote))
        for t in resp:
            col = "id" if t == "files" else "photo_id"
            resp[t] += [dict(x) for x in c.execute(
                "SELECT * FROM %s WHERE %s IN (%s)" % (t, col, qq), lote)]
    with open(RESPALDO, "w", encoding="utf-8") as fh:
        json.dump(resp, fh, ensure_ascii=False)
    print("respaldo de %s filas -> %s" % ("{:,}".format(len(resp["files"])), RESPALDO))

    t0 = time.time()
    total = collections.Counter()
    borrados = fallos = 0
    liberado = 0
    with open(LOG, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ruta_borrada", "bytes", "sobrevive", "resultado"])
        for k, (vive, muere) in enumerate(grupos):
            for r in muere:
                total.update(fusionar(c, vive["id"], r["id"]))
                try:
                    n = r["bytes"]
                    os.remove(r["file_path"])
                    borrados += 1
                    liberado += n
                    w.writerow([r["file_path"], n, vive["file_path"], "borrado"])
                except OSError as e:
                    fallos += 1
                    w.writerow([r["file_path"], -1, vive["file_path"],
                                "ERROR %s" % repr(e)[:60]])
            if (k + 1) % 1000 == 0:
                c.commit()
                print("   %5d/%d grupos  %.1f GB liberados  %.0f s"
                      % (k + 1, len(grupos), liberado / 1e9, time.time() - t0))
                fh.flush()
        c.commit()

    # recien ahora se borran las filas: la fusion ya movio todo lo que colgaba
    for i in range(0, len(ids), 900):
        lote = ids[i:i + 900]
        qq = ",".join("?" * len(lote))
        for t in ("photo_features", "event_photos", "faces", "face_scan_done",
                  "ocr_marca_agua", "etiqueta_veredicto"):
            c.execute("DELETE FROM %s WHERE photo_id IN (%s)" % (t, qq), lote)
        c.execute("DELETE FROM files WHERE id IN (%s)" % qq, lote)
    c.commit()

    print()
    print("=== fusionado ===")
    for k, v in total.most_common():
        print("   %-12s %s" % (k, "{:,}".format(v)))
    print()
    print("borrados %s | fallos %d | %.1f GB liberados | %.0f s"
          % ("{:,}".format(borrados), fallos, liberado / 1e9, time.time() - t0))
    print("filas de la base borradas: %s" % "{:,}".format(len(ids)))
    print("detalle ->", LOG)


if __name__ == "__main__":
    main()
