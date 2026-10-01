r"""
Verifica archivos RAW decodificandolos de verdad.

El barrido de integridad solo podia juzgar JPEG: miraba el marcador de fin
(ffd9). Los RAW no tienen nada equivalente, asi que los 33.398 RAW de la base
(30% del total, y los originales de la Liga entre ellos) nunca se revisaron.

Un RAW si se puede desempaquetar: `raw.raw_image` obliga a leer los datos del
sensor, y un archivo truncado o corrupto falla ahi. Es lento (~0,3 s por
archivo) pero es la unica senal independiente que existe para estos formatos.

TIF/MPO no son RAW de sensor: esos van por PIL, que tambien levanta error si
el archivo esta cortado. Mandarlos a rawpy daria "roto" a archivos sanos.

    python verificar_raw.py                 # todos los RAW
    python verificar_raw.py -n 200          # una muestra
    python verificar_raw.py --solo-rescate  # solo Fotos_F_rescatadas

Solo lee: no toca la base ni los archivos.
"""
import argparse
import collections
import csv
import ntpath
import os
import sqlite3
import sys
import time

# Formatos de sensor: los desempaqueta rawpy.
SENSOR = {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng", ".srw", ".orf"}
# No son RAW de sensor aunque convivan con ellos: van por PIL.
OTROS = {".tif", ".tiff", ".mpo"}
VERIFICABLES = SENSOR | OTROS

OK = "decodifica"
ROTO = "NO decodifica"
NO_SOPORTADO = "formato no soportado (NO es daño)"

# "no lo entiendo" y "los datos estan mal" son cosas distintas y confundirlas
# manda a rescatar archivos sanos. Un DNG de HDR o panoramica de Lightroom es
# lineal y LibRaw no lo abre, pero el archivo esta perfecto.
_NO_SOPORTA = ("LibRawFileUnsupportedError", "UnidentifiedImageError",
               "LibRawTooBigError")


def decodificador(ruta):
    """Que herramienta corresponde. Separado para poder probar el ruteo."""
    ext = os.path.splitext(ruta)[1].lower()
    return "rawpy" if ext in SENSOR else ("pil" if ext in OTROS else None)


def revisar(ruta):
    """(estado, detalle). No distingue 'truncado' de 'corrupto': para el caso
    da igual, el archivo no sirve. Pero si distingue 'no soportado', que no
    dice nada del estado del archivo."""
    cual = decodificador(ruta)
    try:
        if cual == "rawpy":
            import rawpy
            with rawpy.imread(ruta) as raw:
                _ = raw.raw_image.shape      # fuerza el desempaquetado real
        elif cual == "pil":
            from PIL import Image, ImageFile
            ImageFile.LOAD_TRUNCATED_IMAGES = False   # acá SÍ queremos que falle
            with Image.open(ruta) as im:
                im.load()
        else:
            return None, "extension no verificable"
        return OK, ""
    except Exception as e:
        nombre = type(e).__name__
        estado = NO_SOPORTADO if nombre in _NO_SOPORTA else ROTO
        return estado, f"{nombre}: {e}"[:110]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, help="limitar a N archivos")
    ap.add_argument("--solo-rescate", action="store_true",
                    help="solo Fotos_F_rescatadas")
    ap.add_argument("--csv", default="raw_rotos.csv")
    ap.add_argument("--db", default=r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db")
    args = ap.parse_args()

    c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    filas = [r for r in c.execute("""
        SELECT f.id, f.file_path, f.bytes, f.capture_date,
               (SELECT COALESCE(NULLIF(e.name,''), e.suggested_name)
                FROM events e JOIN event_photos ep ON ep.event_id=e.id
                WHERE ep.photo_id=f.id LIMIT 1) evento
        FROM files f WHERE COALESCE(f.borrado,0)=0""")
        if os.path.splitext(r["file_path"])[1].lower() in VERIFICABLES
        and (not args.solo_rescate or "\\Fotos_F_rescatadas" in r["file_path"])]
    if args.n:
        filas = filas[:args.n]
    total_b = sum(r["bytes"] or 0 for r in filas) or 1
    print(f"RAW/TIF a verificar: {len(filas):,} ({total_b/1024**3:,.0f} GB)\n")

    cont, por_carpeta = collections.Counter(), collections.defaultdict(collections.Counter)
    fh = open(args.csv, "w", encoding="utf-8", newline="")
    w = csv.writer(fh)
    w.writerow(["id", "ruta", "evento", "fecha", "estado", "detalle"])
    t0, leido = time.time(), 0
    try:
        for i, r in enumerate(filas, 1):
            ruta = r["file_path"]
            leido += r["bytes"] or 0
            if not os.path.exists(ruta):
                cont["no esta"] += 1
                continue
            estado, detalle = revisar(ruta)
            if estado is None:
                cont["no verificable"] += 1
                continue
            cont[estado] += 1
            raiz = ruta.split("\\")[1] if "\\" in ruta else ruta
            por_carpeta[raiz][estado] += 1
            if estado == ROTO:
                w.writerow([r["id"], ruta, r["evento"] or "", (r["capture_date"] or "")[:10],
                            estado, detalle])
                fh.flush()
            if i % 250 == 0:
                seg = time.time() - t0
                falta = seg / max(leido, 1) * (total_b - leido)
                print(f"  {i:,}/{len(filas):,}  {cont[ROTO]:,} rotos  "
                      f"{leido/1024**2/max(seg,1):.0f} MB/s  faltan ~{falta/60:.0f} min",
                      file=sys.stderr)
    finally:
        fh.close()

    rev = cont[OK] + cont[ROTO]
    print(f"terminado en {(time.time()-t0)/60:.0f} min\n")
    print(f"  {cont[OK]:7,}  {OK}")
    print(f"  {cont[ROTO]:7,}  {ROTO}" + (f"   ({cont[ROTO]/rev*100:.1f}%)" if rev else ""))
    for k in (NO_SOPORTADO, "no esta", "no verificable"):
        if cont[k]:
            print(f"  {cont[k]:7,}  {k}")
    print("\npor carpeta:")
    for raiz, d in sorted(por_carpeta.items(), key=lambda kv: -kv[1][ROTO]):
        n = d[OK] + d[ROTO]
        if d[ROTO]:
            print(f"  {raiz[:44]:44} {d[ROTO]:6,}/{n:6,} rotos ({d[ROTO]/n*100:4.1f}%)")
    print(f"\nlista en {args.csv}")


if __name__ == "__main__":
    main()
