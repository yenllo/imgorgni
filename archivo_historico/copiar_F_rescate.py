"""
Copia TODAS las fotos de F: a destinos seguros (SSD G: + notebook C:).
Rescate: F: viene de un adaptador malo; se lo lee ya por el dock, en frío.

- COPIA (no mueve): deja F: intacto por si hay que reintentar.
- Tolerante a corrupción: carpeta/archivo ilegible se saltea, no frena todo.
- Streaming: lo copiado queda a salvo aunque F: se corte a mitad.
- Llena G: primero (SSD, rápido), después C:. Si se llenan, avisa qué faltó.
- RAW primero (irremplazables), después el resto — por si no entra todo.

    python copiar_F_rescate.py            # copia
    python copiar_F_rescate.py --scan     # solo cuenta cuánto hay y si entra
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from utils_images import is_image_file, SUPPORTED_RAW_EXTS
from utils_log import log_step

ORIGEN = "F:\\"
# Destinos en orden de preferencia (se llena el primero, luego el siguiente).
# G: quedó desconectado con 208 GB ya a salvo; sus archivos se re-copian porque
# no se pueden ver, pero nada se pierde.
DESTINOS = [Path(r"C:\Fotos_F_rescatadas"), Path(r"E:\Fotos_F_rescatadas")]
MARGEN = 3 * 1024 ** 3          # dejar 3 GB libres en cada destino
EXCLUIR = ("$recycle.bin", "system volume information")


def ya_copiado(rel):
    """True si el archivo ya está en algún destino conectado (para no re-copiar)."""
    return any((d / rel).exists() for d in DESTINOS)


def libre(path):
    try:
        return shutil.disk_usage(str(path.anchor)).free
    except Exception:
        return 0


def elegir_destino(size):
    """Primer destino con lugar para un archivo de `size`. None si no hay."""
    for d in DESTINOS:
        if libre(d) - size > MARGEN:
            return d
    return None


def recorrer(solo_raw):
    """Genera rutas de imágenes de F:, tolerante a corrupción."""
    def _ig(err):
        log_step(f"  ⚠️ ilegible (saltado): {getattr(err,'filename','?')}")
    for carpeta, dirs, archivos in os.walk(ORIGEN, onerror=_ig):
        if any(e in carpeta.lower() for e in EXCLUIR):
            dirs[:] = []
            continue
        for nombre in archivos:
            fp = os.path.join(carpeta, nombre)
            if not is_image_file(fp):
                continue
            es_raw = Path(fp).suffix.lower() in SUPPORTED_RAW_EXTS
            if solo_raw and not es_raw:
                continue
            if not solo_raw and es_raw:
                continue           # los RAW ya se copiaron en la 1ª pasada
            yield fp, es_raw


def copiar_lote(solo_raw, mover, stats):
    etiqueta = "RAW" if solo_raw else "resto (JPG/etc.)"
    log_step(f"--- Copiando {etiqueta} ---")
    for fp, _ in recorrer(solo_raw):
        try:
            size = os.path.getsize(fp)
        except OSError:
            stats["ilegibles"] += 1
            continue
        rel = os.path.relpath(fp, ORIGEN)
        if ya_copiado(rel):
            stats["saltados"] += 1
            continue                    # ya está en C: o E:, no re-copiar
        if not mover:
            stats["total"] += 1
            stats["bytes"] += size
            continue
        dest_raiz = elegir_destino(size)
        if dest_raiz is None:
            stats["sin_lugar"] += 1
            continue
        try:
            dest = dest_raiz / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, str(dest))
            stats["copiados"] += 1
            stats["bytes"] += size
            if stats["copiados"] % 300 == 0:
                gb = stats["bytes"] / 1024**3
                libres = " ".join(f"{d.anchor}{libre(d)//1024**3}GB" for d in DESTINOS)
                log_step(f"  ... {stats['copiados']:,} copiadas ({gb:.0f} GB) | libre: {libres}")
        except Exception as e:
            stats["fallos"] += 1
            if stats["fallos"] <= 20:
                log_step(f"  ⚠️ {Path(fp).name}: {e}")


def run(mover):
    log_step("=" * 60)
    log_step("RESCATE DE F:  ->  SSD (G:) + notebook (C:)")
    log_step(f"  modo: {'COPIAR' if mover else 'solo contar'}")
    for d in DESTINOS:
        log_step(f"  destino {d}: {libre(d)//1024**3} GB libres")
    log_step("=" * 60)

    stats = dict(copiados=0, fallos=0, ilegibles=0, sin_lugar=0, saltados=0, total=0, bytes=0)
    t0 = time.time()
    copiar_lote(True, mover, stats)    # RAW primero
    copiar_lote(False, mover, stats)   # después el resto

    dur = (time.time() - t0) / 60
    log_step("=" * 60)
    if mover:
        log_step(f"✅ {stats['copiados']:,} fotos copiadas = {stats['bytes']/1024**3:.1f} GB ({dur:.0f} min)")
        if stats["sin_lugar"]:
            log_step(f"📦 {stats['sin_lugar']:,} NO entraron (destinos llenos) — siguen en F:")
        if stats["ilegibles"] + stats["fallos"]:
            log_step(f"⚠️ {stats['ilegibles']+stats['fallos']:,} ilegibles/corruptas (siguen en F:)")
    else:
        log_step(f"📊 {stats['total']:,} fotos en F: = {stats['bytes']/1024**3:.1f} GB")
        cap = sum(libre(d) for d in DESTINOS) / 1024**3
        log_step(f"   capacidad destinos: {cap:.0f} GB  ->  {'ENTRA TODO' if cap > stats['bytes']/1024**3 else 'NO entra todo, se prioriza RAW'}")
    log_step("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    args = ap.parse_args()
    run(mover=not args.scan)
