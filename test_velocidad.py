r"""Mide en ~30 segundos si el disco esta enlazado a USB 3.0 o cayo a USB 2.0.

Sirve para probar cables, puertos y orientaciones sin adivinar: se corre, se
cambia una cosa, se vuelve a correr. La referencia para el Toshiba de 8 TB
(7200 RPM, SATA/600 dentro del dock):

    ~35-45 MB/s   -> el enlace esta a USB 2.0 (High Speed) aunque los conectores
                     sean 3.0: los pines SuperSpeed no estan contactando.
    ~180-260 MB/s -> USB 3.0 de verdad. Es lo que el disco puede dar.

La lectura NO se mide sobre el archivo recien escrito: Windows lo tiene en RAM y
devuelve miles de MB/s que no son del disco. Se lee un archivo grande que ya
estaba, elegido al azar para que no quede cacheado de una corrida anterior.

    python test_velocidad.py            # sobre K:
    python test_velocidad.py --disco E:
"""
import argparse
import os
import random
import time

BLOQUE = 8 << 20
TAM = 1 << 30          # 1 GB


def escribir(disco):
    p = os.path.join(disco + os.sep, "_test_velocidad.tmp")
    bloque = b"\0" * BLOQUE
    t = time.time()
    with open(p, "wb") as fh:
        for _ in range(TAM // BLOQUE):
            fh.write(bloque)
        fh.flush()
        os.fsync(fh.fileno())      # sin esto se mide la RAM, no el disco
    v = TAM / 1e6 / (time.time() - t)
    os.remove(p)
    return v


def buscar_grandes(disco, cuantos=8, minimo=20 << 20):
    """Archivos ya existentes de >=20 MB, para leer algo que no este en cache."""
    salida = []
    for raiz, dirs, fs in os.walk(disco + os.sep):
        dirs[:] = [d for d in dirs if not d.startswith(("$", "_duplicados"))]
        for f in fs:
            p = os.path.join(raiz, f)
            try:
                if os.path.getsize(p) >= minimo:
                    salida.append(p)
            except OSError:
                pass
        if len(salida) > 400:
            break
    return random.sample(salida, min(cuantos, len(salida))) if salida else []


def leer(archivos):
    tot = 0
    t = time.time()
    for p in archivos:
        try:
            with open(p, "rb") as fh:
                while True:
                    b = fh.read(BLOQUE)
                    if not b:
                        break
                    tot += len(b)
        except OSError:
            pass
    dt = time.time() - t
    return (tot / 1e6 / dt if dt else 0), tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disco", default="K:")
    a = ap.parse_args()
    d = a.disco.rstrip("\\/")
    if not os.path.exists(d + os.sep):
        raise SystemExit("no veo %s" % d)

    print("midiendo %s ..." % d)
    esc = escribir(d)
    archivos = buscar_grandes(d)
    if not archivos:
        raise SystemExit("no encontre archivos grandes para medir la lectura")
    lec, leido = leer(archivos)

    print()
    print("  escritura : %6.1f MB/s   (1 GB con fsync)" % esc)
    print("  lectura   : %6.1f MB/s   (%.0f MB de %d archivos ya existentes)"
          % (lec, leido / 1e6, len(archivos)))
    print()
    peor = min(esc, lec)
    if peor < 60:
        print("  >> USB 2.0. El enlace NO esta en SuperSpeed.")
        print("     El disco puede dar 180-260 MB/s: el problema esta en el cable,")
        print("     el conector o el puerto. Probar en este orden:")
        print("       1. dar vuelta el conector USB-C (es reversible para USB 2.0,")
        print("          pero un lado de los pines SuperSpeed puede no contactar)")
        print("       2. otro puerto USB-C del notebook")
        print("       3. reasentar el USB-B en el dock: entra a medias con facilidad")
        print("       4. otro cable")
    elif peor < 120:
        print("  >> Enlace SuperSpeed pero rindiendo a medias.")
    else:
        print("  >> USB 3.0 funcionando como corresponde.")


if __name__ == "__main__":
    main()
