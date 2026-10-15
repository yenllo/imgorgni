#!/usr/bin/env python3
"""Comprueba que la tabla tag_foto da los mismos resultados que el LIKE viejo.

El curador dejo de filtrar con `scene_tags LIKE '%"tag"%'` y ahora usa la tabla
derivada `tag_foto`. Este script corre las dos formas sobre la misma base y
compara: si alguna difiere, algo se rompio y hay que volver atras.

    python verificar_tags.py                 # sobre photos_v2.db del proyecto
    python verificar_tags.py otra_base.db
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent / "photos_v2.db")


def main():
    cx = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    if not cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='tag_foto'").fetchone():
        sys.exit("no existe tag_foto: arranca el curador una vez para que la construya")

    # las mas usadas mas una rara: los dos extremos del indice
    tags = [r[0] for r in cx.execute(
        "SELECT tag FROM tag_foto GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 5")]
    raras = [r[0] for r in cx.execute(
        "SELECT tag FROM tag_foto GROUP BY tag ORDER BY COUNT(*) ASC LIMIT 2")]
    tags += [t for t in raras if t not in tags]

    print(f"{'etiqueta':<34} {'LIKE':>9} {'tag_foto':>9}  {'lento':>8} {'rapido':>8}")
    fallos = 0
    for t in tags:
        t0 = time.perf_counter()
        viejo = cx.execute(
            "SELECT COUNT(*) FROM files f JOIN photo_features pf ON pf.photo_id=f.id "
            "WHERE COALESCE(f.borrado,0)=0 AND pf.scene_tags LIKE ?",
            (f'%"{t}"%',)).fetchone()[0]
        lento = time.perf_counter() - t0

        t0 = time.perf_counter()
        nuevo = cx.execute(
            "SELECT COUNT(*) FROM files f WHERE COALESCE(f.borrado,0)=0 "
            "AND EXISTS (SELECT 1 FROM tag_foto tf "
            "            WHERE tf.photo_id=f.id AND tf.tag=?)", (t,)).fetchone()[0]
        rapido = time.perf_counter() - t0

        marca = "" if viejo == nuevo else "  <-- DIFIERE"
        fallos += viejo != nuevo
        print(f"{t[:33]:<34} {viejo:>9} {nuevo:>9}  {lento:>7.2f}s {rapido:>7.3f}s{marca}")

    # la tabla no puede haber perdido ni inventado etiquetas
    del_json = set()
    for (s,) in cx.execute("SELECT scene_tags FROM photo_features WHERE scene_tags IS NOT NULL"):
        try:
            del_json.update(x for x in json.loads(s) if isinstance(x, str))
        except (json.JSONDecodeError, TypeError):
            pass
    de_tabla = {r[0] for r in cx.execute("SELECT DISTINCT tag FROM tag_foto")}
    faltan, sobran = del_json - de_tabla, de_tabla - del_json
    print(f"\netiquetas distintas: {len(del_json)} en scene_tags, {len(de_tabla)} en tag_foto")
    if faltan:
        print(f"  FALTAN en la tabla: {sorted(faltan)[:5]}")
    if sobran:
        print(f"  SOBRAN en la tabla: {sorted(sobran)[:5]}")
    fallos += bool(faltan) + bool(sobran)

    cx.close()
    if fallos:
        sys.exit(f"\n{fallos} comprobaciones fallaron: NO uses esta version")
    print("\nok: la tabla da los mismos resultados que el LIKE")


if __name__ == "__main__":
    main()
