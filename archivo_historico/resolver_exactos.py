"""Para los duplicados EXACTOS (mismo SHA256, byte a byte) no hace falta que
decidas nada a mano: todas las copias son idénticas, la única pregunta es
CUÁL ubicación conservar. Este script agrupa por hash, elige la copia que vive
en la carpeta más "sana" (ver `penalizacion_ruta` en 5_duplicados.py: penaliza
_duplicados_contenido, carpetas de descarga, copias re-numeradas, etc.) y deja
todo marcado decision='descartar' contra esa única ganadora.

No mueve archivos: eso lo sigue haciendo el botón "Mover los marcados a
cuarentena" del curador, a mano, como siempre.

Solo toca grupos donde TODAS las filas siguen 'pendiente' -- si ya revisaste
algo a mano, no lo pisa.
"""
import re
import sqlite3
from collections import defaultdict

from web_curador import DB
from importlib import import_module

_dup = import_module("5_duplicados")

RE_HASH = re.compile(r"\(([0-9a-fA-F]{6,})\)")


def clave_grupo(razon):
    m = RE_HASH.search(razon or "")
    return m.group(1).lower() if m else None


def elegir_ganador(rutas):
    """La ruta menos sospechosa; empate: la más corta."""
    return max(rutas, key=lambda r: (-_dup.penalizacion_ruta(r), -len(r)))


def main():
    c = sqlite3.connect(DB, timeout=180)
    filas = c.execute(
        "SELECT id, razon, mb, ruta_conservar, ruta_descartar, id_conservar, "
        "id_descartar, decision FROM duplicados WHERE tipo='EXACTO'").fetchall()

    grupos = defaultdict(list)
    for f in filas:
        clave = clave_grupo(f[1])
        if clave:
            grupos[clave].append(f)

    resueltos = saltados_ya_revisados = sin_hash = 0
    nuevas_filas = []
    ids_borrar = []

    for clave, grupo in grupos.items():
        if any(f[7] != "pendiente" for f in grupo):
            saltados_ya_revisados += 1
            continue

        # Todas las rutas (conservar + descartar) que aparecen en el grupo,
        # con su id y un mb de referencia por si hace falta.
        info = {}
        for f in grupo:
            info[f[3]] = (f[5], info.get(f[3], (None, None))[1])
            info[f[4]] = (f[6], f[2])
        rutas = list(info.keys())
        if len(rutas) < 2:
            continue

        ganador = elegir_ganador(rutas)
        id_ganador = info[ganador][0]
        razon = grupo[0][1]

        for r in rutas:
            if r == ganador:
                continue
            id_r, mb_r = info[r]
            nuevas_filas.append(("EXACTO", razon, mb_r or 0, ganador, r,
                                  id_ganador, id_r, "descartar"))
        ids_borrar.extend(f[0] for f in grupo)
        resueltos += 1

    if ids_borrar:
        c.executemany("DELETE FROM duplicados WHERE id=?", [(i,) for i in ids_borrar])
    if nuevas_filas:
        c.executemany(
            """INSERT INTO duplicados (tipo, razon, mb, ruta_conservar,
               ruta_descartar, id_conservar, id_descartar, decision)
               VALUES (?,?,?,?,?,?,?,?)""", nuevas_filas)
    c.commit()

    print(f"grupos EXACTO resueltos automáticamente : {resueltos:,}")
    print(f"  -> {len(nuevas_filas):,} copias marcadas 'descartar'")
    print(f"grupos salteados (ya tenían una decisión manual): {saltados_ya_revisados:,}")
    c.close()


if __name__ == "__main__":
    main()
