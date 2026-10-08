r"""Datos para el modelo de color de Renzo (8/10/2026): que hizo en cada foto de LigaExScuola.

    python color_datos.py catalogo   # 1. valores finales + historial, desde una COPIA del catalogo
    python color_datos.py raws       # 2. cruza cada foto con su RAW (TrabajoFotografico o NAS)
    python color_datos.py rasgos     # 3. de cada RAW: metadatos, balance "como se tomo" y miniaturas
    python color_datos.py finales    # 4. cruza cada foto con su JPEG entregado (Drive): el "look" final

Todo queda en _color\ (fuera de git). Nunca abre el catalogo original: lo copia primero.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DATOS = AQUI / "_color"
CATALOGO = Path(r"C:\Fotos\otro\catalogos\LigaExScuola\LigaExScuola.lrcat")
LOCAL = AQUI / "TrabajoFotografico"
NAS_LIGA = "/mnt/fotos/Fotos/Galeria/Trabajo/Liga ExScuola"   # en la Pi
DRIVE = Path(r"G:\Mi unidad\TrabajoFotografia\LigaExScuola2026")
CLAVES = ("WhiteBalance", "Temperature", "Tint", "Exposure2012", "Contrast2012", "Highlights2012",
          "Shadows2012", "Whites2012", "Blacks2012", "Vibrance", "Saturation", "Texture", "Clarity2012",
          "Dehaze", "CameraProfile", "ProcessVersion", "CropTop", "CropLeft", "CropBottom", "CropRight",
          "CropAngle", "HasCrop")
EXIFTOOL = shutil.which("exiftool") or r"C:\Users\renzo\AppData\Local\Programs\ExifTool\exiftool.exe"


def ajustes(texto):
    """El texto Lua de un revelado (s = { Temperature = 3850, ... }) -> dict con las CLAVES."""
    if isinstance(texto, bytes):
        texto = texto.decode("utf-8", "ignore")
    out = {}
    for k in CLAVES:
        m = re.search(rf"\b{k} = ([^,\n}}]+)", texto or "")
        if m:
            v = m.group(1).strip().strip('"')
            try:
                v = float(v)
            except ValueError:
                pass
            out[k] = v
    return out


def catalogo():
    import sqlite3
    DATOS.mkdir(exist_ok=True)
    copia = DATOS / "LigaExScuola_copia.lrcat"
    shutil.copy2(CATALOGO, copia)       # el original no se abre nunca
    c = sqlite3.connect(copia)
    a_mano = defaultdict(set)
    for img, nombre in c.execute("SELECT image, name FROM Adobe_libraryImageDevelopHistoryStep"):
        a_mano[img].add(nombre)
    filas = []
    q = """SELECT i.id_local, f.baseName, f.extension, i.captureTime, r.absolutePath || fo.pathFromRoot,
                  s.text, i.rating, i.pick
           FROM Adobe_images i JOIN AgLibraryFile f ON f.id_local = i.rootFile
           JOIN AgLibraryFolder fo ON fo.id_local = f.folder
           JOIN AgLibraryRootFolder r ON r.id_local = fo.rootFolder
           JOIN Adobe_imageDevelopSettings s ON s.image = i.id_local"""
    for iid, base, ext, captura, carpeta, texto, rating, pick in c.execute(q):
        pasos = a_mano.get(iid, set())
        filas.append({"id": iid, "nombre": f"{base}.{ext}", "captura": captura, "carpeta_catalogo": carpeta,
                      "final": ajustes(texto), "rating": rating, "pick": pick,
                      "temp_a_mano": "Temperatura" in pasos or "Matiz" in pasos,
                      "expo_a_mano": "Exposición" in pasos,
                      "auto_eb": any(p.startswith("Equilibrio de blancos autom") for p in pasos),
                      "auto_tono": any(p.startswith("Ajustes autom") for p in pasos)})
    (DATOS / "catalogo.json").write_text(json.dumps(filas, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(filas)} fotos; temperatura a mano en {sum(f['temp_a_mano'] for f in filas)}, "
          f"exposicion a mano en {sum(f['expo_a_mano'] for f in filas)}")


def exif(rutas, campos=("DateTimeOriginal", "SubSecTimeOriginal")):
    """exiftool en tandas -> {ruta: {campo: valor}}."""
    out = {}
    for i in range(0, len(rutas), 200):
        tanda = rutas[i:i + 200]
        arg = DATOS / "exiftool_args.txt"
        arg.write_text("\n".join(tanda), encoding="utf-8")
        r = subprocess.run([EXIFTOOL, "-j", "-charset", "filename=utf8", *[f"-{c}" for c in campos],
                            "-@", str(arg)], capture_output=True, text=True, encoding="utf-8")
        for d in json.loads(r.stdout or "[]"):
            out[os.path.normpath(d["SourceFile"])] = d
    return out


def hora(captura):
    """'2026-05-16T19:12:33.12' o '2026:05:16 19:12:33' -> '2026-05-16 19:12:33' (al segundo)."""
    s = str(captura).replace("T", " ")
    return s[:4] + "-" + s[5:7] + "-" + s[8:10] + s[10:19]


def nas_unc():
    for base in (r"\\192.168.1.62\Fotos", r"\\100.78.164.81\Fotos"):
        if os.path.isdir(base + r"\Fotos"):
            return base
    sys.exit("no llego al NAS")


def raws():
    fotos = json.loads((DATOS / "catalogo.json").read_text(encoding="utf-8"))
    # todos los ARW de la liga: en la Pi (rapido) y en TrabajoFotografico
    r = subprocess.run(["ssh", "fotosnas", f'find "{NAS_LIGA}" -iname "*.arw" -path "*/RAW/*"'],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    unc = nas_unc()
    cands = defaultdict(list)
    for p in r.stdout.splitlines():
        cands[os.path.basename(p).upper()].append(unc + p.replace("/mnt/fotos", "").replace("/", "\\"))
    for p in LOCAL.glob("LigaExScuola/*/RAW/**/*.ARW"):
        cands[p.name.upper()].append(str(p))
    # desempate por hora de captura (los DSC se repiten entre tarjetas y temporadas)
    dudosos = sorted({c for f in fotos for c in cands.get(f["nombre"].upper(), [])})
    print(f"{len(dudosos)} RAW candidatos; leo su hora de captura...")
    horas = {k: hora(v.get("DateTimeOriginal", "")) for k, v in exif(dudosos).items()}
    ok = 0
    for f in fotos:
        h = hora(f["captura"])
        buenos = [c for c in cands.get(f["nombre"].upper(), []) if horas.get(os.path.normpath(c)) == h]
        # local primero: leer del disco es mucho mas rapido que por la red
        buenos.sort(key=lambda c: not c.startswith(str(LOCAL)))
        f["raw"] = buenos[0] if buenos else None
        ok += bool(buenos)
    (DATOS / "catalogo.json").write_text(json.dumps(fotos, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"RAW encontrado para {ok} de {len(fotos)}")


if __name__ == "__main__":
    {"catalogo": catalogo, "raws": raws}[sys.argv[1]]()
