#!/usr/bin/env python3
"""Mueve duplicados confirmados a cuarentena. No borra nada, nunca.

Corre EN LA PI. Los movimientos son renames dentro del mismo NTFS: no copian
un solo byte, son instantaneos.

    python3 mover_cuarentena.py plan     tanda1.csv   # simulacro, no toca nada
    python3 mover_cuarentena.py aplicar  tanda1.csv   # mueve de verdad
    python3 mover_cuarentena.py deshacer registro.csv # revierte todo
    python3 mover_cuarentena.py test                  # self-check

La guarda que importa: antes de mover un duplicado se verifica que **el keeper
de su grupo exista en disco**. Sin eso, un grupo cuyo keeper se perdio en el
camino te dejaria sin ninguna copia. Es la unica forma de que esto pierda datos,
asi que es la unica condicion que aborta el archivo entero.
"""

import sys
from pathlib import Path as _Path

# Este script vive en mantenimiento/ pero los modulos (db.py, utils_*) y los
# datos (photos_v2.db, _salidas_locales/) viven en la raiz del repo.
_ROOT = _Path(__file__).resolve().parent.parent
_HERE = _Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(os.environ.get("DEDUP_RAIZ", "/mnt/fotos"))
CUARENTENA = RAIZ / "_duplicados_cuarentena"


def dentro_de_raiz(p):
    try:
        Path(p).resolve().relative_to(RAIZ.resolve())
        return True
    except ValueError:
        return False


def destino_de(origen):
    """La misma ruta, colgando de la cuarentena. Preserva la estructura para
    poder devolver cada archivo a su lugar exacto."""
    rel = Path(origen).resolve().relative_to(RAIZ.resolve())
    return CUARENTENA / rel


def leer(csv_path):
    filas = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            filas.append((r["grupo"], r["keeper"], r["duplicado"], int(r["bytes"])))
    return filas


def revisar(filas):
    """Clasifica sin tocar el disco. Devuelve (a_mover, problemas)."""
    a_mover, problemas = [], []
    keepers_ok = {}
    for gid, keeper, dup, tam in filas:
        if keeper not in keepers_ok:
            keepers_ok[keeper] = os.path.exists(keeper)

        if not keepers_ok[keeper]:
            problemas.append(("keeper no existe", dup, keeper))
        elif not dentro_de_raiz(dup) or not dentro_de_raiz(keeper):
            problemas.append(("fuera de la raiz", dup, keeper))
        elif not os.path.exists(dup):
            problemas.append(("el duplicado ya no esta", dup, keeper))
        elif os.path.realpath(dup) == os.path.realpath(keeper):
            problemas.append(("keeper y duplicado son el mismo archivo", dup, keeper))
        elif os.path.exists(destino_de(dup)):
            problemas.append(("ya hay algo en la cuarentena con esa ruta", dup, keeper))
        else:
            a_mover.append((gid, keeper, dup, tam))
    return a_mover, problemas


def informe(filas, a_mover, problemas):
    gb = sum(f[3] for f in a_mover) / 1e9
    print(f"  en la lista        : {len(filas):,}")
    print(f"  listos para mover  : {len(a_mover):,}   ({gb:,.1f} GB)")
    print(f"  con problemas      : {len(problemas):,}")
    if problemas:
        from collections import Counter
        for motivo, n in Counter(p[0] for p in problemas).most_common():
            print(f"      {n:>7,}  {motivo}")
        print("\n  primeros casos:")
        for motivo, dup, keeper in problemas[:5]:
            print(f"      [{motivo}]")
            print(f"        dup    : {dup}")
            print(f"        keeper : {keeper}")
    return gb


def plan(csv_path):
    filas = leer(csv_path)
    print(f"SIMULACRO — no se toca nada\n")
    a_mover, problemas = revisar(filas)
    informe(filas, a_mover, problemas)
    print(f"\n  destino: {CUARENTENA}")


def aplicar(csv_path):
    filas = leer(csv_path)
    print("Revisando antes de mover...")
    a_mover, problemas = revisar(filas)
    gb = informe(filas, a_mover, problemas)

    # Los que no tienen keeper en disco ya quedaron fuera de a_mover: esa es la
    # proteccion real. Aca solo se avisa fuerte, porque si son muchos significa
    # que el plan quedo viejo y conviene rescanear antes de seguir.
    sin_keeper = [p for p in problemas if p[0] == "keeper no existe"]
    if sin_keeper:
        print(f"\n  OJO: {len(sin_keeper):,} duplicados tienen su keeper ausente."
              f"\n  NO se mueven (moverlos seria quedarse sin ninguna copia).")

    if not a_mover:
        sys.exit("\nNo hay nada para mover.")

    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    registro_p = Path(csv_path).parent / f"movidos-{sello}.csv"
    print(f"\nMoviendo {len(a_mover):,} archivos ({gb:,.1f} GB)...")
    print(f"registro -> {registro_p}\n")

    t0, hechos, fallados = time.perf_counter(), 0, 0
    with open(registro_p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["grupo", "origen", "destino", "bytes", "keeper"])
        for gid, keeper, dup, tam in a_mover:
            dest = destino_de(dup)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():           # carrera: alguien lo creo entre revisar y ahora
                    fallados += 1
                    continue
                os.rename(dup, dest)
                w.writerow([gid, dup, str(dest), tam, keeper])
                hechos += 1
            except OSError as e:
                fallados += 1
                print(f"\n  falla en {dup}: {e}")
            if hechos % 2000 == 0:
                fh.flush()
                print(f"\r  {hechos:,}/{len(a_mover):,}  "
                      f"{time.perf_counter()-t0:.0f}s", end="", flush=True)

    print(f"\n\n  movidos : {hechos:,}")
    if fallados:
        print(f"  fallados: {fallados:,}")
    print(f"  tiempo  : {time.perf_counter()-t0:.0f}s")
    print(f"\n  Nada se borro. Para revertir:")
    print(f"    python3 mover_cuarentena.py deshacer {registro_p}")


def deshacer(registro_path):
    filas = list(csv.DictReader(open(registro_path, newline="", encoding="utf-8")))
    print(f"Devolviendo {len(filas):,} archivos a su lugar original...\n")
    vueltos, fallados = 0, 0
    for r in filas:
        origen, dest = r["origen"], r["destino"]
        try:
            if not os.path.exists(dest):
                fallados += 1
                continue
            if os.path.exists(origen):      # ya hay algo ahi, no pisar
                fallados += 1
                continue
            Path(origen).parent.mkdir(parents=True, exist_ok=True)
            os.rename(dest, origen)
            vueltos += 1
        except OSError as e:
            fallados += 1
            print(f"  falla con {dest}: {e}")
    print(f"  devueltos: {vueltos:,}")
    if fallados:
        print(f"  fallados : {fallados:,}")


def test():
    """ponytail: un check que falla si se rompe algo que pierda archivos."""
    import tempfile
    global RAIZ, CUARENTENA
    tmp = Path(tempfile.mkdtemp())
    RAIZ = tmp / "fotos"
    CUARENTENA = RAIZ / "_duplicados_cuarentena"
    (RAIZ / "buena").mkdir(parents=True)
    (RAIZ / "copias").mkdir()

    (RAIZ / "buena" / "A.JPG").write_bytes(b"a")
    (RAIZ / "copias" / "A.JPG").write_bytes(b"a")
    (RAIZ / "copias" / "B.JPG").write_bytes(b"b")     # su keeper no existe
    (RAIZ / "copias" / "C.JPG").write_bytes(b"c")     # su keeper tampoco, pero ausente

    lista = tmp / "t.csv"
    with open(lista, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["grupo", "keeper", "duplicado", "bytes"])
        w.writerow(["1", str(RAIZ / "buena" / "A.JPG"), str(RAIZ / "copias" / "A.JPG"), 1])
        w.writerow(["2", str(RAIZ / "buena" / "NO_EXISTE.JPG"), str(RAIZ / "copias" / "B.JPG"), 1])

    a_mover, problemas = revisar(leer(lista))
    assert len(a_mover) == 1, a_mover
    assert a_mover[0][2].endswith("copias/A.JPG")
    assert any(p[0] == "keeper no existe" for p in problemas), problemas
    # el duplicado con keeper ausente NO puede estar en la lista de mover
    assert not any("B.JPG" in m[2] for m in a_mover), "MOVERIA LA UNICA COPIA"

    # el destino preserva la estructura
    d = destino_de(RAIZ / "copias" / "A.JPG")
    assert d == CUARENTENA / "copias" / "A.JPG", d

    # nada se sirve de afuera de la raiz
    assert dentro_de_raiz(RAIZ / "copias" / "A.JPG")
    assert not dentro_de_raiz("/etc/shadow")

    # ida y vuelta completa
    aplicar(str(lista))
    assert not (RAIZ / "copias" / "A.JPG").exists(), "no se movio"
    assert (CUARENTENA / "copias" / "A.JPG").exists(), "no llego a cuarentena"
    assert (RAIZ / "buena" / "A.JPG").exists(), "SE TOCO EL KEEPER"
    assert (RAIZ / "copias" / "B.JPG").exists(), "movio uno sin keeper"

    reg = sorted(tmp.glob("movidos-*.csv"))[-1]
    deshacer(str(reg))
    assert (RAIZ / "copias" / "A.JPG").exists(), "no volvio"
    assert not (CUARENTENA / "copias" / "A.JPG").exists(), "quedo en cuarentena"

    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "test":
        test()
    elif cmd in ("plan", "aplicar", "deshacer") and len(sys.argv) == 3:
        {"plan": plan, "aplicar": aplicar, "deshacer": deshacer}[cmd](sys.argv[2])
    else:
        sys.exit(__doc__)
