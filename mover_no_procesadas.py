"""
Mueve a E: las fotos de F: que NO pasaron por el pipeline (no están en la BD).

Pensado para rescatar de un disco que falla: lee F: carpeta por carpeta con
tolerancia a corrupción (una carpeta ilegible se saltea, no frena todo), y
mueve las no indexadas a E:\\Fotos_no_procesadas preservando la estructura.

Es streaming: mueve cada archivo apenas lo encuentra, así si F: se cuelga a
mitad, lo ya movido queda a salvo. Y libera espacio en F: sobre la marcha.

    python mover_no_procesadas.py                 # mueve
    python mover_no_procesadas.py --scan          # solo cuenta, no mueve
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from db import get_conn
from utils_images import is_image_file
from utils_log import log_step

ORIGEN = "F:\\"
DESTINO = Path(r"E:\Fotos_no_procesadas")
CONFIG = "config_local.json"

# Carpetas que NO se tocan: ya fueron manejadas por el pipeline o son de sistema
EXCLUIR = ("_duplicados_contenido", "_descartadas", "_organizadas",
           "fotos_organizadas_ia", "$recycle.bin", "system volume information",
           "gdrive_midriveycomputadoras")


def indexadas_de_F(config_path):
    """Set de rutas de F: que YA están en la BD (normalizadas a minúscula)."""
    import json
    cfg = json.load(open(config_path, encoding="utf-8"))
    conn = get_conn(cfg["db_path"], read_only=True)
    s = set()
    for (p,) in conn.execute("SELECT file_path FROM files WHERE file_path LIKE 'F:%'"):
        s.add(p.lower())
    conn.close()
    return s


def excluida(ruta_low):
    return any(e in ruta_low for e in EXCLUIR)


def run(mover):
    log_step("=" * 60)
    log_step("MOVER FOTOS NO PROCESADAS  (F: -> E:\\Fotos_no_procesadas)")
    log_step(f"  modo: {'MOVER' if mover else 'solo contar'}")
    log_step("=" * 60)

    log_step("Cargando lo ya indexado de F: desde la BD...")
    ya = indexadas_de_F(CONFIG)
    log_step(f"  {len(ya):,} fotos de F: ya procesadas (se dejan donde están)")

    if mover:
        DESTINO.mkdir(parents=True, exist_ok=True)

    def _ignorar(err):
        log_step(f"  ⚠️ carpeta ilegible (saltada): {getattr(err,'filename','?')}")

    n_mov, n_fallo, bytes_mov = 0, 0, 0
    t0 = time.time()
    for carpeta, _dirs, archivos in os.walk(ORIGEN, onerror=_ignorar):
        cl = carpeta.lower()
        if excluida(cl):
            _dirs[:] = []          # no bajar a carpetas excluidas
            continue
        for nombre in archivos:
            fp = os.path.join(carpeta, nombre)
            if not is_image_file(fp):
                continue
            if fp.lower() in ya:
                continue           # ya procesada
            # no procesada -> mover
            if not mover:
                n_mov += 1
                try:
                    bytes_mov += os.path.getsize(fp)
                except OSError:
                    pass
                continue
            try:
                rel = os.path.relpath(fp, ORIGEN)
                dest = DESTINO / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                size = os.path.getsize(fp)
                shutil.move(fp, str(dest))
                n_mov += 1
                bytes_mov += size
            except Exception as e:
                n_fallo += 1
                if n_fallo <= 20:
                    log_step(f"  ⚠️ no se pudo mover {nombre}: {e}")
            if n_mov % 500 == 0:
                log_step(f"  ... {n_mov:,} movidas ({bytes_mov/1024**3:.1f} GB)")

    dur = (time.time() - t0) / 60
    log_step("=" * 60)
    verbo = "movidas" if mover else "a mover"
    log_step(f"✅ {n_mov:,} fotos {verbo}  =  {bytes_mov/1024**3:.1f} GB   ({dur:.0f} min)")
    if n_fallo:
        log_step(f"⚠️ {n_fallo:,} no se pudieron mover (corruptas/ilegibles)")
    if mover:
        log_step(f"Quedaron en: {DESTINO}")
        log_step("(Las que fallaron siguen en F: — probablemente son los archivos dañados)")
    else:
        log_step("Fue solo conteo. Para moverlas: python mover_no_procesadas.py")
    log_step("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="solo contar, no mover")
    args = ap.parse_args()
    run(mover=not args.scan)
