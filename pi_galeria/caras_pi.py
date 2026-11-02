"""Busqueda por selfie en la Pi: onnxruntime para los modelos, OpenCV para la imagen.

Usa los MISMOS modelos que caras_evento.py en el notebook (buffalo_l de InsightFace:
det_10g para detectar, w600k_r50 para el embedding), asi la selfie se compara
directo contra el caras.npz del evento sin reindexar.

    venv_caras/bin/python caras_pi.py <caras.npz> <selfie.jpg>   # JSON [[archivo, similitud], ...]

onnxruntime vive en venv_caras/ (python3 -m venv --system-site-packages + pip
install onnxruntime), no en el Python del sistema. No sirve cv2.dnn: el OpenCV
4.10 de Debian calcula mal det_10g en la Pi (puntaje 0,21 donde da 0,86).

Corre como proceso aparte desde galeria.py: el reconocedor ocupa ~400 MB y la Pi
tiene 1,8 GB, asi que la memoria se devuelve al terminar y un candado deja
correr una sola busqueda a la vez.
"""

import json
import sys
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODELOS = Path(__file__).resolve().parent / "modelos_caras"
UMBRAL = 0.40   # coseno; en el cumple KatiSasso las caras de otra persona quedan bajo 0,2
TAM_DET = 640
# donde caen los 5 puntos (ojos, nariz, comisuras) en el recorte de 112 px de ArcFace
PLANTILLA = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                      [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)


@lru_cache(maxsize=None)
def modelo(nombre):
    return ort.InferenceSession(str(MODELOS / nombre), providers=["CPUExecutionProvider"])


def correr(nombre, blob):
    s = modelo(nombre)
    return s.run(None, {s.get_inputs()[0].name: blob})


def detectar(img, umbral=0.5):
    """SCRFD (det_10g): [(score, bbox[4], kps[5,2])] en coordenadas de img."""
    h, w = img.shape[:2]
    escala = TAM_DET / max(h, w)
    lienzo = np.zeros((TAM_DET, TAM_DET, 3), np.uint8)
    lienzo[:int(h * escala), :int(w * escala)] = cv2.resize(img, (int(w * escala), int(h * escala)))
    blob = cv2.dnn.blobFromImage(lienzo, 1 / 128, (TAM_DET, TAM_DET), (127.5,) * 3, swapRB=True)
    salidas = [o.reshape(-1, o.shape[-1]) for o in correr("det_10g.onnx", blob)]
    caras = []
    for paso in (8, 16, 32):
        n = (TAM_DET // paso) ** 2 * 2          # 2 anclas por celda
        sc, bb, kp = (next(o for o in salidas if o.shape == (n, k)) for k in (1, 4, 10))
        ys, xs = np.mgrid[:TAM_DET // paso, :TAM_DET // paso]
        centros = np.repeat(np.stack([xs, ys], -1).reshape(-1, 2) * paso, 2, axis=0).astype(np.float32)
        ok = sc[:, 0] >= umbral
        c, b, k = centros[ok], bb[ok] * paso, kp[ok] * paso
        cajas = np.hstack([c - b[:, :2], c + b[:, 2:]]) / escala
        puntos = (np.repeat(c, 5, axis=0).reshape(-1, 5, 2) + k.reshape(-1, 5, 2)) / escala
        caras += list(zip(sc[ok, 0], cajas, puntos))
    if not caras:
        return []
    cajas = [[float(x1), float(y1), float(x2 - x1), float(y2 - y1)] for _, (x1, y1, x2, y2), _ in caras]
    keep = cv2.dnn.NMSBoxes(cajas, [float(s) for s, _, _ in caras], umbral, 0.4)
    return [caras[i] for i in np.array(keep).flatten()]


def embedding(img, kps):
    m, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), PLANTILLA, method=cv2.LMEDS)
    cara = cv2.warpAffine(img, m, (112, 112), borderValue=0)
    blob = cv2.dnn.blobFromImage(cara, 1 / 127.5, (112, 112), (127.5,) * 3, swapRB=True)
    e = correr("w600k_r50.onnx", blob)[0].flatten()
    return e / np.linalg.norm(e)


def buscar(indice, selfie, umbral=UMBRAL):
    img = cv2.imdecode(np.fromfile(str(selfie), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    caras = detectar(img)
    if not caras:
        return None
    _, caja, kps = max(caras, key=lambda c: (c[1][2] - c[1][0]) * (c[1][3] - c[1][1]))
    q = embedding(img, kps)
    d = np.load(indice)
    mejor = {}
    for a, s in zip(d["archivo"], d["emb"] @ q):
        a = str(a)
        if s >= umbral and s > mejor.get(a, -1):
            mejor[a] = round(float(s), 3)
    return sorted(mejor.items(), key=lambda x: -x[1])


if __name__ == "__main__":
    import fcntl  # solo Linux; el resto del modulo se prueba tambien en Windows
    with open(MODELOS / ".candado", "w") as candado:
        fcntl.flock(candado, fcntl.LOCK_EX)     # una busqueda a la vez: cada una pide ~400 MB
        print(json.dumps(buscar(sys.argv[1], sys.argv[2])))
