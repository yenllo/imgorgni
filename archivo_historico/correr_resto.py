"""
Corre desde la Fase 2 en adelante, sobre lo YA indexado en la base de datos.
Salta la Fase 1 (el escaneo de disco), que se colgaba en F: por corrupción.

La Fase 2 trabaja desde la BD (needs_analysis=1), no recorre el disco, así que
esquiva el cuelgue. Lee cada foto individual con manejo de error por archivo.

    python correr_resto.py
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PY = sys.executable
LOG = Path("pipeline_resto.log")
SALIDA = r"E:\Fotos_Organizadas_IA_v3"

PASOS = [
    ("Fase 2 · análisis IA", [PY, "main.py", "-ph", "2", "-c", "config_local.json"]),
    ("Fase 5 · duplicados", [PY, "5_duplicados.py", "-c", "config_local.json",
                             "--csv", "duplicados.csv"]),
    ("Fase 3 · eventos", [PY, "main.py", "-ph", "3", "-c", "config_local.json"]),
    ("Fase 3c · OCR", [PY, "3c_ocr_eventos.py", "-c", "config_local.json", "--aplicar"]),
]
# La Fase 4 lee las fotos de teléfono que siguen en F:. Mientras F: esté trabada
# cuelga el proceso entero, así que va aparte y solo si se pide.
EXPORT = ("Fase 4 · export", [PY, "4_exportar.py", "-c", "config_local.json",
                              "--out", SALIDA])


def log(msg):
    linea = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(linea, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--con-export", action="store_true",
                    help="incluir la Fase 4 (requiere que F: no esté trabada)")
    args = ap.parse_args()

    pasos = PASOS + ([EXPORT] if args.con_export else [])
    t0 = time.time()
    log("=" * 60)
    log("PIPELINE (desde Fase 2) — sobre lo ya indexado")
    log("=" * 60)
    for nombre, cmd in pasos:
        log(f"▶ {nombre}")
        inicio = time.time()
        # Escribe el log de la fase EN VIVO: si una fase se cuelga, se puede ver
        # en qué archivo quedó. Con capture_output la salida queda invisible.
        salida = Path(f"resto_{nombre.split()[1].replace(':','')}.log")
        with salida.open("w", encoding="utf-8", errors="replace") as fh:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
        dur = time.time() - inicio
        if r.returncode == 0:
            log(f"  ✓ {nombre} — {dur/60:.1f} min")
        else:
            cola = salida.read_text(encoding="utf-8", errors="replace")[-250:]
            log(f"  ✗ {nombre} código {r.returncode}")
            log(f"    {cola}")
    log("=" * 60)
    log(f"TERMINADO en {(time.time()-t0)/3600:.1f} h")
    if not args.con_export:
        log("  (sin Fase 4 — falta el export; ver --con-export)")
    log("=" * 60)


if __name__ == "__main__":
    main()
