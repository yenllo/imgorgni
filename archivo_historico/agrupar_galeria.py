#!/usr/bin/env python3
"""Mete las carpetas de evento sueltas de galeria/ adentro de su coleccion.

El arbol de colecciones ya existe —`Liga ExScuola/Liga ExScuola 2025-2/...`—
pero varias fechas quedaron sueltas en la raiz con las fotos adentro. Esto las
fusiona con su carpeta del arbol, emparejando por la FECHA del nombre.

Solo mueve archivos. No toca la base: las rutas se resincronizan al final con
reapuntar_rutas.py, una sola vez, cuando el disco ya este como lo queres.

Las carpetas sueltas SIN pareja en el arbol no se tocan: son eventos sueltos
que corresponde que vivan en la raiz (un cumpleaños no pertenece a una liga).

    python agrupar_galeria.py              # simulacro
    python agrupar_galeria.py --mover
    python agrupar_galeria.py --deshacer bitacora.jsonl
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

G = Path(os.environ.get("GALERIA", "K:/galeria"))
FECHA = re.compile(r"(\d{4}-\d{2}-\d{2})")


def archivos(d):
    return [Path(c) / a for c, _s, arch in os.walk(d) for a in arch]


def arbol_por_fecha():
    """Carpetas de evento que ya viven dentro de una coleccion, por fecha."""
    dest = {}
    for raiz, dirs, _f in os.walk(G):
        prof = Path(raiz).relative_to(G).parts
        if not 2 <= len(prof) <= 3:
            continue
        m = FECHA.search(Path(raiz).name)
        if m:
            dest.setdefault(m.group(1), Path(raiz))
    return dest


def etiqueta(nombre):
    """Lo que viene despues de la fecha, primera palabra. `2025-08-30 F1` -> `f1`.

    La fecha sola no alcanza para emparejar: el 2026-05-22 hay DOS eventos
    distintos ("Fotos Martina" con una Nikon a las 16:12 y "CitaMateo" a las
    14:17). Sin esto se fusionaban y era un error.
    """
    resto = FECHA.sub("", nombre, count=1).strip(" -_")
    return resto.split()[0].lower() if resto.split() else ""


def plan():
    dest = arbol_por_fecha()
    tareas, sueltas = [], []
    for d in sorted(G.iterdir()):
        if not d.is_dir():
            continue
        m = FECHA.search(d.name)
        arch = archivos(d)
        if not arch:
            continue
        candidato = dest.get(m.group(1)) if m else None
        # misma fecha Y misma primera palabra: `F8` con `F8`, no `Fotos` con `CitaMateo`
        if (candidato and candidato != d
                and etiqueta(d.name) == etiqueta(candidato.name)):
            tareas.append((d, candidato, arch))
        else:
            sueltas.append((d, len(arch)))
    return tareas, sueltas


def correr(hacerlo, bitacora):
    tareas, sueltas = plan()
    total = sum(len(a) for _o, _d, a in tareas)
    print(f"  {len(tareas)} carpetas a fusionar · {total:,} archivos")
    print(f"  {len(sueltas)} se quedan en la raiz (no pertenecen a ninguna coleccion)")
    if not tareas:
        return

    if os.stat(G).st_dev != os.stat(tareas[0][0]).st_dev:
        sys.exit("ABORTA: origen y destino en discos distintos.")

    log = open(bitacora, "a", encoding="utf-8") if hacerlo else None
    movidos = choques = 0
    for origen, destino, arch in tareas:
        if not hacerlo:
            print(f"    {len(arch):>6}  {origen.name}")
            print(f"            -> {destino.relative_to(G)}")
            continue
        for f in arch:
            # se respeta la subestructura: lo que estaba en RAW/ sigue en RAW/
            rel = f.relative_to(origen)
            nuevo = destino / rel
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            n = 2
            while nuevo.exists():
                choques += 1
                nuevo = nuevo.parent / f"{nuevo.stem}-{n}{nuevo.suffix}"
                n += 1
            log.write(json.dumps({"de": str(f), "a": str(nuevo)}) + "\n")
            log.flush()
            os.rename(f, nuevo)
            movidos += 1
        # la carpeta vacia que queda se borra, con sus subcarpetas vacias
        for c, dirs, _f in os.walk(origen, topdown=False):
            try:
                os.rmdir(c)
            except OSError:
                pass
        print(f"\r    {movidos:,}/{total:,}", end="", flush=True)

    if hacerlo:
        log.close()
        print(f"\n  movidos {movidos:,}" + (f", {choques} renombrados por repetidos" if choques else ""))
        print(f"  bitacora: {bitacora}   (revertir: --deshacer {bitacora})")
    else:
        print("\n  se quedan en la raiz:")
        for d, n in sueltas:
            print(f"    {n:>6}  {d.name}")
        print("\n  SIMULACRO — agrega --mover para hacerlo.")


def deshacer(bitacora):
    lineas = [json.loads(l) for l in open(bitacora, encoding="utf-8") if l.strip()]
    v = 0
    for r in reversed(lineas):
        if os.path.exists(r["a"]) and not os.path.exists(r["de"]):
            os.makedirs(os.path.dirname(r["de"]), exist_ok=True)
            os.rename(r["a"], r["de"])
            v += 1
    print(f"  volvieron {v:,} archivos")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mover", action="store_true")
    ap.add_argument("--deshacer", metavar="BITACORA")
    a = ap.parse_args()
    if a.deshacer:
        deshacer(a.deshacer)
    else:
        correr(a.mover, f"agrupar-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")


if __name__ == "__main__":
    main()
