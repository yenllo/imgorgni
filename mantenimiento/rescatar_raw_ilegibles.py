#!/usr/bin/env python3
"""Rescata el JPEG embebido de los RAW ilegibles y manda los RAW a cuarentena.

Estos RAW estan marcados `danado=1` porque LibRaw no los puede revelar: la
miniatura se ve bien pero el archivo no sirve. Lo unico recuperable es el JPEG
que la camara dejo adentro.

    /mnt/fotos/_rescatados/<id>-<nombre>.jpg          <- lo que se salva
    /mnt/fotos/_cuarentena_raw_ilegibles/<nombre>     <- el RAW, por las dudas

El RAW NO se borra: se mueve. En la base queda con la ruta nueva y `borrado=1`,
asi que desaparece de todas las vistas del curador pero se puede deshacer.

Cada movimiento se anota ANTES de tocar la base, igual que organizar_por_evento.

    python rescatar_raw_ilegibles.py            # simulacro
    python rescatar_raw_ilegibles.py --hacerlo
    python rescatar_raw_ilegibles.py --deshacer bitacora.jsonl
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB = os.environ.get("CURADOR_DB", "/home/yenllo/curador/photos_v2.db")
RESCATE = Path(os.environ.get("RESCATE_DIR", "/mnt/fotos/_rescatados"))
CUARENTENA = Path(os.environ.get("CUARENTENA_RAW",
                                 "/mnt/fotos/_cuarentena_raw_ilegibles"))


def candidatos(cx):
    return [(r["id"], r["file_path"]) for r in cx.execute(
        "SELECT id, file_path FROM files "
        " WHERE danado=1 AND COALESCE(borrado,0)=0")]


def embebido(ruta):
    """El JPEG que la camara dejo adentro, o None si no hay uno usable."""
    import rawpy
    from PIL import Image
    try:
        with rawpy.imread(str(ruta)) as r:
            th = r.extract_thumb()
        if th.format != rawpy.ThumbFormat.JPEG:
            return None
        im = Image.open(io.BytesIO(th.data))
        im.load()
        return th.data, im.size
    except Exception:
        return None


def mismo_disco(a, b):
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def correr(cx, hacerlo, bitacora):
    tareas = candidatos(cx)
    print(f"  {len(tareas)} archivos marcados como dañados")
    if not tareas:
        return
    for d in (RESCATE, CUARENTENA):
        d.mkdir(parents=True, exist_ok=True)
    if not mismo_disco(CUARENTENA, os.path.dirname(tareas[0][1])):
        sys.exit("ABORTA: la cuarentena no esta en el mismo disco que las fotos.")

    rescatados = movidos = sin_embebido = ausentes = 0
    grandes = chicos = 0
    log = open(bitacora, "a", encoding="utf-8") if hacerlo else None
    filas = []

    for pid, ruta in tareas:
        if not os.path.exists(ruta):
            ausentes += 1
            continue
        emb = embebido(ruta)
        if emb is None:
            sin_embebido += 1
        else:
            datos, (w, h) = emb
            if w * h >= 10e6:
                grandes += 1
            else:
                chicos += 1
            nombre = re.sub(r"[^A-Za-z0-9._-]", "_", Path(ruta).stem)
            destino_jpg = RESCATE / f"{pid}-{nombre}.jpg"
            if hacerlo and not destino_jpg.exists():
                destino_jpg.write_bytes(datos)
            rescatados += 1

        destino_raw = CUARENTENA / Path(ruta).name
        n = 2
        while destino_raw.exists():
            destino_raw = CUARENTENA / f"{Path(ruta).stem}-{n}{Path(ruta).suffix}"
            n += 1
        if hacerlo:
            log.write(json.dumps({"id": pid, "de": ruta, "a": str(destino_raw)}) + "\n")
            log.flush()
            os.rename(ruta, destino_raw)
            filas.append((str(destino_raw), pid))
        movidos += 1

    if hacerlo and filas:
        # borrado=1 los saca de todas las vistas; la ruta queda valida para
        # poder revertir sin adivinar donde quedo cada uno
        cx.executemany("UPDATE files SET file_path=?, borrado=1 WHERE id=?", filas)
        cx.commit()
        log.close()

    v = "rescataria/moveria" if not hacerlo else "rescato/movio"
    print(f"  {v}: {rescatados} JPEG salvados ({grandes} grandes, {chicos} chicos)")
    print(f"  {v}: {movidos} RAW a cuarentena")
    if sin_embebido:
        print(f"  {sin_embebido} sin embebido usable (se mueven igual)")
    if ausentes:
        print(f"  {ausentes} no estaban en el disco")
    if hacerlo:
        print(f"  rescatados en: {RESCATE}")
        print(f"  cuarentena en: {CUARENTENA}")
        print(f"  bitacora: {bitacora}   (revertir: --deshacer {bitacora})")
    else:
        print("\n  SIMULACRO — agrega --hacerlo para ejecutarlo.")


def deshacer(cx, bitacora):
    lineas = [json.loads(l) for l in open(bitacora, encoding="utf-8") if l.strip()]
    vuelto = 0
    for r in reversed(lineas):
        if os.path.exists(r["a"]) and not os.path.exists(r["de"]):
            os.makedirs(os.path.dirname(r["de"]), exist_ok=True)
            os.rename(r["a"], r["de"])
            cx.execute("UPDATE files SET file_path=?, borrado=0 WHERE id=?",
                       (r["de"], r["id"]))
            vuelto += 1
    cx.commit()
    print(f"  volvieron {vuelto} archivos a su lugar")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hacerlo", action="store_true")
    ap.add_argument("--deshacer", metavar="BITACORA")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()
    cx = sqlite3.connect(a.db, timeout=60)
    cx.row_factory = sqlite3.Row
    if a.deshacer:
        deshacer(cx, a.deshacer)
    else:
        correr(cx, a.hacerlo, f"rescate-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    cx.close()


if __name__ == "__main__":
    main()
