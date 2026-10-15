
import sys
from pathlib import Path as _Path

# Este script vive en mantenimiento/ pero los modulos (db.py, utils_*) y los
# datos (photos_v2.db, _salidas_locales/) viven en la raiz del repo.
_ROOT = _Path(__file__).resolve().parent.parent
_HERE = _Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
r"""
Mete el resultado del barrido de integridad en la BD (files.danado).

El barrido de 2026-08-01/02 dejó el mapa del daño en dos CSV y nada más. Eso es
lo que obligó a re-descubrirlo a mano cada vez: la Fase 5 elegía qué copia
conservar sin saber cuál estaba rota, y salieron 594 pares donde se conservaba
la dañada y se descartaba la sana. Con la columna en la base, puntaje() la usa
como primer criterio y el error no se puede repetir.

    python cargar_integridad.py                  # carga los CSV de siempre
    python cargar_integridad.py otro.csv ...     # o los que le pases

Los CSV solo listan archivos DAÑADOS, así que todo lo que no aparece queda en 0.
Se puede volver a correr después de cada verificación: es idempotente.
"""
import csv
import sqlite3
import sys

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
CSVS = ("integridad_completa.csv", "raw_rotos.csv")


# Etiquetas cortas para poder filtrar en el curador. La diferencia no es
# cosmética: un JPEG truncado SE VE roto en la miniatura, pero un RAW muestra su
# preview incrustado, que suele estar sano aunque los datos del sensor no lo
# esten. Sin distinguirlos, revisar RAW a ojo da falsa tranquilidad.
JPEG_VIEJO = "JPEG truncado (ya venía roto)"
JPEG_NUEVO = "JPEG truncado (se rompió después)"
RAW_MUERTO = "RAW no decodifica (la miniatura engaña)"


def categoria(estado):
    if estado.startswith("NO decodifica"):
        return RAW_MUERTO
    if "hash distinto" in estado:
        return JPEG_NUEVO
    return JPEG_VIEJO


def ids_danados(rutas_csv):
    """{files.id: motivo} de lo que el barrido marcó como roto. Se ignoran las
    filas sin id (archivos que ya no estaban)."""
    ids = {}
    for ruta in rutas_csv:
        try:
            with open(ruta, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("id") or "").strip().isdigit():
                        ids[int(r["id"])] = categoria(r.get("estado", ""))
        except FileNotFoundError:
            print(f"  (falta {ruta}, se saltea)")
    return ids


def cargar(c, ids):
    cols = {r[1] for r in c.execute("PRAGMA table_info(files)")}
    if "danado" not in cols:
        c.execute("ALTER TABLE files ADD COLUMN danado INTEGER DEFAULT 0")
        print("  + files.danado")
    if "danado_motivo" not in cols:
        c.execute("ALTER TABLE files ADD COLUMN danado_motivo TEXT")
        print("  + files.danado_motivo")
    # Se reescribe la columna entera, no solo los rotos: si un archivo se
    # recuperó desde OneDrive y ya no está en el CSV, tiene que volver a 0.
    c.execute("UPDATE files SET danado=0, danado_motivo=NULL WHERE COALESCE(danado,0)<>0")
    c.executemany("UPDATE files SET danado=1, danado_motivo=? WHERE id=?",
                  [(m, i) for i, m in ids.items()])
    c.execute("CREATE INDEX IF NOT EXISTS ix_files_danado ON files(danado)")
    c.commit()
    return c.execute("SELECT COUNT(*) FROM files WHERE danado=1").fetchone()[0]


def main():
    rutas = sys.argv[1:] or list(CSVS)
    ids = ids_danados(rutas)
    c = sqlite3.connect(DB, timeout=60)
    n = cargar(c, ids)
    total = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    c.close()
    print(f"{n:,} archivos marcados como dañados de {total:,} ({100*n/max(total,1):.1f}%)")


def demo():
    """Chequeo mínimo: marca, desmarca y no toca lo que no está en la lista."""
    assert categoria("NO decodifica") == RAW_MUERTO
    assert categoria("truncado + hash distinto (se rompio despues)") == JPEG_NUEVO
    assert categoria("truncado + hash OK (ya venia roto)") == JPEG_VIEJO

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, file_path TEXT)")
    c.executemany("INSERT INTO files (id, file_path) VALUES (?,?)",
                  [(1, "a"), (2, "b"), (3, "c")])
    assert cargar(c, {1: RAW_MUERTO, 3: JPEG_VIEJO}) == 2
    assert [r[0] for r in c.execute("SELECT id FROM files WHERE danado=1")] == [1, 3]
    assert c.execute("SELECT danado_motivo FROM files WHERE id=1").fetchone()[0] == RAW_MUERTO
    # segunda corrida con menos rotos: el 3 se recuperó y vuelve a sano
    assert cargar(c, {1: RAW_MUERTO}) == 1
    assert [r[0] for r in c.execute("SELECT id FROM files WHERE danado=1")] == [1]
    assert c.execute("SELECT danado_motivo FROM files WHERE id=3").fetchone()[0] is None, \
        "al recuperarse tiene que perder tambien el motivo"
    print("OK: carga, recarga, guarda el motivo y limpia los que ya no están rotos")


if __name__ == "__main__":
    (demo if "--demo" in sys.argv else main)()
