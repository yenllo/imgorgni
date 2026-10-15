"""Genera un inventario de K: (ruta relativa + tamano) para poder
comparar despues contra E: sin necesitar los dos discos conectados a
la vez (comparten la unica bahia del dock).

    python generar_inventario_K.py
"""
import sys
import time
from pathlib import Path

RAIZ = Path("K:/")
# fechado: cada inventario es un estado del disco, no se pisan entre si
SALIDA = Path(sys.argv[1] if len(sys.argv) > 1
              else f"inventario_K-{time.strftime('%Y%m%d-%H%M')}.txt")

print("recorriendo K:...", flush=True)
count = 0
with open(SALIDA, "w", encoding="utf-8") as f:
    for p in RAIZ.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(RAIZ)
        size = p.stat().st_size
        f.write(f"{size}\t{rel}\n")
        count += 1
        if count % 10000 == 0:
            print(f"  {count:,} archivos", flush=True)

print(f"listo: {count:,} archivos en {SALIDA}")
