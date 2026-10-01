r"""
Aparta las fotos de telefono a K:\_Telefono_revisar\ para revisarlas a mano.

No borra nada. Mueve dentro del MISMO volumen (es un rename: no copia bytes,
no depende de la velocidad del disco) y actualiza files.file_path en la misma
transaccion, asi el curador las sigue viendo en su pantalla /telefono con
miniatura y todo.

Conserva la estructura de carpetas debajo del destino:

    K:\Lightroom_extraido\2025\2025-10-14\IMG-20211105-WA0000.jpg
 -> K:\_Telefono_revisar\Lightroom_extraido\2025\2025-10-14\IMG-20211105-WA0000.jpg

Asi no hay choques de nombre y se ve de donde salio cada una. Cada corrida deja
un manifiesto CSV con origen,destino: con eso `--deshacer` revierte todo.

    python apartar_telefono.py                     # informe, no toca nada
    python apartar_telefono.py --aplicar
    python apartar_telefono.py --deshacer manifiesto_telefono_....csv
"""
import argparse
import csv
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_analisis import clasificar_dispositivo

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
DESTINO = r"K:\_Telefono_revisar"


def respaldar(db):
    """Foto consistente de la base. La API backup y no copy2: esta en WAL."""
    dest = f"{db}.backup-antes-apartar-telefono-{dt.datetime.now():%Y%m%d-%H%M%S}"
    o = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=180)
    d = sqlite3.connect(dest)
    with d:
        o.backup(d)
    d.close()
    o.close()
    return dest


def planear(c, destino, plano=False):
    """[(id, origen, destino)] de todo lo que hay que mover.

    Con `plano`, todo cae suelto en la raiz del destino y los nombres repetidos
    se desempatan con un sufijo. Sin `plano`, se conserva el arbol de carpetas.
    En los dos casos la corrida es reanudable: lo que ya esta en su lugar final
    no se vuelve a tocar.
    """
    raiz = Path(destino)
    plan = []
    tomados = {p.name.lower() for p in raiz.iterdir()} if raiz.is_dir() and plano else set()
    for pid, fp, cm in c.execute(
            "SELECT id, file_path, camera_model FROM files WHERE COALESCE(borrado,0)=0"):
        if clasificar_dispositivo(cm, fp) != "telefono":
            continue
        p = Path(fp)
        if p.parent == raiz if plano else raiz in p.parents:
            continue
        if plano:
            nombre, n = p.name, 1
            while nombre.lower() in tomados:
                n += 1
                nombre = f"{p.stem}_{n}{p.suffix}"
            tomados.add(nombre.lower())
            plan.append((pid, fp, str(raiz / nombre)))
            continue
        try:
            rel = p.relative_to(p.anchor)
        except ValueError:
            continue
        plan.append((pid, fp, str(raiz / rel)))
    return plan


def mover(c, plan, manifiesto):
    """Mueve y actualiza la base. El CSV se escribe ANTES de tocar la base:
    si algo explota a mitad de camino, el manifiesto ya sabe que se movio."""
    hechos, fallados = [], []
    with open(manifiesto, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "origen", "destino"])
        for pid, orig, dest in plan:
            try:
                if not os.path.exists(orig):
                    fallados.append((orig, "no esta en disco"))
                    continue
                if os.path.exists(dest):
                    fallados.append((orig, "el destino ya existe"))
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                os.replace(orig, dest)
            except OSError as e:
                fallados.append((orig, str(e)))
                continue
            w.writerow([pid, orig, dest])
            fh.flush()
            hechos.append((dest, pid))
    c.executemany("UPDATE files SET file_path=? WHERE id=?", hechos)
    c.commit()
    return hechos, fallados


def deshacer(c, manifiesto):
    filas = list(csv.DictReader(open(manifiesto, encoding="utf-8")))
    vuelta, fallados = [], []
    for f in reversed(filas):
        try:
            if not os.path.exists(f["destino"]):
                fallados.append((f["destino"], "ya no esta ahi"))
                continue
            os.makedirs(os.path.dirname(f["origen"]), exist_ok=True)
            os.replace(f["destino"], f["origen"])
        except OSError as e:
            fallados.append((f["destino"], str(e)))
            continue
        vuelta.append((f["origen"], int(f["id"])))
    c.executemany("UPDATE files SET file_path=? WHERE id=?", vuelta)
    c.commit()
    for raiz in {Path(f["destino"]).parents[-2] for f in filas if Path(f["destino"]).parents}:
        barrer_vacias(raiz)
    return vuelta, fallados


def barrer_vacias(raiz):
    """Borra las carpetas que quedaron vacias, de las hojas hacia arriba."""
    # De las hojas hacia arriba: os.walk devuelve (ruta, dirs, files), y la que
    # ordena es la RUTA, no la tupla (que siempre mide 3).
    for actual, _, _ in sorted(os.walk(str(raiz)), key=lambda t: len(t[0]), reverse=True):
        try:
            os.rmdir(actual)
        except OSError:
            pass              # no esta vacia: alguien dejo algo ahi, se respeta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--destino", default=DESTINO)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--plano", action="store_true",
                    help="todas sueltas en la raiz del destino, sin subcarpetas")
    ap.add_argument("--deshacer", metavar="CSV")
    a = ap.parse_args()

    if a.deshacer:
        print(f"backup: {respaldar(a.db)}\n")
        c = sqlite3.connect(a.db, timeout=180)
        v, f = deshacer(c, a.deshacer)
        print(f"devueltas a su lugar: {len(v):,}   fallaron: {len(f):,}")
        for r, m in f[:10]:
            print(f"   {m}: {r}")
        return

    c = sqlite3.connect(a.db, timeout=180)
    plan = planear(c, a.destino, plano=a.plano)
    gb = sum(r[1] or 0 for r in c.execute(
        "SELECT id, bytes FROM files WHERE id IN (%s)" % ",".join(str(p[0]) for p in plan))
    ) / 1024 ** 3 if plan else 0
    print(f"fotos de telefono a apartar: {len(plan):,}   ({gb:.1f} GB)")
    print(f"destino: {a.destino}")

    unidad = Path(a.destino).anchor
    if not os.path.isdir(unidad):
        print(f"\n{unidad} no esta montada. No hay nada que mover todavia.")
        return
    distintos = {Path(o).anchor for _, o, _ in plan} - {unidad}
    if distintos:
        print(f"\nOJO: hay origenes fuera de {unidad} ({distintos}): serian copia, no rename.")
        return

    if not a.aplicar:
        for _, o, d in plan[:5]:
            print(f"   {o}\n-> {d}")
        print(f"\ndry-run: no se movio nada. --aplicar para hacerlo.")
        return

    print(f"backup: {respaldar(a.db)}")
    manifiesto = f"manifiesto_telefono_{dt.datetime.now():%Y%m%d-%H%M%S}.csv"
    hechos, fallados = mover(c, plan, manifiesto)
    if a.plano:
        barrer_vacias(a.destino)
    print(f"\nmovidas: {len(hechos):,}   fallaron: {len(fallados):,}")
    print(f"manifiesto (para --deshacer): {manifiesto}")
    for r, m in fallados[:10]:
        print(f"   {m}: {r}")


if __name__ == "__main__":
    main()
