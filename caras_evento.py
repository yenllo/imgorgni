"""Caras de un evento suelto (fuera de photos_v2.db) para el buscador por selfie.

    python caras_evento.py "C:\\Fotos\\<evento>"                  # indexa jpeg/ -> caras.npz
    python caras_evento.py "C:\\Fotos\\<evento>" --buscar yo.jpg  # fotos donde aparece

caras.npz queda en la carpeta del evento: una fila por cara con su embedding
ArcFace normalizado (512), el archivo y el bbox. Buscar es un producto punto.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from utils_analisis import FaceAnalyzer
from utils_images import load_image_array

# ponytail: umbral fijo de similitud coseno; calibrarlo con selfies reales del evento
UMBRAL = 0.40


def indexar(carpeta: Path):
    jpgs = sorted((carpeta / "jpeg").glob("*.[jJ][pP][gG]"))
    print(f"{len(jpgs)} JPEG en {carpeta}", flush=True)
    fa = FaceAnalyzer(use_gpu=False)
    emb, archivo, bbox, score = [], [], [], []
    t0 = time.time()
    for i, fp in enumerate(jpgs, 1):
        try:
            caras = fa.analyze(load_image_array(str(fp)))
        except Exception as e:
            print(f"  error {fp.name}: {e}", flush=True)
            continue
        for c in caras:
            emb.append(c["embedding"])
            archivo.append(fp.name)
            bbox.append(c["bbox"])
            score.append(c["det_score"])
        if i % 50 == 0:
            print(f"  {i}/{len(jpgs)}  caras={len(emb)}  {time.time() - t0:.0f}s", flush=True)
    np.savez(carpeta / "caras.npz", emb=np.asarray(emb, np.float32).reshape(-1, 512),
             archivo=np.asarray(archivo), bbox=np.asarray(bbox, np.int32).reshape(-1, 4),
             score=np.asarray(score, np.float32))
    print(f"listo: {len(emb)} caras en {len(set(archivo))} fotos, {time.time() - t0:.0f}s")


def buscar(carpeta: Path, selfie: str, umbral: float = UMBRAL):
    """Devuelve [(archivo, similitud)] ordenado, la mejor cara por foto."""
    caras = FaceAnalyzer(use_gpu=False).analyze(load_image_array(selfie), min_lado_rel=0.0)
    if not caras:
        sys.exit("no se detecto ninguna cara en la selfie")
    q = np.asarray(max(caras, key=lambda c: c["bbox"][2] - c["bbox"][0])["embedding"], np.float32)
    d = np.load(carpeta / "caras.npz")
    sim = d["emb"] @ q
    mejor = {}
    vivas = {p.name for p in (carpeta / "jpeg").iterdir()}  # las borradas despues del indexado no salen
    for a, s in zip(d["archivo"], sim):
        if s >= umbral and a in vivas and s > mejor.get(a, -1):
            mejor[a] = float(s)
    return sorted(mejor.items(), key=lambda x: -x[1])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta")
    ap.add_argument("--buscar")
    ap.add_argument("--umbral", type=float, default=UMBRAL)
    a = ap.parse_args()
    if a.buscar:
        for archivo, s in buscar(Path(a.carpeta), a.buscar, a.umbral):
            print(f"{s:.3f}  {archivo}")
    else:
        indexar(Path(a.carpeta))
