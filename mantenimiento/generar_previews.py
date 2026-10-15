#!/usr/bin/env python3
"""Pre-genera las miniaturas y las vistas de todo lo que vive en `galeria/`.

Escribe en las MISMAS carpetas y con los MISMOS tamaños que ya usa el curador:

    ~/curador/_thumbs/<photo_id>.jpg    320px   <- la grilla
    ~/curador/_vistas/<photo_id>.jpg   1600px   <- el visor

Por eso no hace falta tocar el curador: sus rutas /thumb y /vista hacen
`if not existe: generar`, asi que encontrarlas ya hechas es todo lo que
necesitan. Las dos carpetas estan fuera de `galeria/`, que queda solo con
originales.

Solo procesa fotos cuya ruta este bajo `galeria/`, o sea las de los eventos
confirmados que ya movio organizar_por_evento.py. El resto se sigue generando a
demanda la primera vez que lo abris.

DE LOS RAW SE EXTRAE EL JPEG EMBEBIDO, no se decodifican. Medido en la Pi el
21/8/2026: un CR2 de 17,8 MB tarda 0,025 s extrayendo contra 1,736 s
decodificando — 69 veces mas rapido — y el embebido viene en resolucion
completa (5184x3456). Sony es la excepcion: el ARW embebe 1616x1080, que
alcanza justo para la vista de 1600 y de sobra para la miniatura.

    python generar_previews.py                  # simulacro: cuenta y estima
    python generar_previews.py --generar
    python generar_previews.py --generar --workers 4
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
import argparse
import io
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DB = os.environ.get("CURADOR_DB", "/home/yenllo/curador/photos_v2.db")
GALERIA = Path(os.environ.get("GALERIA_EVENTOS", "/mnt/fotos/galeria"))
RAIZ = Path(os.environ.get("CURADOR_RAIZ", "/home/yenllo/curador"))

# Las mismas carpetas y tamaños que el curador. Si algun dia cambian alla,
# cambian aca: son las variables de entorno que el propio curador respeta.
DESTINOS = (
    (Path(os.environ.get("CURADOR_THUMBS", RAIZ / "_thumbs")), 320),
    (Path(os.environ.get("CURADOR_VISTAS", RAIZ / "_vistas")), 1600),
)
RAW_EXT = (".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2")


def abrir(ruta):
    """La imagen mas grande que se pueda sacar barato.

    De un RAW, el JPEG que la camara ya dejo adentro. Si ese thumb viene como
    bitmap en vez de JPEG (pasa en algunos modelos), cae al decodificado a media
    resolucion, que igual es el doble de rapido que el completo.
    """
    from PIL import Image, ImageOps
    if Path(ruta).suffix.lower() in RAW_EXT:
        import rawpy
        with rawpy.imread(str(ruta)) as r:
            try:
                th = r.extract_thumb()
            except Exception:
                th = None
            if th is not None and th.format == rawpy.ThumbFormat.JPEG:
                return ImageOps.exif_transpose(Image.open(io.BytesIO(th.data)))
            arr = r.postprocess(half_size=True, use_camera_wb=True)
        return Image.fromarray(arr)
    return ImageOps.exif_transpose(Image.open(ruta))


def una(pid, ruta):
    """(pid, cuantas escribio, error). Las que ya estan no se rehacen."""
    faltan = [(d, px) for d, px in DESTINOS if not (d / f"{pid}.jpg").exists()]
    if not faltan:
        return pid, 0, None
    try:
        im = abrir(ruta).convert("RGB")
    except Exception as e:
        return pid, 0, f"{type(e).__name__}: {e}"
    hechas = 0
    for d, px in faltan:
        try:
            chico = im.copy()
            chico.thumbnail((px, px))
            d.mkdir(parents=True, exist_ok=True)
            chico.save(d / f"{pid}.jpg", "JPEG", quality=85,
                       optimize=True, progressive=True)
            hechas += 1
        except Exception as e:
            return pid, hechas, f"{type(e).__name__}: {e}"
    return pid, hechas, None


def pendientes(cx):
    """Las fotos que viven bajo galeria/ y que existen de verdad en el disco."""
    filas = cx.execute(
        "SELECT id, file_path FROM files "
        " WHERE COALESCE(borrado,0)=0 AND file_path LIKE ?",
        (str(GALERIA) + "%",)).fetchall()
    return [(r[0], r[1]) for r in filas if os.path.exists(r[1])]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generar", action="store_true", help="hacerlo de verdad")
    ap.add_argument("--workers", type=int, default=4,
                    help="hilos (default 4, los nucleos de la Pi)")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    cx = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    tareas = pendientes(cx)
    cx.close()
    if not tareas:
        sys.exit(f"  no hay nada bajo {GALERIA}.\n"
                 "  Corriste organizar_por_evento.py --mover?")

    crudas = sum(1 for _, p in tareas if Path(p).suffix.lower() in RAW_EXT)
    print(f"  {len(tareas):,} fotos bajo {GALERIA}"
          f"  ({crudas:,} RAW, {len(tareas)-crudas:,} el resto)")
    for d, px in DESTINOS:
        ya = sum(1 for _ in d.iterdir()) if d.exists() else 0
        print(f"    {px:>4}px -> {d}  ({ya:,} ya generadas)")
    est = (crudas * 0.05 + (len(tareas) - crudas) * 0.065) / max(1, a.workers)
    print(f"  estimado con {a.workers} hilos: {est/60:.0f} min")
    if not a.generar:
        print("\n  SIMULACRO — agrega --generar para hacerlo.")
        return

    t0 = time.perf_counter()
    hechas = saltadas = fallidas = 0
    errores = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(una, pid, ruta) for pid, ruta in tareas]
        for i, f in enumerate(as_completed(futs), 1):
            pid, n, err = f.result()
            if err:
                fallidas += 1
                if len(errores) < 10:
                    errores.append(f"{pid}: {err}")
            elif n:
                hechas += 1
            else:
                saltadas += 1
            if i % 200 == 0:
                pas = time.perf_counter() - t0
                print(f"\r  {i:,}/{len(tareas):,}  {pas/60:.0f} min, "
                      f"faltan ~{pas/i*(len(tareas)-i)/60:.0f}", end="", flush=True)

    print(f"\n  generadas {hechas:,} · ya estaban {saltadas:,} · fallaron {fallidas:,}"
          f"  en {(time.perf_counter()-t0)/60:.1f} min")
    for e in errores:
        print("    " + e)
    for d, px in DESTINOS:
        if d.exists():
            arch = list(d.iterdir())
            gb = sum(f.stat().st_size for f in arch) / 1073741824
            print(f"    {px:>4}px: {len(arch):,} archivos, {gb:.1f} GB")


if __name__ == "__main__":
    main()
