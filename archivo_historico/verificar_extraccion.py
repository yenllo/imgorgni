r"""
Comprueba que lo extraido coincide con lo que dice el ZIP.

Cada entrada de un ZIP trae su CRC32. Comparar el archivo en disco contra ese
CRC es prueba definitiva de que salio entero: mas fuerte que mirar el tamaño,
porque dos procesos escribiendo el mismo archivo a la vez pueden dejarlo del
tamaño correcto y con el contenido entremezclado.

    python verificar_extraccion.py                    # los ultimos 10 zips
    python verificar_extraccion.py --zips 80 81 82    # unos puntuales
    python verificar_extraccion.py --todos            # todo (lento)
    python verificar_extraccion.py --reparar          # reextrae lo que no cuadre

Los archivos que no cuadran se borran con --reparar para que la proxima corrida
del extractor los vuelva a sacar (pierde la marca del zip).
"""
import argparse
import os
import re
import zipfile
import zlib

ORIGEN = r"K:\Descargas"
DESTINO = r"K:\Lightroom_extraido"
MARCAS = os.path.join(DESTINO, "_extraidos")


def crc_archivo(path, buf=1 << 20):
    c = 0
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(buf), b""):
            c = zlib.crc32(b, c)
    return c & 0xFFFFFFFF


def revisar_zip(nombre, reparar=False):
    """(ok, faltantes, malos). malos = existe pero el CRC no coincide."""
    ruta = os.path.join(ORIGEN, nombre)
    ok = faltan = 0
    malos = []
    with zipfile.ZipFile(ruta) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            dst = os.path.join(DESTINO, *info.filename.split("/"))
            # El extractor, ante un choque de nombre, escribe en __dupN y deja
            # el original de otro zip. Buscar solo en la ruta original haria
            # ver como "corrupto" un archivo perfectamente sano -- y con
            # --reparar lo borraria.
            candidatos = [dst]
            raiz, ext = os.path.splitext(dst)
            candidatos += [f"{raiz}__dup{i}{ext}" for i in range(2, 6)]
            existentes = [p for p in candidatos if os.path.exists(p)]
            if not existentes:
                faltan += 1
                continue
            for p in existentes:
                if os.path.getsize(p) == info.file_size and crc_archivo(p) == info.CRC:
                    ok += 1
                    break
            else:
                malos.append((dst, f"ninguna de {len(existentes)} copias coincide"))
    if reparar and malos:
        for p, _ in malos:
            try:
                os.remove(p)
            except OSError:
                pass
        marca = os.path.join(MARCAS, nombre + ".ok")
        if os.path.exists(marca):
            os.remove(marca)
    return ok, faltan, malos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", nargs="*", help="numeros de zip a revisar")
    ap.add_argument("--ultimos", type=int, default=10)
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--reparar", action="store_true")
    args = ap.parse_args()

    marcas = sorted(os.listdir(MARCAS)) if os.path.isdir(MARCAS) else []
    hechos = [m[:-3] for m in marcas if m.endswith(".ok")]
    if args.zips:
        objetivo = [f"LightroomLibrary_{n}.zip" for n in args.zips]
    elif args.todos:
        objetivo = hechos
    else:
        # los ultimos marcados por fecha de la marca
        objetivo = [m[:-3] for m in sorted(
            (m for m in marcas if m.endswith(".ok")),
            key=lambda m: os.path.getmtime(os.path.join(MARCAS, m)))][-args.ultimos:]

    print(f"revisando {len(objetivo)} zips contra su CRC")
    print("MODO: " + ("REPARAR (borra lo que no cuadre)" if args.reparar else "solo informar") + "\n")
    tot_ok = tot_faltan = 0
    tot_malos = []
    for z in objetivo:
        if not os.path.exists(os.path.join(ORIGEN, z)):
            print(f"  {z}: no esta el zip, salteo")
            continue
        ok, faltan, malos = revisar_zip(z, args.reparar)
        tot_ok += ok; tot_faltan += faltan; tot_malos += malos
        estado = "OK" if not malos and not faltan else \
                 f"{len(malos)} MALOS, {faltan} faltan"
        print(f"  {z:32} {ok:5,} verificados  {estado}")
        for p, m in malos[:3]:
            print(f"       {m}: {p.replace(DESTINO, '')}")

    print(f"\n  verificados bien : {tot_ok:,}")
    print(f"  faltantes        : {tot_faltan:,}")
    print(f"  CORRUPTOS        : {len(tot_malos):,}")
    if tot_malos and not args.reparar:
        print("\n  correlo con --reparar para borrarlos y que se reextraigan")
    elif tot_malos:
        print("\n  borrados; volve a correr extraer_lightroom.py y los rehace")


if __name__ == "__main__":
    main()
