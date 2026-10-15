r"""Decide, archivo por archivo, cuales de la cuarentena se pueden borrar.

Por que va desde la cuarentena y no desde la tabla `duplicados`: esa tabla tiene
47.985 filas marcadas 'movido' pero solo 642 apuntan a donde el archivo quedo, y
'movido' tambien se usa cuando el archivo ya no estaba. Guiarse por ahi es borrar
a ciegas. El disco, en cambio, no miente: se recorre la cuarentena y para cada
archivo se busca un gemelo VIVO en la biblioteca.

Un archivo de la cuarentena es seguro de borrar solo si existe otra copia viva,
fuera de la cuarentena, y las dos son identicas byte a byte. Cualquier otra cosa
—no aparece gemelo, el gemelo no se puede leer, difieren— se marca y no se toca.

    python verificar_cuarentena.py              # verifica todo (~8 h)
    python verificar_cuarentena.py --limite 200 # prueba corta
    python verificar_cuarentena.py --reanudar   # sigue donde quedo

NO BORRA NADA. Deja `verificacion_cuarentena.csv` con una fila por archivo.
El borrado va aparte, y solo sobre los SEGURO.
"""
import argparse
import csv
import hashlib
import os
import sqlite3
import sys
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
CUARENTENA = r"K:\_duplicados_cuarentena"
SALIDA = r"C:\Renzo\Proyectos\IMGorgni\verificacion_cuarentena.csv"
BLOQUE = 1 << 20
# Se resuelve en main() segun --carpeta: la misma logica sirve para cualquier
# arbol del que uno quiera saber que se puede borrar sin perder nada.
CARPETA = CUARENTENA


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(BLOQUE)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def iguales(a, b):
    """byte a byte. Corta en la primera diferencia."""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(BLOQUE), fb.read(BLOQUE)
            if x != y:
                return False
            if not x:
                return True


def indice_vivos(c):
    """{sha256: [rutas vivas fuera de la cuarentena]} desde la base.

    Es solo para tener candidatos rapido; el veredicto igual se toma
    comparando los bytes, asi que un hash desactualizado no puede hacer que
    se borre algo de mas: a lo sumo hace perder el candidato y sale SIN_GEMELO.
    """
    pref = CARPETA.lower()
    idx = {}
    for h, p in c.execute("""SELECT file_hash_sha256, file_path FROM files
                              WHERE file_hash_sha256 IS NOT NULL
                                AND file_hash_sha256 <> ''
                                AND COALESCE(borrado,0)=0"""):
        if p and not p.lower().startswith(pref):
            idx.setdefault(h, []).append(p)
    return idx


def por_tamano(c):
    """{bytes: [rutas vivas]} — respaldo cuando el hash no esta en la base."""
    pref = CARPETA.lower()
    idx = {}
    for n, p in c.execute("""SELECT bytes, file_path FROM files
                              WHERE bytes > 0 AND COALESCE(borrado,0)=0"""):
        if p and not p.lower().startswith(pref):
            idx.setdefault(n, []).append(p)
    return idx


def main():
    global CARPETA, SALIDA          # va primero: abajo se usan como default
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--reanudar", action="store_true",
                    help="saltea lo que ya figura en el CSV")
    ap.add_argument("--carpeta", default=CUARENTENA,
                    help="que arbol revisar (default: la cuarentena)")
    ap.add_argument("--salida", default=SALIDA)
    a = ap.parse_args()
    CARPETA = a.carpeta.rstrip(chr(92))
    SALIDA = a.salida
    print("revisando:", CARPETA)

    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    print("armando el indice de la biblioteca viva...")
    por_hash = indice_vivos(c)
    por_bytes = por_tamano(c)
    print("  %d hashes, %d tamaños distintos" % (len(por_hash), len(por_bytes)))

    hechos = set()
    if a.reanudar and os.path.exists(SALIDA):
        with open(SALIDA, encoding="utf-8") as fh:
            for fila in csv.DictReader(fh):
                hechos.add(fila["ruta"])
        print("  ya verificados en una corrida previa: %d" % len(hechos))

    archivos = []
    for dp, _, fs in os.walk(CARPETA):
        for f in fs:
            archivos.append(os.path.join(dp, f))
    archivos.sort()
    if a.limite:
        archivos = archivos[:a.limite]
    pendientes = [p for p in archivos if p not in hechos]
    total_bytes = 0
    for p in pendientes:
        try:
            total_bytes += os.path.getsize(p)
        except OSError:
            pass
    print("en cuarentena: %d archivos | a verificar ahora: %d (%.1f GB)"
          % (len(archivos), len(pendientes), total_bytes / 1e9))
    print("se lee la cuarentena Y el gemelo, asi que son ~%.0f GB de lectura"
          % (total_bytes * 2 / 1e9))
    print()

    modo = "a" if (a.reanudar and hechos) else "w"
    cuenta = {}
    t0 = time.time()
    leido = 0
    liberable = 0
    with open(SALIDA, modo, encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if modo == "w":
            w.writerow(["ruta", "bytes", "veredicto", "gemelo"])
        for i, p in enumerate(pendientes):
            gemelo = ""
            try:
                n = os.path.getsize(p)
                h = sha256(p)
                leido += n
                # candidatos: por hash, y si no hay, por tamaño exacto
                cands = por_hash.get(h) or por_bytes.get(n) or []
                v = "SIN_GEMELO"
                for cand in cands:
                    if not os.path.exists(cand):
                        continue
                    try:
                        if iguales(p, cand):
                            leido += n
                            v, gemelo = "SEGURO", cand
                            break
                    except OSError:
                        continue
                if v == "SEGURO":
                    liberable += n
            except OSError as e:
                v = "ERROR"
                gemelo = repr(e)[:60]
            cuenta[v] = cuenta.get(v, 0) + 1
            w.writerow([p, n if 'n' in dir() else -1, v, gemelo])
            if (i + 1) % 200 == 0:
                dt = time.time() - t0
                vel = leido / 1e6 / dt if dt else 0
                queda = (len(pendientes) - i - 1) / (i + 1) * dt / 3600
                print("   %6d/%d  %5.1f GB leidos  %3.0f MB/s  faltan ~%.1f h  "
                      "seguros %d (%.0f GB)"
                      % (i + 1, len(pendientes), leido / 1e9, vel, queda,
                         cuenta.get("SEGURO", 0), liberable / 1e9))
                fh.flush()

    print()
    print("=== resultado ===")
    for k in sorted(cuenta, key=lambda x: -cuenta[x]):
        print("   %-14s %6d" % (k, cuenta[k]))
    print()
    print("seguros de borrar: %d archivos, %.1f GB" % (cuenta.get("SEGURO", 0),
                                                       liberable / 1e9))
    if cuenta.get("SIN_GEMELO"):
        print("!! %d SIN GEMELO: son la unica copia. NO se borran."
              % cuenta["SIN_GEMELO"])
    print("detalle ->", SALIDA)


if __name__ == "__main__":
    main()
