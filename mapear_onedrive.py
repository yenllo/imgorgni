r"""
Decide qué carpetas de OneDrive vale la pena descargar, sin descargar nada.

OneDrive sincroniza el árbol como marcadores: el archivo figura con su nombre,
tamaño y fecha reales aunque el contenido esté solo en la nube. Listar y pedir
metadatos NO lo baja; solo abrirlo lo haría. Este script nunca abre un archivo,
así que se puede correr sobre 35 mil archivos en segundos y sin gastar cuota.

Para cada carpeta responde tres cosas cruzando contra la base:
  - cuántas de sus fotos YA están en el archivo local (no aportan nada)
  - cuántas coinciden con archivos DAÑADOS (son el reemplazo que buscamos)
  - cuántas no están en la base (material nuevo)

    python mapear_onedrive.py
    python mapear_onedrive.py --raiz "C:\Users\renzo\OneDrive\Pictures"

Cómo decide si ya lo tenés, y por qué así:

  "ya está"  -> coinciden NOMBRE Y TAMAÑO exacto con un archivo de la base.
  "repara"   -> el nombre coincide con uno dañado (acá el tamaño no sirve:
                justo lo que buscamos es una versión distinta y sana).
  "nuevo"    -> todo lo demás.

Comparar solo el nombre no alcanza: el 44% de los archivos de la base comparte
nombre con otro (`dsc07719.jpg` aparece 7 veces). Con ese criterio, 16.452
archivos de OneDrive figuraban como "ya lo tengo" cuando pesan distinto — son
otra foto, u otra versión. Sumar el tamaño no es perfecto, pero los errores
dejan de ser simétricos: saltear algo que no tenés lo perdés, y bajar de más
se detecta despues con sha256 y se descarta. Conviene errar hacia "nuevo".

El veredicto final NO es este: es cruzar_descarga.py con sha256, una vez que el
archivo está en disco. Esto solo elige qué carpetas bajar.
"""
import argparse
import collections
import csv
import ntpath
import os
import sqlite3
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
RAIZ = r"C:\Users\renzo\OneDrive"
IMG = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tif", ".tiff",
       ".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng", ".orf", ".srw"}
VIDEO = {".mp4", ".mov", ".avi", ".mts", ".m4v", ".mkv"}


def recorrer(raiz):
    """(carpeta, nombre, ext, bytes, mtime) de cada archivo. Solo metadatos."""
    for dirpath, _, archivos in os.walk(raiz):
        for a in archivos:
            ext = os.path.splitext(a)[1].lower()
            if ext not in IMG and ext not in VIDEO:
                continue
            try:
                st = os.stat(os.path.join(dirpath, a))   # no abre el archivo
            except OSError:
                continue
            yield dirpath, a.lower(), ext, st.st_size, st.st_mtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", default=RAIZ)
    ap.add_argument("--csv", default="onedrive_carpetas.csv")
    args = ap.parse_args()
    if not os.path.isdir(args.raiz):
        raise SystemExit(f"no existe {args.raiz}")

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    en_base, danados = set(), set()
    nombres = set()
    for ruta, b, dan in c.execute("""SELECT file_path, COALESCE(bytes,0),
                                            COALESCE(danado,0)
                                     FROM files WHERE COALESCE(borrado,0)=0"""):
        nom = ntpath.basename(ruta).lower()
        nombres.add(nom)
        en_base.add((nom, b))            # nombre Y tamaño: ver el docstring
        if dan == 1:                     # solo pérdida real; el nivel 2 abre igual
            danados.add(nom)
    print(f"base: {len(nombres):,} nombres distintos, "
          f"{len(en_base):,} pares nombre+tamaño, {len(danados):,} dañados\n")

    t0 = time.time()
    carp = collections.defaultdict(lambda: {
        "n": 0, "bytes": 0, "ya": 0, "repara": 0, "nuevo": 0,
        "video": 0, "fechas": [], "ext": collections.Counter()})
    for dirpath, nom, ext, size, mtime in recorrer(args.raiz):
        d = carp[dirpath]
        d["n"] += 1
        d["bytes"] += size
        d["fechas"].append(mtime)
        d["ext"][ext] += 1
        if ext in VIDEO:
            d["video"] += 1
        elif nom in danados:
            d["repara"] += 1
        elif (nom, size) in en_base:
            d["ya"] += 1
        else:
            d["nuevo"] += 1
    print(f"recorridas {len(carp):,} carpetas con fotos en {time.time()-t0:.0f}s\n")

    filas = []
    for ruta, d in carp.items():
        if d["repara"]:
            veredicto = "DESCARGAR (repara dañados)"
        elif d["nuevo"] >= max(3, d["n"] * 0.2):
            veredicto = "descargar (material nuevo)"
        elif d["nuevo"]:
            veredicto = "quizás (poco nuevo)"
        else:
            veredicto = "no hace falta (ya está todo)"
        f = sorted(d["fechas"])
        filas.append({
            "carpeta": ruta, "archivos": d["n"], "gb": d["bytes"] / 1024**3,
            "repara": d["repara"], "nuevo": d["nuevo"], "ya": d["ya"],
            "video": d["video"], "veredicto": veredicto,
            "desde": time.strftime("%Y-%m-%d", time.localtime(f[0])),
            "hasta": time.strftime("%Y-%m-%d", time.localtime(f[-1])),
            "ext": " ".join(f"{k}:{v}" for k, v in d["ext"].most_common(4))})

    orden = {"DESCARGAR (repara dañados)": 0, "descargar (material nuevo)": 1,
             "quizás (poco nuevo)": 2, "no hace falta (ya está todo)": 3}
    filas.sort(key=lambda x: (orden[x["veredicto"]], -x["repara"], -x["nuevo"]))

    print(f"{'carpeta':<58} {'arch':>6} {'GB':>6} {'repara':>7} {'nuevo':>6}")
    print("-" * 92)
    for x in filas[:28]:
        corta = x["carpeta"].replace(args.raiz, "…")
        print(f"{corta[-58:]:<58} {x['archivos']:>6,} {x['gb']:>6.1f} "
              f"{x['repara']:>7,} {x['nuevo']:>6,}")

    print("\n" + "=" * 60)
    for v in orden:
        sel = [x for x in filas if x["veredicto"] == v]
        if sel:
            print(f"  {v:32} {len(sel):4} carpetas  "
                  f"{sum(x['archivos'] for x in sel):7,} arch  "
                  f"{sum(x['gb'] for x in sel):7.1f} GB")
    baja = [x for x in filas if orden[x["veredicto"]] <= 1]
    print("=" * 60)
    print(f"  bajando solo lo que sirve: {sum(x['gb'] for x in baja):,.0f} GB "
          f"de {sum(x['gb'] for x in filas):,.0f} GB totales")

    with open(args.csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)
    print(f"\n  detalle -> {args.csv}")


if __name__ == "__main__":
    main()
