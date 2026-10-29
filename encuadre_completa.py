"""Encuadre al protagonista de la edicion completa, version simple (29/10/2026).

Regla medida contra los recortes de Renzo (344 de 2026-1/F1/F3/F4 y sus 49 de la F5; analisis en
_salidas_locales/encuadre/): recorta suave, se queda con ~0,82 del ancho (la mitad entre 0,79 y 0,87) y la misma
proporcion del cuadro; el protagonista queda un poco bajo el centro (0,53). Centrar en el protagonista o en el grupo
detectado quedo PEOR que recortar parejo; lo que mejor se le parecio en la F5 fue poner el centro a medio camino
entre el centro de la foto y el del grupo (error de centro 3,1 % del ancho, IoU 0,78). Nunca rota: el angulo es el
nivelado, aparte. Siguiente paso: decidir mejor quien entra en el grupo (segmentacion + sus recortes).

    python encuadre_completa.py <carpeta con RAW>   # recorte de cada foto
    python encuadre_completa.py                      # self-check
"""
import sys
from pathlib import Path

ANCHO = 0.82            # fraccion del ancho (y del alto: misma proporcion que el cuadro)
POSICION_VERTICAL = 0.53  # donde queda el centro del protagonista dentro del recorte (0 arriba, 1 abajo)
ASPECTO = 1.5           # ancho/alto del cuadro, para medir distancias en x


def grupo(prota, otros):
    """Caja del protagonista + quienes estan pegados a el (solapan o a menos de media altura suya, de tamaño
    parecido). Coordenadas 0-1 (x1, y1, x2, y2)."""
    h = prota[3] - prota[1]
    g = [prota] + [o for o in otros
                   if (o[3] - o[1]) > 0.6 * h and o[0] - h * 0.5 / ASPECTO <= prota[2] and o[2] + h * 0.5 / ASPECTO >= prota[0]]
    return min(b[0] for b in g), min(b[1] for b in g), max(b[2] for b in g), max(b[3] for b in g)


def recorte_regla(prota, otros=(), ancho=ANCHO):
    """-> (CropLeft, CropRight, CropTop, CropBottom). Sin protagonista: recorte parejo centrado."""
    if prota is None:
        m = round((1 - ancho) / 2, 4)
        return m, round(1 - m, 4), m, round(1 - m, 4)
    g = grupo(prota, list(otros))
    cx = (0.5 + (g[0] + g[2]) / 2) / 2
    cy = (prota[1] + prota[3]) / 2 + (0.5 - POSICION_VERTICAL) * ancho
    l, t = cx - ancho / 2, cy - ancho / 2
    # que el protagonista no quede afuera (si cabe), y el recorte dentro del cuadro
    l = min(max(l, prota[2] - ancho), prota[0]) if prota[2] - prota[0] <= ancho else l
    l, t = min(max(l, 0.0), 1 - ancho), min(max(t, 0.0), 1 - ancho)
    return round(l, 4), round(l + ancho, 4), round(t, 4), round(t + ancho, 4)


_det = {}


def detectar(ruta):
    """-> (protagonista, otros) en 0-1, desde el JPEG embebido del RAW, con los detectores de Autocull."""
    import cv2
    import numpy as np
    import rawpy
    from autocull.config import DEFAULT_CONFIG as CFG
    from autocull.analyzers.subject import SubjectDetector, detect_persons_simple
    from autocull.analyzers.pose import PoseDetector
    from autocull.extractors.preview import downsample_for_analysis
    from autocull.scoring.scorer import pick_protagonist
    if not _det:
        _det["s"], _det["p"] = SubjectDetector(CFG), PoseDetector(CFG)
        _det["s"].initialize(); _det["p"].initialize()
    with rawpy.imread(str(ruta)) as raw:
        t = raw.extract_thumb()
        img = cv2.imdecode(np.frombuffer(t.data, np.uint8), cv2.IMREAD_COLOR) if t.format == rawpy.ThumbFormat.JPEG \
            else raw.postprocess(half_size=True, use_camera_wb=True)[:, :, ::-1]
    img = downsample_for_analysis(img, CFG.analysis_megapixels)
    h, w = img.shape[:2]
    personas = _det["p"].detect_persons(img)
    if not personas:
        return None, []
    pelotas = [d for d in detect_persons_simple(img, CFG, detector=_det["s"]) if d["class_id"] == CFG.yolo_ball_class_id]
    k = pick_protagonist(personas, pelotas, img.shape, CFG)
    norm = lambda b: (b[0] / w, b[1] / h, b[2] / w, b[3] / h)
    return norm(personas[k]["bbox"]), [norm(p["bbox"]) for j, p in enumerate(personas) if j != k]


def recorte(ruta):
    """Recorte de la completa para un RAW; si la deteccion falla, recorte parejo centrado."""
    try:
        return recorte_regla(*detectar(ruta))
    except Exception:
        return recorte_regla(None)


def _prueba():
    l, r, t, b = recorte_regla((0.45, 0.2, 0.55, 0.8))           # protagonista al centro: recorte centrado
    assert abs((l + r) / 2 - 0.5) < 1e-3 and abs(r - l - ANCHO) < 1e-3
    l, r, t, b = recorte_regla((0.0, 0.2, 0.12, 0.8))            # pegado a la izquierda: se corre, sin salirse
    assert l == 0.0 and r == ANCHO
    l, r, t, b = recorte_regla((0.80, 0.3, 0.95, 0.9))           # a la derecha: lo deja adentro entero
    assert l <= 0.80 and r >= 0.95 and 0 <= l and r <= 1
    assert recorte_regla(None) == (0.09, 0.91, 0.09, 0.91)        # sin persona: parejo
    print("encuadre_completa: OK (centrado, bordes, protagonista adentro, sin persona)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        _prueba()
    else:
        for r in sorted(p for p in Path(sys.argv[1]).iterdir() if p.suffix.lower() == ".arw"):
            print(r.name, recorte(r))
