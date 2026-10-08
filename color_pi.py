"""Corre EN LA PI (lee el 8TB a 272 MB/s; por la red serian horas). Parte de color_datos.py.

    python3 color_pi.py fotos.json salida/     # RAW de cada foto del catalogo
    python3 color_pi.py finales salida_finales/ # JPEG entregados (el look final)

fotos.json: [{"id", "nombre", "captura"}] del catalogo. Por cada foto busca su ARW original bajo la
liga, lo confirma por hora de captura y deja en salida/:
  <id>.lin.npy   imagen LINEAL del sensor (R, G, B por superpixel 2x2), sin balance ni curva, ~192x128,
                 normalizada a 0-1: el color real de la luz de la cancha, base para la temperatura
  <id>.jpg       la vista previa que trae el RAW (con el balance de la camara), 512 px: que hay en la escena
  rasgos.jsonl   una linea por foto: ruta, EXIF, balance "como se tomo" y de dia, recorte de luces
Retoma: lo que ya esta en rasgos.jsonl no se repite.
"""
import io
import json
import os
import subprocess
import sys
import time

import cv2
import exifread
import numpy as np
import rawpy
from PIL import Image

LIGA = "/mnt/fotos/Fotos/Galeria/Trabajo/Liga ExScuola"
LADO = 192


def hora(s):
    s = str(s).replace("T", " ")
    return s[:4] + "-" + s[5:7] + "-" + s[8:10] + s[10:19]


def exif_de(ruta):
    with open(ruta, "rb") as f:
        t = exifread.process_file(f, details=False)
    g = lambda k: str(t[k]) if k in t else None
    return {"fecha": g("EXIF DateTimeOriginal"), "iso": g("EXIF ISOSpeedRatings"),
            "exposicion": g("EXIF ExposureTime"), "f": g("EXIF FNumber"), "focal": g("EXIF FocalLength")}


def lineal(raw):
    """Superpixel 2x2 del Bayer -> R, G, B lineales, sin demosaico ni balance. Rapido y fiel al sensor."""
    im = raw.raw_image_visible
    h, w = im.shape[0] // 2 * 2, im.shape[1] // 2 * 2
    pat, desc = raw.raw_pattern, raw.color_desc.decode()
    negro = raw.black_level_per_channel
    canales = {"R": [], "G": [], "B": []}
    for dy in (0, 1):
        for dx in (0, 1):
            c = int(pat[dy, dx])
            plano = im[dy:h:2, dx:w:2].astype(np.float32) - negro[c]
            canales[desc[c]].append(plano)
    blanco = float(raw.white_level - max(negro))
    rgb = np.dstack([np.mean(canales[k], axis=0) for k in "RGB"]) / blanco
    recorte = float((rgb.max(axis=2) > 0.98).mean())
    alto = round(LADO * rgb.shape[0] / rgb.shape[1])
    chico = cv2.resize(rgb, (LADO, alto), interpolation=cv2.INTER_AREA)
    return np.clip(chico, 0, None).astype(np.float16), recorte


def main(entrada, salida):
    os.makedirs(salida, exist_ok=True)
    fotos = json.load(open(entrada, encoding="utf-8"))
    hechas = set()
    rasgos = os.path.join(salida, "rasgos.jsonl")
    if os.path.exists(rasgos):
        hechas = {json.loads(l)["id"] for l in open(rasgos, encoding="utf-8") if l.strip()}
    todos = subprocess.run(["find", LIGA, "-iname", "*.arw", "-path", "*/RAW/*"], capture_output=True,
                           text=True).stdout.splitlines()
    por_nombre = {}
    for p in todos:
        if "/Compras/" in p:
            continue
        por_nombre.setdefault(os.path.basename(p).upper(), []).append(p)
    fechas = {}
    t0, n = time.time(), 0
    with open(rasgos, "a", encoding="utf-8") as out:
        for f in fotos:
            if f["id"] in hechas:
                continue
            ruta = None
            for c in por_nombre.get(f["nombre"].upper(), []):
                if c not in fechas:
                    fechas[c] = exif_de(c)
                if hora(fechas[c]["fecha"] or "") == hora(f["captura"]):
                    ruta = c
                    break
            fila = {"id": f["id"], "raw": ruta}
            if ruta:
                try:
                    with rawpy.imread(ruta) as raw:
                        lin, recorte = lineal(raw)
                        fila.update(exif=fechas[ruta], recorte=recorte,
                                    eb_camara=[float(x) for x in raw.camera_whitebalance],
                                    eb_dia=[float(x) for x in raw.daylight_whitebalance],
                                    color_desc=raw.color_desc.decode())
                        try:
                            th = raw.extract_thumb()
                            if th.format == rawpy.ThumbFormat.JPEG:
                                im = Image.open(io.BytesIO(th.data)).convert("RGB")
                                im.thumbnail((512, 512))
                                im.save(os.path.join(salida, f"{f['id']}.jpg"), quality=88)
                        except Exception as e:
                            fila["sin_vista"] = str(e)[:100]
                    np.save(os.path.join(salida, f"{f['id']}.lin.npy"), lin)
                except Exception as e:
                    fila["error"] = str(e)[:200]
            out.write(json.dumps(fila) + "\n")
            out.flush()
            n += 1
            if n % 25 == 0:
                print(f"{n} en {time.time() - t0:.0f} s", flush=True)
    print(f"listo: {n} nuevas en {time.time() - t0:.0f} s", flush=True)


def finales(salida):
    """Los JPEG que Renzo entrego (EdicionBasica / EdicionCompleta, sin IG ni Pre): hora de captura y
    una copia de 512 px. Es el "look" final de verdad, para comparar contra lo que proponga el modelo."""
    os.makedirs(salida, exist_ok=True)
    indice = os.path.join(salida, "finales.jsonl")
    hechas = set()
    if os.path.exists(indice):
        hechas = {json.loads(l)["ruta"] for l in open(indice, encoding="utf-8") if l.strip()}
    todos = subprocess.run(["find", LIGA, "-iname", "*.jpg", "-ipath", "*edicion*"], capture_output=True,
                           text=True).stdout.splitlines()
    todos = [p for p in todos if "ig" not in os.path.basename(os.path.dirname(p)).lower()[-2:]
             and "preedicion" not in p.lower()]
    t0, n = time.time(), 0
    with open(indice, "a", encoding="utf-8") as out:
        for i, ruta in enumerate(sorted(todos)):
            if ruta in hechas:
                continue
            fila = {"ruta": ruta, "nombre": os.path.basename(ruta), "completa": "completa" in ruta.lower()}
            try:
                fila["fecha"] = exif_de(ruta)["fecha"]
                im = Image.open(ruta)
                im.draft("RGB", (1024, 1024))      # decodifica ya reducido: no carga 42 MP
                im = im.convert("RGB")
                im.thumbnail((512, 512))
                nombre = f"final_{i:05d}.jpg"
                im.save(os.path.join(salida, nombre), quality=90)
                fila["chica"] = nombre
            except Exception as e:
                fila["error"] = str(e)[:200]
            out.write(json.dumps(fila) + "\n")
            out.flush()
            n += 1
            if n % 100 == 0:
                print(f"finales: {n} en {time.time() - t0:.0f} s", flush=True)
    print(f"finales listo: {n} en {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "finales":
        finales(sys.argv[2])
    else:
        main(sys.argv[1], sys.argv[2])
