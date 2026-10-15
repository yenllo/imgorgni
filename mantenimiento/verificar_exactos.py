r"""
Rehashea los dos lados de cada par EXACTO y confirma que sigan siendo identicos.

El cartel "SHA256 identico" y el boton "marcar todos - byte a byte: seguro" se
apoyan en el hash que se calculo al indexar. Si un archivo se daño despues, el
par sigue diciendo "identico" pero ya no lo es, y descartarlo a ciegas tira la
unica copia buena.

    python verificar_exactos.py                 # solo informa
    python verificar_exactos.py --marcar        # los que ya no coinciden pasan
                                                # a pendiente y dejan de ser EXACTO

Cada archivo se lee una sola vez aunque aparezca en varios pares.
"""
import argparse
import hashlib
import os
import sqlite3
import sys
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"

IGUALES = "siguen identicos"
DISTINTOS = "YA NO son identicos"
FALTA = "falta alguno de los dos"


def sha256(path, bloque=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(bloque), b""):
            h.update(b)
    return h.hexdigest()


def clasificar(pares, hashes):
    """pares: (id, ruta_a, ruta_b). hashes: ruta -> sha o None si no se pudo."""
    salida = {IGUALES: [], DISTINTOS: [], FALTA: []}
    for pid, a, b in pares:
        ha, hb = hashes.get(a), hashes.get(b)
        if ha is None or hb is None:
            salida[FALTA].append((pid, a, b))
        elif ha == hb:
            salida[IGUALES].append((pid, a, b))
        else:
            salida[DISTINTOS].append((pid, a, b))
    return salida


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marcar", action="store_true",
                    help="sacar de EXACTO los pares que ya no coinciden")
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    pares = [(r["id"], r["a"], r["b"]) for r in c.execute("""
        SELECT d.id, fa.file_path a, fb.file_path b
        FROM duplicados d
        JOIN files fa ON fa.id = d.id_conservar
        JOIN files fb ON fb.id = d.id_descartar
        WHERE d.tipo = 'EXACTO'""")]
    print(f"pares EXACTO: {len(pares):,}")

    rutas = sorted({r for _, a, b in pares for r in (a, b)})
    total_mb = sum(os.path.getsize(r) for r in rutas if os.path.exists(r)) / 1024**2
    print(f"archivos distintos a leer: {len(rutas):,} ({total_mb:,.0f} MB)\n")

    hashes, t0 = {}, time.time()
    for i, r in enumerate(rutas, 1):
        try:
            hashes[r] = sha256(r) if os.path.exists(r) else None
        except OSError:
            hashes[r] = None
        if i % 200 == 0:
            hecho = time.time() - t0
            print(f"  {i:,}/{len(rutas):,}  ({hecho:.0f}s)", file=sys.stderr)

    res = clasificar(pares, hashes)
    print(f"leidos en {time.time()-t0:.0f}s\n")
    for k in (IGUALES, DISTINTOS, FALTA):
        print(f"  {len(res[k]):5,}  {k}")

    if res[DISTINTOS]:
        print(f"\n--- los {len(res[DISTINTOS]):,} que ya no son identicos ---")
        for pid, a, b in res[DISTINTOS][:30]:
            print(f"  duplicados.id={pid}")
            print(f"     conserva : {a}")
            print(f"     descarta : {b}")
        if len(res[DISTINTOS]) > 30:
            print(f"  ... y {len(res[DISTINTOS])-30} mas")

    if args.marcar and res[DISTINTOS]:
        ids = [(p,) for p, _, _ in res[DISTINTOS]]
        c.executemany("""UPDATE duplicados
                         SET tipo='REVISAR', decision='pendiente',
                             razon='era EXACTO pero los archivos ya no coinciden'
                         WHERE id=?""", ids)
        c.commit()
        print(f"\n{len(ids):,} pares sacados de EXACTO -> tipo REVISAR, pendientes")
    elif res[DISTINTOS]:
        print("\n(nada cambiado; con --marcar salen de EXACTO)")
    c.close()


if __name__ == "__main__":
    main()
