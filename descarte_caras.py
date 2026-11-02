"""Primer descarte de un evento: fotos donde alguien sale mal y repetidas de rafaga.

    python descarte_caras.py "C:\\Fotos\\<evento>" [--prota foto_de_la_protagonista.jpg]

Necesita caras.npz (caras_evento.py) y calidad.npz (caras_calidad.py). Deja
descarte.csv en la carpeta del evento: archivo, decision (queda/mala/repetida), razon.
Con --xmp ademas pone estrellas en sidecars de los RAW. No mueve ni borra nada.

Umbrales calibrados mirando caras del cumpleanos KatiSasso (31/10/2026):
  ojos   parpadeo de AMBOS ojos > 0.55 sin sonrisa (la carcajada con ojos cerrados es buena)
  boca   mandibula > 0.30 sin sonrisa = hablando a media palabra / mueca
  foco   ni la cara MAS nitida de la foto pasa 20 (las del fondo desenfocadas son normales)
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

RAFAGA_S = 1.0     # tomas a <= 1 s de la anterior son el mismo momento
PROTA_SIM = 0.40   # similitud con la foto de referencia para decir "es la protagonista"


def hora(fp: Path) -> float:
    e = Image.open(fp).getexif().get_ifd(0x8769)
    sub = str(e.get(37521, "0") or "0").strip()
    return datetime.strptime(e[36867], "%Y:%m:%d %H:%M:%S").timestamp() + int(sub) / 10 ** len(sub)


def embedding_prota(fp: str) -> np.ndarray:
    from utils_analisis import FaceAnalyzer
    from utils_images import load_image_array
    caras = FaceAnalyzer(use_gpu=False).analyze(load_image_array(fp), min_lado_rel=0.0, min_det_score=0.3)
    if not caras:
        raise SystemExit(f"no se detecto cara en {fp}")
    return np.asarray(max(caras, key=lambda c: c["bbox"][2] - c["bbox"][0])["embedding"], np.float32)


def decidir(carpeta: Path, prota: np.ndarray = None):
    c, q = np.load(carpeta / "caras.npz"), np.load(carpeta / "calidad.npz")
    a, lado, nit, bs, n = c["archivo"], q["lado"], q["nitidez"], q["bs"], list(q["nombres"])
    blink = np.minimum(bs[:, n.index("eyeBlinkLeft")], bs[:, n.index("eyeBlinkRight")])
    jaw = bs[:, n.index("jawOpen")]
    smile = (bs[:, n.index("mouthSmileLeft")] + bs[:, n.index("mouthSmileRight")]) / 2
    es_prota = (c["emb"] @ prota >= PROTA_SIM) if prota is not None else np.zeros(len(a), bool)
    mx = {f: lado[a == f].max() for f in set(a)}
    principal = np.array([l >= 0.05 and l >= 0.6 * mx[f] for f, l in zip(a, lado)]) | (es_prota & (lado >= 0.03))
    mala_cara = {"ojos": principal & (blink > 0.55) & (smile < 0.5),
                 "boca": principal & (jaw > 0.30) & (smile < 0.1)}

    fotos = sorted(p.name for p in (carpeta / "jpeg").iterdir())
    info = {}
    for f in fotos:
        m = (a == f) & principal
        if not m.any():
            info[f] = ([], None)  # sin caras juzgables: no se toca
            continue
        razon = [k for k, v in mala_cara.items() if (v & (a == f)).any()]
        if nit[m].max() < 20:
            razon.append("foco")
        mp = m & es_prota
        # orden dentro de la rafaga: menos problemas, despues la protagonista mas nitida, despues la cara mas nitida
        info[f] = (razon, (len(razon), -float(nit[mp].max()) if mp.any() else 0.0, -float(nit[m].max())))

    t = {f: hora(carpeta / "jpeg" / f) for f in fotos}
    grupos, g = [], [fotos[0]]
    for p, f in zip(fotos, fotos[1:]):
        if t[f] - t[p] <= RAFAGA_S:
            g.append(f)
        else:
            grupos.append(g)
            g = [f]
    grupos.append(g)

    out = {}
    for g in grupos:
        juzgables = [f for f in g if info[f][1] is not None and not info[f][0]]
        mejor = min(juzgables, key=lambda f: info[f][1]) if juzgables else None
        for f in g:
            razon, clave = info[f]
            if razon:
                out[f] = ("mala", "+".join(razon))
            elif clave is None or f == mejor:
                out[f] = ("queda", "")
            else:
                out[f] = ("repetida", f"rafaga de {len(g)}, queda {mejor}")
    return out, len(grupos)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta")
    ap.add_argument("--prota")
    ap.add_argument("--xmp", action="store_true", help="estrellas en sidecars de los RAW: 3 queda, 2 repetida, 1 mala")
    a = ap.parse_args()
    carpeta = Path(a.carpeta)
    out, ngr = decidir(carpeta, embedding_prota(a.prota) if a.prota else None)
    with open(carpeta / "descarte.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "decision", "razon"])
        w.writerows([f, d, r] for f, (d, r) in sorted(out.items()))
    tot = {k: sum(1 for d, _ in out.values() if d == k) for k in ("queda", "mala", "repetida")}
    print(f"{len(out)} fotos en {ngr} momentos -> {tot}")
    if a.xmp:
        # estrellas y no etiquetas: el nombre de la etiqueta depende del idioma de Lightroom
        from autocull.output.xmp import generate_xmp_sidecar
        estrellas = {"queda": 3, "repetida": 2, "mala": 1}
        for f, (d, _) in out.items():
            raw = carpeta / "RAW" / (Path(f).stem + ".ARW")
            if raw.exists():
                generate_xmp_sidecar(str(raw), rating=estrellas[d], label="")
        print("XMP escritos en RAW/: en Lightroom, Metadatos > Leer metadatos de archivos")
