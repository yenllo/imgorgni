"""Restaura de cuarentena los pares donde la copia 'conservada' resulto
faltante o corrupta (detectados por verificar_cuarentena.py). El archivo
descartado vuelve a su ubicacion original y el par queda 'pendiente' para
revision manual -- no confiamos en la decision automatica para estos.

    python rescatar_cuarentena.py
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
import json
import shutil
import sqlite3
from pathlib import Path

CUARENTENA = Path(r"E:\_duplicados_cuarentena")

with open("verificacion_cuarentena_problemas.json", encoding="utf-8") as f:
    problemas = json.load(f)

todos = problemas["faltantes"] + problemas["corruptos"]
print(f"pares a rescatar: {len(todos)}")

c = sqlite3.connect("C:/Renzo/Proyectos/IMGorgni/photos_v2.db", timeout=30)
restaurados = fallos = 0
for dup_id, id_conservar_orig, ruta_conservar_orig, ruta_en_cuarentena in todos:
    row = c.execute("SELECT id, file_path FROM files WHERE file_path=?",
                     (ruta_en_cuarentena,)).fetchone()
    if not row:
        # la ruta cacheada puede estar vieja: buscar por id_descartar del par
        d = c.execute("SELECT id_descartar FROM duplicados WHERE id=?", (dup_id,)).fetchone()
        row = c.execute("SELECT id, file_path FROM files WHERE id=?", (d[0],)).fetchone() if d else None
    if not row or not Path(row[1]).exists():
        fallos += 1
        print(f"  no se encontro el archivo en cuarentena para dup_id={dup_id}")
        continue

    fid, ruta_actual = row
    try:
        rel = Path(ruta_actual).relative_to(CUARENTENA)
        destino = Path(rel.parts[0] + ":\\" + "\\".join(rel.parts[1:]))
    except ValueError:
        fallos += 1
        continue

    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino = destino.with_name(f"{destino.stem}_rescatado{destino.suffix}")
    shutil.move(ruta_actual, str(destino))
    c.execute("UPDATE files SET file_path=? WHERE id=?", (str(destino), fid))
    c.execute("UPDATE duplicados SET decision='pendiente', ruta_descartar=? WHERE id=?",
              (str(destino), dup_id))
    restaurados += 1

c.commit()
print(f"restaurados: {restaurados}")
print(f"fallos (revisar a mano): {fallos}")
