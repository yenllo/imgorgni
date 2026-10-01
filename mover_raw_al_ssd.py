"""
Mueve los RAW de F: que NO se analizaron (no están en la BD) al SSD nuevo (G:).
Cuando el SSD se llena, el resto queda en F:.

Rescate de un disco que falla: lee F: carpeta por carpeta con tolerancia a
corrupción, y mueve en streaming (lo movido queda a salvo aunque F: se cuelgue).

    python mover_raw_al_ssd.py            # mueve
    python mover_raw_al_ssd.py --scan     # solo cuenta, no mueve
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from db import get_conn
from utils_images import SUPPORTED_RAW_EXTS
from utils_log import log_step

ORIGEN = "F:\\"
DESTINO = Path(r"G:\RAW_no_analizados")   # SSD de 240 GB
CONFIG = "config_local.json"
MARGEN_BYTES = 5 * 1024 ** 3               # dejar 5 GB libres en el SSD

EXCLUIR = ("_duplicados_contenido", "_descartadas", "_organizadas",
           "fotos_organizadas_ia", "$recycle.bin", "system volume information",
           "gdrive_midriveycomputadoras")


def indexadas_de_F():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    conn = get_conn(cfg["db_path"], read_only=True)
    s = {p.lower() for (p,) in
         conn.execute("SELECT file_path FROM files WHERE file_path LIKE 'F:%'")}
    conn.close()
    return s


def es_raw(fp):
    return Path(fp).suffix.lower() in SUPPORTED_RAW_EXTS


def libre_ssd():
    try:
        return shutil.disk_usage(str(DESTINO.anchor)).free
    except Exception:
        return 0


def run(mover):
    log_step("=" * 60)
    log_step(f"RAW no analizados de F:  ->  {DESTINO}")
    log_step(f"  modo: {'MOVER' if mover else 'solo contar'}")
    log_step("=" * 60)

    ya = indexadas_de_F()
    log_step(f"  {len(ya):,} fotos de F: ya en la BD (esas se dejan)")

    if mover:
        DESTINO.mkdir(parents=True, exist_ok=True)
        log_step(f"  SSD G: libre: {libre_ssd()/1024**3:.0f} GB")

    def _ignorar(err):
        log_step(f"  ⚠️ carpeta ilegible (saltada): {getattr(err,'filename','?')}")

    n_mov, n_fallo, n_quedan_F, bytes_mov = 0, 0, 0, 0
    ssd_lleno = False
    t0 = time.time()

    for carpeta, _dirs, archivos in os.walk(ORIGEN, onerror=_ignorar):
        if any(e in carpeta.lower() for e in EXCLUIR):
            _dirs[:] = []
            continue
        for nombre in archivos:
            fp = os.path.join(carpeta, nombre)
            if not es_raw(fp):
                continue
            if fp.lower() in ya:
                continue            # ya procesada, se deja
            # RAW no analizado
            if not mover:
                n_mov += 1
                try:
                    bytes_mov += os.path.getsize(fp)
                except OSError:
                    pass
                continue
            try:
                size = os.path.getsize(fp)
            except OSError:
                n_fallo += 1
                continue
            # ¿entra en el SSD?
            if ssd_lleno or libre_ssd() - size < MARGEN_BYTES:
                if not ssd_lleno:
                    log_step(f"  ⛔ SSD casi lleno -> el resto se queda en F:")
                    ssd_lleno = True
                n_quedan_F += 1
                continue
            try:
                dest = DESTINO / os.path.relpath(fp, ORIGEN)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(fp, str(dest))
                n_mov += 1
                bytes_mov += size
                if n_mov % 200 == 0:
                    log_step(f"  ... {n_mov:,} RAW movidos ({bytes_mov/1024**3:.1f} GB, "
                             f"SSD libre {libre_ssd()/1024**3:.0f} GB)")
            except Exception as e:
                n_fallo += 1
                if n_fallo <= 20:
                    log_step(f"  ⚠️ {nombre}: {e}")

    dur = (time.time() - t0) / 60
    log_step("=" * 60)
    verbo = "movidos al SSD" if mover else "a mover"
    log_step(f"✅ {n_mov:,} RAW {verbo}  =  {bytes_mov/1024**3:.1f} GB   ({dur:.0f} min)")
    if n_quedan_F:
        log_step(f"📦 {n_quedan_F:,} RAW se quedan en F: (no entraron en el SSD)")
    if n_fallo:
        log_step(f"⚠️ {n_fallo:,} ilegibles/corruptos (siguen en F:)")
    log_step("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    args = ap.parse_args()
    run(mover=not args.scan)
