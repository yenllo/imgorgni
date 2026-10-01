"""Cruza los nombres de RAW (.ARW) de las tarjetas SD (D:/F:) contra la
base de datos, para saber cuales ya estan respaldados en la biblioteca y
cuales faltan (para no perder fotos de la final de la liga al reusar las
tarjetas como espacio de la balsa).

    python verificar_sd_raws.py
"""
import sqlite3
from pathlib import Path

LISTA = Path("sd_raws.txt")
DB = Path("C:/Renzo/Proyectos/IMGorgni/photos_v2.db")

nombres = {l.strip() for l in LISTA.read_text(encoding="utf-8").splitlines() if l.strip()}
print(f"{len(nombres)} RAWs unicos en las SD", flush=True)

con = sqlite3.connect(DB)
cur = con.cursor()

en_db = set()
faltantes = []
for nombre in nombres:
    cur.execute(
        "SELECT file_path FROM files WHERE file_path LIKE ? LIMIT 1",
        (f"%{nombre}",),
    )
    row = cur.fetchone()
    if row:
        en_db.add(nombre)
    else:
        faltantes.append(nombre)

print(f"\nya respaldados en la biblioteca: {len(en_db)}")
print(f"NO encontrados (posiblemente solo en la SD): {len(faltantes)}")

Path("sd_raws_faltantes.txt").write_text("\n".join(sorted(faltantes)), encoding="utf-8")
print("lista completa de faltantes en sd_raws_faltantes.txt")
if faltantes:
    print("\nprimeros 20 faltantes:")
    for n in sorted(faltantes)[:20]:
        print(" ", n)
