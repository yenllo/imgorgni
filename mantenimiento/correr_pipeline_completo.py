"""
Corre el pipeline completo de punta a punta sobre K:, donde ya vive todo.
Pensado para dejarlo corriendo desatendido (la prueba real).

Fases (en orden):
  1. Indexado de K:       (hashes, EXIF, GPS, quita duplicados exactos)
  2. Análisis IA          (embeddings, escenas, calidad, pHash)  <- la larga
  5. Duplicados           (reporte de casi-iguales, NO borra)
  3. Eventos y viajes
  3c. OCR de eventos
  4. Export               (solo con --con-export)

No borra nada irreversible: la Fase 1 MUEVE duplicados exactos a
_duplicados_contenido (reversible), y el export usa hardlinks/copias.

    python correr_pipeline_completo.py [--con-export]
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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PY = sys.executable
LOG = Path("pipeline_completo.log")
CFG = "config_K.json"
SALIDA = r"K:\Fotos_Organizadas_IA_v4"

PASOS = [
    ("Fase 1 · indexar K:", [PY, "main.py", "-ph", "1", "-c", CFG]),
    ("Fase 2 · análisis IA", [PY, "main.py", "-ph", "2", "-c", CFG]),
    ("Fase 5 · duplicados", [PY, "5_duplicados.py", "-c", CFG,
                             "--csv", "duplicados.csv"]),
    ("Fase 3 · eventos", [PY, "main.py", "-ph", "3", "-c", CFG]),
    ("Fase 3c · OCR", [PY, "3c_ocr_eventos.py", "-c", CFG, "--aplicar"]),
]

# El export es la unica fase que escribe una biblioteca nueva en disco, y
# rehacerla son cientos de GB: va aparte, no por descuido.
EXPORT = ("Fase 4 · export", [PY, "4_exportar.py", "-c", CFG, "--out", SALIDA])


def log(msg):
    linea = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(linea, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def main():
    pasos = PASOS + ([EXPORT] if "--con-export" in sys.argv else [])
    t0 = time.time()
    log("=" * 60)
    log("PIPELINE COMPLETO — corrida real")
    log("=" * 60)

    for nombre, cmd in pasos:
        log(f"▶ {nombre}")
        inicio = time.time()
        # La salida va al log de la fase en vivo, no capturada: una fase larga
        # tiene que poder mirarse con `tail` mientras corre, no al terminar.
        fase_log = Path(f"fase_{nombre.split()[1].replace(':','')}.log")
        with fase_log.open("w", encoding="utf-8", errors="replace", buffering=1) as fh:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
        dur = time.time() - inicio
        if r.returncode == 0:
            log(f"  ✓ {nombre} — {dur/60:.1f} min")
        else:
            log(f"  ✗ {nombre} terminó con código {r.returncode} — revisá {fase_log}")
            cola = fase_log.read_text(encoding="utf-8", errors="replace")[-300:]
            log(f"    últimas líneas: {cola}")
            # Seguir: una fase que falla no debe frenar todo (ej. OCR opcional)

    log("=" * 60)
    log(f"TERMINADO en {(time.time()-t0)/3600:.1f} horas")
    if pasos is not PASOS and pasos[-1] is EXPORT:
        log(f"Resultado en: {SALIDA}")
    log(f"Duplicados a revisar: duplicados.csv")
    log("=" * 60)


if __name__ == "__main__":
    main()
