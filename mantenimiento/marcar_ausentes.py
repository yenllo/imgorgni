#!/usr/bin/env python3
"""Marca como ausentes las filas cuyo archivo ya no existe en el NAS.

Son 3.500 que sobrevivieron a la limpieza del 15/10/2026: fotos borradas en
limpiezas viejas (Settimana 2024, OneDrive_bajado, Fotos_Organizadas). Si
quedan activas, cada verificacion del sistema las vuelve a reportar como rotas
para siempre.

Quedan con borrado = 1 (asi el curador no las lista) y con el motivo escrito en
danado_motivo, que es lo que las distingue de las que se marcaron por ser la
copia descartada de un duplicado. Para revertir:

    UPDATE files SET borrado = 0, danado_motivo = NULL
     WHERE danado_motivo = 'ausente-limpieza-20261015';

    python marcar_ausentes.py            # cuenta, no toca
    python marcar_ausentes.py --hacerlo
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
import os
import sqlite3
import sys

from db import verificar_disco_montado

DB = os.path.join(str(_ROOT), "photos_v2.db")
MOTIVO = "ausente-limpieza-20261015"

verificar_disco_montado("K:\\")          # sin disco no se decide nada
hacer = "--hacerlo" in sys.argv
cx = sqlite3.connect(DB, timeout=120)
faltan = [(i,) for i, p in cx.execute(
    "SELECT id, file_path FROM files WHERE COALESCE(borrado,0) = 0")
    if not os.path.exists(p)]
print("filas activas cuyo archivo no esta en el disco: %d" % len(faltan))
if hacer:
    cx.executemany("UPDATE files SET borrado = 1, danado_motivo = ? WHERE id = ?",
                   [(MOTIVO, i[0]) for i in faltan])
    cx.commit()
    cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    act = cx.execute("SELECT COUNT(*) FROM files WHERE COALESCE(borrado,0) = 0").fetchone()[0]
    vivas = sum(1 for (p,) in cx.execute(
        "SELECT file_path FROM files WHERE COALESCE(borrado,0) = 0") if os.path.exists(p))
    print("marcadas %d | activas ahora %d | de esas, con archivo %d" % (len(faltan), act, vivas))
cx.close()
