"""Calidad por cara para aprender a descartar: expresion (MediaPipe), pose y nitidez.

    python caras_calidad.py "C:\\Fotos\\<evento>"     # necesita caras.npz (caras_evento.py)

Deja calidad.npz en la carpeta del evento, alineado fila a fila con caras.npz:
  bs      (N, 52)  blendshapes de MediaPipe (sonrisa, parpadeo, mandibula...), NaN si no hallo cara
  pose    (N, 3)   pitch, yaw, roll en grados
  nitidez (N,)     varianza del Laplaciano de la cara llevada a 128 px
  lado    (N,)     lado corto del bbox relativo al lado corto de la foto
"""

import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mpt
from mediapipe.tasks.python import vision

from utils_images import load_image_array

MODELO = Path(__file__).parent / "autocull" / "models" / "face_landmarker.task"
MARGEN = 0.6  # 0.15-1.0 dan casi lo mismo (11-12 de 16); MediaPipe necesita ver algo de contorno para encontrar la cara


def nitidez(cara_bgr):
    g = cv2.resize(cv2.cvtColor(cara_bgr, cv2.COLOR_BGR2GRAY), (128, 128), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def main(carpeta: Path):
    d = np.load(carpeta / "caras.npz")
    archivo, bbox = d["archivo"], d["bbox"]
    n = len(archivo)
    bs = np.full((n, 52), np.nan, np.float32)
    pose = np.full((n, 3), np.nan, np.float32)
    nit = np.zeros(n, np.float32)
    lado = np.zeros(n, np.float32)
    nombres = None
    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mpt.BaseOptions(model_asset_path=str(MODELO)),
        output_face_blendshapes=True, output_facial_transformation_matrixes=True, num_faces=1))
    fotos = sorted(set(archivo))
    t0 = time.time()
    for k, foto in enumerate(fotos, 1):
        try:
            img = load_image_array(str(carpeta / "jpeg" / foto))
        except Exception as e:  # borrada en la revision mientras corria
            print(f"  salto {foto}: {e}", flush=True)
            continue
        H, W = img.shape[:2]
        for i in np.flatnonzero(archivo == foto):
            x1, y1, x2, y2 = bbox[i]
            nit[i] = nitidez(img[max(0, y1):y2, max(0, x1):x2])
            lado[i] = min(x2 - x1, y2 - y1) / min(H, W)
            m = int(MARGEN * max(x2 - x1, y2 - y1))
            crop = img[max(0, y1 - m):min(H, y2 + m), max(0, x1 - m):min(W, x2 + m)]
            r = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                   data=np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))))
            if not r.face_blendshapes:
                continue
            bs[i] = [c.score for c in r.face_blendshapes[0]]
            nombres = nombres or [c.category_name for c in r.face_blendshapes[0]]
            pose[i] = cv2.RQDecomp3x3(np.asarray(r.facial_transformation_matrixes[0])[:3, :3])[0]
        if k % 100 == 0:
            print(f"  {k}/{len(fotos)}  {time.time() - t0:.0f}s", flush=True)
    np.savez(carpeta / "calidad.npz", bs=bs, pose=pose, nitidez=nit, lado=lado,
             nombres=np.asarray(nombres or []))
    print(f"listo: {n} caras, {int(np.isfinite(bs[:, 0]).sum())} con MediaPipe, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
