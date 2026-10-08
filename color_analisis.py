r"""Une catalogo + RAW + JPEG final y mide el balance REAL con que quedo cada foto (8/10/2026).

    python color_analisis.py      # -> _color\tabla.json y un resumen

El balance "real" sale de comparar el JPEG final de Renzo con el RAW lineal, pixel a pixel, en el
espacio del sensor (matriz de color de Adobe para la a7R III): la ganancia de rojo y azul respecto del
verde que hubo que aplicar para que el sensor diera ese JPEG. No depende de lo que diga el catalogo.
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

AQUI = Path(__file__).resolve().parent
DATOS = AQUI / "_color"
# Adobe ColorMatrix2 (D65) de la ILCE-7RM3, leida con rawpy: camara <- XYZ
CAM_XYZ = np.array([[0.664, -0.1847, -0.0503], [-0.5238, 1.301, 0.2474], [-0.0993, 0.1673, 0.6527]])
XYZ_SRGB = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
CAM_SRGB = CAM_XYZ @ XYZ_SRGB          # sRGB lineal -> camara


def hora(s):
    s = str(s).replace("T", " ")
    return s[:4] + "-" + s[5:7] + "-" + s[8:10] + s[10:19]


def lineal_srgb(im):
    a = np.asarray(im, dtype=np.float32) / 255
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def ganancias(lin, final_jpg, ajuste, lado=96):
    """Ganancia efectiva (log R/G, log B/G) que lleva el RAW lineal al JPEG final, en espacio camara.
    Solo fotos sin rotacion: el recorte del catalogo se aplica al RAW para alinear."""
    h, w = lin.shape[:2]
    t, l = ajuste.get("CropTop", 0) or 0, ajuste.get("CropLeft", 0) or 0
    b, r = ajuste.get("CropBottom", 1) or 1, ajuste.get("CropRight", 1) or 1
    raw = lin[int(t * h):max(int(b * h), int(t * h) + 2), int(l * w):max(int(r * w), int(l * w) + 2)]
    fin = Image.open(final_jpg).convert("RGB")
    alto = round(lado * raw.shape[0] / raw.shape[1])
    fin = lineal_srgb(fin.resize((lado, alto), Image.BILINEAR)) @ CAM_SRGB.T
    raw = np.asarray(Image.fromarray(raw.astype(np.float32)[..., 0]).resize((lado, alto), Image.BILINEAR))[..., None] \
        if False else np.dstack([np.asarray(Image.fromarray(raw[..., c].astype(np.float32)).resize((lado, alto), Image.BILINEAR))
                                 for c in range(3)])
    # pixeles utiles: RAW sin recortar ni en negro, final en tonos medios y poco saturado (lo neutro manda
    # el balance; la saturacion/intensidad de Renzo deforma los colores fuertes)
    lum = fin.mean(axis=2)
    sat = (fin.max(axis=2) - fin.min(axis=2)) / np.maximum(fin.max(axis=2), 1e-6)
    ok = (raw.min(axis=2) > 0.002) & (raw.max(axis=2) < 0.9) & (lum > 0.03) & (lum < 0.7) & (fin.min(axis=2) > 1e-4)
    peso = ok & (sat < np.percentile(sat[ok], 50)) if ok.sum() > 50 else ok
    if peso.sum() < 30:
        return None
    lr = np.log(fin[..., 0] / raw[..., 0]) - np.log(fin[..., 1] / raw[..., 1])
    lb = np.log(fin[..., 2] / raw[..., 2]) - np.log(fin[..., 1] / raw[..., 1])
    return float(np.median(lr[peso])), float(np.median(lb[peso])), int(peso.sum())


def tabla():
    fotos = {f["id"]: f for f in json.loads((DATOS / "catalogo.json").read_text(encoding="utf-8"))}
    rasgos = {}
    for l in (DATOS / "salida" / "rasgos.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            d = json.loads(l)
            rasgos[d["id"]] = d
    finales = {}
    for l in (DATOS / "finales" / "finales.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            d = json.loads(l)
            if d.get("chica") and d.get("fecha"):
                clave = (Path(d["nombre"]).stem.upper(), hora(d["fecha"]))
                # la basica gana: la completa suele ir recortada/enderezada
                if clave not in finales or not d["completa"]:
                    finales[clave] = d
    filas = []
    for iid, f in fotos.items():
        r = rasgos.get(iid)
        if not r or not r.get("raw") or r.get("error"):
            continue
        fila = {"id": iid, "nombre": f["nombre"], "captura": f["captura"], "final": f["final"],
                "temp_a_mano": f["temp_a_mano"], "expo_a_mano": f["expo_a_mano"], "raw": r["raw"],
                "exif": r["exif"], "eb_camara": r["eb_camara"], "recorte": r["recorte"]}
        fin = finales.get((Path(f["nombre"]).stem.upper(), hora(f["captura"])))
        if fin and abs(f["final"].get("CropAngle", 0) or 0) < 0.05:
            lin = np.load(DATOS / "salida" / f"{iid}.lin.npy").astype(np.float32)
            g = ganancias(lin, DATOS / "finales" / fin["chica"], f["final"])
            if g:
                fila["real_lr"], fila["real_lb"], fila["real_px"] = g
                fila["final_jpg"] = fin["chica"]
        filas.append(fila)
    (DATOS / "tabla.json").write_text(json.dumps(filas, ensure_ascii=False), encoding="utf-8")
    return filas


def resumen(filas):
    con = [f for f in filas if "real_lr" in f and isinstance(f["final"].get("Temperature"), float)]
    print(f"{len(filas)} fotos con RAW; {len(con)} con JPEG final alineado y temperatura guardada")
    for modo in ("Custom", "Auto"):
        s = [f for f in con if f["final"].get("WhiteBalance") == modo]
        if len(s) < 10:
            continue
        T = np.array([1e6 / f["final"]["Temperature"] for f in s])   # en mired: lineal con el color
        lr = np.array([f["real_lr"] for f in s])
        lb = np.array([f["real_lb"] for f in s])
        cam = np.array([math.log(f["eb_camara"][0] / f["eb_camara"][1]) - math.log(f["eb_camara"][2] / f["eb_camara"][1]) for f in s])
        print(f"  {modo:6} n={len(s):4}  corr(mired guardado, real log R/B) = {np.corrcoef(T, lr - lb)[0, 1]:+.2f}"
              f"   corr(camara 'como se tomo', real) = {np.corrcoef(cam, lr - lb)[0, 1]:+.2f}")


if __name__ == "__main__":
    resumen(tabla())
