"""
Escáner de duplicados independiente: cuenta cuántas fotos y cuántos GB están
repetidos entre varias carpetas, SIN necesitar el pipeline completo.

Pensado para la pregunta "¿cuánto de esto es duplicado?" antes de decidir qué
borrar por espacio. Detecta:
  - EXACTOS      : mismo SHA256 (byte a byte). Rápido, no decodifica.
  - CASI-IGUALES : mismo pHash o cercano (recompresiones, resizes, ediciones).

    python escanear_duplicados.py "F:/" "E:/GDrive..." "E:/Adobe"
    python escanear_duplicados.py --solo-exactos "F:/" "E:/Adobe"   # rápido
    python escanear_duplicados.py --umbral 6 --csv dups.csv  ...
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
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from utils_images import compute_sha256, load_image_array, is_image_file, SUPPORTED_RAW_EXTS
from utils_analisis import phash as calc_phash
from utils_log import Spinner, log_step

# Reusar la lógica de agrupación y "cuál conservar" que ya está probada
import importlib
_dup = importlib.import_module("5_duplicados")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def listar_fotos(raices):
    """Devuelve [(ruta, origen)] de todas las imágenes bajo las raíces dadas."""
    fotos = []
    for raiz in raices:
        r = Path(raiz)
        if not r.exists():
            log_step(f"⚠️ No existe: {raiz}")
            continue
        etiqueta = r.name or str(r)
        for p in r.rglob("*"):
            if p.is_file() and is_image_file(str(p)):
                fotos.append((str(p), etiqueta))
    return fotos


def procesar(ruta, con_phash):
    """(ruta, size, sha256, phash|None). phash None si no se pide o falla."""
    try:
        size = Path(ruta).stat().st_size
    except OSError:
        size = 0
    try:
        sha = compute_sha256(ruta)
    except Exception:
        sha = None
    ph = None
    if con_phash:
        try:
            # rawpy escupe warnings a stderr con archivos corruptos: silenciar
            import contextlib, io
            with contextlib.redirect_stderr(io.StringIO()):
                ph = calc_phash(load_image_array(ruta))
        except Exception:
            ph = None
    return ruta, size, sha, ph


def gb(n):
    return n / (1024 ** 3)


def run(raices, solo_exactos, umbral, workers, salida_csv):
    log_step("=" * 60)
    log_step("ESCÁNER DE DUPLICADOS")
    log_step(f"  carpetas: {', '.join(raices)}")
    log_step(f"  modo: {'solo exactos (rápido)' if solo_exactos else 'exactos + casi-iguales'}")
    log_step("=" * 60)

    sp = Spinner("Listando fotos...")
    sp.start()
    fotos = listar_fotos(raices)
    sp.stop(f"{len(fotos):,} fotos encontradas")
    if not fotos:
        return

    origen = {ruta: org for ruta, org in fotos}
    rutas = [r for r, _ in fotos]

    # --- Hashing en paralelo ---
    datos = {}
    hechas = 0
    sp = Spinner("Calculando hashes...")
    sp.start()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(procesar, r, not solo_exactos): r for r in rutas}
        for fut in as_completed(futs):
            ruta, size, sha, ph = fut.result()
            datos[ruta] = (size, sha, ph)
            hechas += 1
            if hechas % 200 == 0:
                sp.text = f"Calculando hashes... {hechas:,}/{len(rutas):,}"
    sp.stop(f"{len(datos):,} fotos procesadas")

    tam_total = sum(d[0] for d in datos.values())

    # --- 1. Exactos por SHA256 ---
    por_sha = defaultdict(list)
    for ruta, (size, sha, ph) in datos.items():
        if sha:
            por_sha[sha].append(ruta)
    grupos_exactos = {k: v for k, v in por_sha.items() if len(v) > 1}
    descartes_ex = [r for v in grupos_exactos.values() for r in v[1:]]
    bytes_ex = sum(datos[r][0] for r in descartes_ex)

    # --- 2. Casi-iguales por pHash (reusa el multi-index hashing probado) ---
    descartes_ph, bytes_ph = [], 0
    if not solo_exactos:
        # Excluir los ya marcados exactos para no contarlos dos veces
        ya = set(descartes_ex) | {v[0] for v in grupos_exactos.values()}
        cands = []
        for ruta, (size, sha, ph) in datos.items():
            if ph and ruta not in descartes_ex:
                c = _dup.Candidato(0, ruta, size, None, None, ph)
                cands.append(c)
        grupos = _dup.agrupar_por_phash(cands, umbral)
        for g in grupos:
            ordenado = sorted(g, key=lambda c: c.puntaje(), reverse=True)
            for c in ordenado[1:]:
                descartes_ph.append(c.path)
                bytes_ph += c.size

    # --- Reporte ---
    total_dup = len(descartes_ex) + len(descartes_ph)
    bytes_dup = bytes_ex + bytes_ph

    log_step("=" * 60)
    log_step("RESULTADO")
    log_step("=" * 60)
    log_step(f"📷 Fotos escaneadas   : {len(datos):,}  ({gb(tam_total):.1f} GB)")
    log_step(f"🟰 Duplicados EXACTOS  : {len(descartes_ex):,}  ({gb(bytes_ex):.1f} GB)")
    if not solo_exactos:
        log_step(f"≈  Casi-iguales        : {len(descartes_ph):,}  ({gb(bytes_ph):.1f} GB)")
    log_step(f"{'─'*40}")
    log_step(f"♻️  RECUPERABLE TOTAL  : {total_dup:,} fotos = {gb(bytes_dup):.1f} GB")
    pct = bytes_dup / tam_total * 100 if tam_total else 0
    log_step(f"   ({pct:.0f}% del espacio son copias)")

    # --- Desglose por origen: cuántos duplicados aporta cada carpeta ---
    if len(raices) > 1:
        por_origen = defaultdict(lambda: [0, 0])
        for r in descartes_ex + descartes_ph:
            por_origen[origen.get(r, "?")][0] += 1
            por_origen[origen.get(r, "?")][1] += datos[r][0]
        log_step(f"\n   Duplicados por carpeta de origen:")
        for org, (n, b) in sorted(por_origen.items(), key=lambda x: -x[1][1]):
            log_step(f"     {org[:30]:<32} {n:>6,} fotos  {gb(b):>6.1f} GB")

    # --- CSV para revisar antes de borrar ---
    if salida_csv:
        import csv
        with open(salida_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["tipo", "descartar", "origen", "mb", "sha256"])
            for r in descartes_ex:
                w.writerow(["EXACTO", r, origen.get(r, ""),
                            round(datos[r][0] / 1e6, 1), datos[r][1]])
            for r in descartes_ph:
                w.writerow(["CASI-IGUAL", r, origen.get(r, ""),
                            round(datos[r][0] / 1e6, 1), ""])
        log_step(f"\n📋 Detalle en: {salida_csv}  (revisá antes de borrar)")
    log_step("=" * 60)
    log_step("Este script NO borra nada. Es solo el conteo.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Contar duplicados entre carpetas")
    ap.add_argument("carpetas", nargs="+", help="Carpetas a escanear")
    ap.add_argument("--solo-exactos", action="store_true",
                    help="Solo SHA256 (rápido, no decodifica imágenes)")
    ap.add_argument("--umbral", type=int, default=5, help="Distancia pHash (0-7)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--csv", default="duplicados_escaneo.csv")
    args = ap.parse_args()
    run(args.carpetas, args.solo_exactos, args.umbral, args.workers, args.csv)
