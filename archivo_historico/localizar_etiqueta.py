r"""
Dice DONDE se ve una etiqueta dentro de una foto, dibujable como cuadro.

Las scene_tags salen de CLIP sobre la imagen ENTERA: dan un vector por foto y
no tienen noción de ubicación. Cuando una foto tiene gato y perro, la etiqueta
'cat' aplica a todo el cuadro y no se puede saber cual de los dos miro.

Esto lo resuelve por fuerza bruta, que es lo unico honesto sin un detector: se
corta la imagen en una grilla de recortes solapados, se pasa cada uno por el
MISMO encoder visual del pipeline, y se compara contra el embedding de texto de
la etiqueta (el de scene_embeddings.npz). El recorte que mas se parece es donde
esta la etiqueta.

Corre sobre la MINIATURA de 320px que ya esta cacheada, no sobre el original:
son ~10 pasadas de 32 ms y cero lecturas a K:.

Ojo con el BGR: el pipeline alimenta el modelo con imagenes de cv2 sin pasar a
RGB (2_analisis.py, preprocess_feature_batch). Sea acertado o no, hay que
repetirlo para que los puntajes sean comparables con los que asignaron la
etiqueta.
"""
import json
from pathlib import Path

import numpy as np

MEDIA = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
DESVIO = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
LADO = 224

_sesion = None
_etiquetas = None      # {etiqueta: vector de texto normalizado}


def _cargar(modelo="models/visual/model.onnx", escenas="scene_embeddings.npz"):
    global _sesion, _etiquetas
    if _sesion is None:
        import onnxruntime as ort
        _sesion = ort.InferenceSession(modelo, providers=["CPUExecutionProvider"])
    if _etiquetas is None:
        d = np.load(escenas, allow_pickle=True)
        m = d["embeddings"].astype(np.float32)
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-8)
        _etiquetas = {str(k): m[i] for i, k in enumerate(d["labels"])}
    return _sesion, _etiquetas


def _recortes(w, h, filas=3, solape=0.5):
    """Grilla solapada + el cuadro entero. Devuelve [(x1,y1,x2,y2)] en pixeles."""
    cajas = [(0, 0, w, h)]
    lw, lh = w / filas, h / filas
    paso_x, paso_y = lw * (1 - solape), lh * (1 - solape)
    y = 0.0
    while y + lh <= h + 1:
        x = 0.0
        while x + lw <= w + 1:
            cajas.append((int(x), int(y), int(min(x + lw, w)), int(min(y + lh, h))))
            x += paso_x
        y += paso_y
    return cajas


def localizar(ruta_imagen, etiqueta, filas=3, modelo=None, escenas=None):
    """[(caja_normalizada, puntaje)] ordenado de mejor a peor.

    La caja viene en 0..1 sobre el ancho y alto de la imagen, lista para
    dibujar encima de cualquier version reescalada. `puntaje` es el coseno
    contra el embedding de texto de la etiqueta.
    """
    import cv2
    ses, etqs = _cargar(*(x for x in (modelo, escenas) if x) or ())
    if etiqueta not in etqs:
        return []
    texto = etqs[etiqueta]
    img = cv2.imread(str(ruta_imagen), cv2.IMREAD_COLOR)   # BGR, como el pipeline
    if img is None:
        return []
    h, w = img.shape[:2]
    nombre = ses.get_inputs()[0].name

    salida = []
    for (x1, y1, x2, y2) in _recortes(w, h, filas):
        trozo = img[y1:y2, x1:x2]
        if trozo.size == 0:
            continue
        x = cv2.resize(trozo, (LADO, LADO), interpolation=cv2.INTER_AREA).astype(np.float32)
        x = ((x / 255.0) - MEDIA) / DESVIO
        x = np.transpose(x, (2, 0, 1))[None]
        emb = ses.run(None, {nombre: x})[0][0].astype(np.float32)
        n = float(np.linalg.norm(emb))
        if n <= 1e-8:
            continue
        salida.append(((x1 / w, y1 / h, x2 / w, y2 / h), float(np.dot(emb / n, texto))))
    salida.sort(key=lambda t: -t[1])
    return salida


def mejor_caja(ruta_imagen, etiqueta, **kw):
    """La caja mas parecida a la etiqueta, sin contar el cuadro entero.

    Devuelve (caja, puntaje, ventaja) donde `ventaja` es cuanto le gana al
    cuadro completo: si es <= 0 el modelo no ve la etiqueta en ninguna parte
    concreta —es una escena, no un objeto— y no conviene dibujar nada.
    """
    todo = localizar(ruta_imagen, etiqueta, **kw)
    if not todo:
        return None
    entero = next((p for c, p in todo if c == (0.0, 0.0, 1.0, 1.0)), None)
    parciales = [(c, p) for c, p in todo if c != (0.0, 0.0, 1.0, 1.0)]
    if not parciales:
        return None
    caja, punt = parciales[0]
    return caja, punt, (punt - entero if entero is not None else 0.0)


if __name__ == "__main__":
    import sys
    ruta, etq = sys.argv[1], sys.argv[2]
    r = mejor_caja(ruta, etq)
    print(json.dumps({"caja": r[0], "puntaje": r[1], "ventaja": r[2]} if r else None, indent=1))
