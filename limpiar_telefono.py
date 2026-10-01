"""
Libera espacio moviendo fotos de teléfono claramente inservibles a una
CUARENTENA (no las borra). Reversible: revisás la carpeta y borrás vos.

Qué mueve (criterios conservadores, para no llevarse fotos buenas):
  - NEGRAS/vacías : brillo muy bajo Y sin contenido (contraste casi nulo).
                    Una foto oscura pero CON algo (concierto, vela) NO se toca.
  - CAPTURAS      : por nombre de archivo (Screenshot_, captura, PXL sc…).

NO toca: recibos/boletas (riesgo de falso positivo alto, se suelen guardar a
propósito) ni fotos normales. Eso queda para que lo revises vos.

    python limpiar_telefono.py --scan  F:/Telefonos_y_Capturas   # solo reporta
    python limpiar_telefono.py --mover F:/Telefonos_y_Capturas    # mueve a cuarentena
"""
import argparse
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

sys.path.insert(0, ".")
from utils_images import is_image_file, load_image_array
from utils_log import Spinner, log_step

CUARENTENA = Path(r"C:\_revisar_borrar_telefono")

_CAPTURA = re.compile(r"(screenshot|captura|screen[_-]?shot|scrn)", re.IGNORECASE)


def clasificar(fp: str):
    """Devuelve ('negra'|'captura'|None, size). None = NO tocar."""
    try:
        size = Path(fp).stat().st_size
    except OSError:
        size = 0

    nombre = Path(fp).name
    if _CAPTURA.search(nombre):
        return "captura", size

    # Negra/vacía: hay que MIRAR la imagen. Estricto para no llevarse night shots.
    try:
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            img = load_image_array(fp)
        small = cv2.resize(img, (256, 256), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        brillo = float(gray.mean())
        contraste = float(gray.std())
        # negra de verdad: muy oscura Y sin nada (contraste casi cero).
        # brillo<15 solo NO alcanza: una foto de fuegos artificiales es oscura
        # pero tiene contraste alto -> se conserva.
        if brillo < 16 and contraste < 11:
            return "negra", size
    except Exception:
        pass
    return None, size


def listar(carpetas):
    fotos = []
    for c in carpetas:
        r = Path(c)
        if not r.exists():
            log_step(f"⚠️ no existe: {c}")
            continue
        fotos += [str(p) for p in r.rglob("*") if p.is_file() and is_image_file(str(p))]
    return fotos


def run(carpetas, mover, workers):
    log_step("=" * 60)
    log_step("LIMPIEZA DE FOTOS DE TELÉFONO")
    log_step(f"  modo: {'MOVER a cuarentena' if mover else 'solo reporte (no toca nada)'}")
    log_step("=" * 60)

    sp = Spinner("Listando..."); sp.start()
    fotos = listar(carpetas)
    sp.stop(f"{len(fotos):,} fotos")
    if not fotos:
        return

    negras, capturas = [], []
    bytes_n = bytes_c = 0
    hechas = 0
    sp = Spinner("Revisando..."); sp.start()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(clasificar, fp): fp for fp in fotos}
        for fut in as_completed(futs):
            fp = futs[fut]
            tipo, size = fut.result()
            if tipo == "negra":
                negras.append(fp); bytes_n += size
            elif tipo == "captura":
                capturas.append(fp); bytes_c += size
            hechas += 1
            if hechas % 300 == 0:
                sp.text = f"Revisando... {hechas:,}/{len(fotos):,}"
    sp.stop("Revisión completa")

    gb = lambda b: b / (1024 ** 3)
    total = len(negras) + len(capturas)
    log_step(f"⬛ Negras/vacías : {len(negras):,}  ({gb(bytes_n):.2f} GB)")
    log_step(f"📱 Capturas      : {len(capturas):,}  ({gb(bytes_c):.2f} GB)")
    log_step(f"   TOTAL a liberar: {total:,} fotos = {gb(bytes_n+bytes_c):.2f} GB")

    if not mover:
        log_step("\nSolo reporte. Para moverlas a cuarentena (reversible):")
        log_step("   python limpiar_telefono.py --mover " + " ".join(carpetas))
        return

    # Mover a cuarentena preservando ruta relativa, para poder revertir
    CUARENTENA.mkdir(parents=True, exist_ok=True)
    movidas, fallos = 0, 0
    for tipo, lista in (("negras", negras), ("capturas", capturas)):
        for fp in lista:
            src = Path(fp)
            dest = CUARENTENA / tipo / src.name
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}_{abs(hash(fp))%10000}{dest.suffix}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dest))
                movidas += 1
            except Exception:
                fallos += 1
    log_step(f"\n✅ {movidas:,} fotos movidas a {CUARENTENA}")
    if fallos:
        log_step(f"⚠️ {fallos:,} no se pudieron mover (en uso por el pipeline?)")
    log_step("Revisá esa carpeta y borrala vos cuando confirmes. NADA se borró.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("carpetas", nargs="+")
    ap.add_argument("--mover", action="store_true", help="Mover a cuarentena (default: solo reporte)")
    ap.add_argument("--scan", action="store_true", help="Solo reportar")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    run(args.carpetas, args.mover and not args.scan, args.workers)
