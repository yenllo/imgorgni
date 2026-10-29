"""Reduccion de ruido de la edicion completa, por foto, segun el ruido medido a tamaño de entrega.

28/10/2026: la completa lleva el Denoise IA de Lightroom con TOPE 30, dosificado por foto (la profesional usa
0-100). La reduccion manual se descarto: editando la F5 Renzo vio que perdia demasiado detalle. Una sola cantidad
para todas no sirve: en la F5 las completas iban de ISO 200 limpio a ISO 8000 en modo APS-C, que al mismo ISO
tiene ~1,5x el ruido del cuadro completo porque se amplia mas.

El ruido se mide como lo vera el cliente: RAW revelado sin reduccion, con la exposicion que lleva la foto,
llevado a 3.840 px (el ancho de la completa), en las zonas lisas de sombras y medios tonos. La cantidad va en un
preset de Lightroom por escalon ("IMGorgni Denoise 10/20/30", solo "Quitar ruido"): Lightroom guarda la cantidad
dentro de un bloque codificado y no se puede escribir directo.

    python ruido_completa.py <carpeta> [--ev 0.6]   # grupo y preset de cada RAW
    python ruido_completa.py                        # self-check
"""
import sys
from pathlib import Path

import cv2
import numpy as np

ANCHO_ENTREGA = 3840

# (ruido minimo, grupo, preset de Denoise). Grupos medidos con las 50 completas de la F5 (27/10/2026):
# A ≤1,3 ISO 200-400 / B 1,9-3,0 ISO 500-1250 / C 3,6-5,5 ISO 6400-8000 FF / D 6,3-8,1 ISO 8000 APS-C.
# Tope 30 para la completa (Renzo, 28/10/2026); antes entregaba ~50 en todas. En DSC02453 (D) el 30 queda limpio
# y conserva la textura de cara y pelo.
GRUPOS = [
    (5.8, "D", "IMGorgni Denoise 30"),
    (3.2, "C", "IMGorgni Denoise 30"),
    (1.5, "B", "IMGorgni Denoise 20"),
    (0.0, "A", "IMGorgni Denoise 10"),
]


def ruido_imagen(rgb):
    """Ruido de luminancia (0-255) en el 20 % mas liso de las zonas de sombras y medios tonos."""
    y = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    base = cv2.GaussianBlur(y, (0, 0), 2)
    res = y - base
    estructura = cv2.GaussianBlur(np.abs(cv2.Sobel(base, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(base, cv2.CV_32F, 0, 1)), (0, 0), 4)
    T = 32
    tejas = sorted((estructura[i:i + T, j:j + T].mean(), res[i:i + T, j:j + T].std())
                   for i in range(0, y.shape[0] - T, T) for j in range(0, y.shape[1] - T, T)
                   if 25 <= base[i:i + T, j:j + T].mean() <= 140)
    if not tejas:
        return None
    return float(np.median([t[1] for t in tejas[: max(10, len(tejas) // 5)]]))


def ruido_raw(ruta, ev):
    import rawpy
    with rawpy.imread(str(ruta)) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True, bright=2 ** ev, output_bps=8)
    h, w = rgb.shape[:2]
    if w > ANCHO_ENTREGA:  # cuadro completo a la mitad da ~3.980 px; APS-C ~2.590 queda asi
        rgb = cv2.resize(rgb, (ANCHO_ENTREGA, round(h * ANCHO_ENTREGA / w)), interpolation=cv2.INTER_AREA)
    return ruido_imagen(rgb)


def grupo(ruido):
    """-> (grupo, preset de Denoise). Sin medicion (foto sin zonas lisas): grupo A, la reduccion minima."""
    return next((g, p) for minimo, g, p in GRUPOS if (ruido or 0.0) >= minimo)


def _prueba():
    rng = np.random.default_rng(0)
    for sigma, esperado in ((0.8, "A"), (2.4, "B"), (4.5, "C"), (7.5, "D")):
        gris = np.clip(80 + rng.normal(0, sigma, (600, 800)), 0, 255).astype(np.uint8)
        medido = ruido_imagen(np.dstack([gris] * 3))
        assert abs(medido - sigma) < 0.25 * sigma + 0.3, (sigma, medido)
        assert grupo(medido)[0] == esperado, (sigma, medido, grupo(medido)[0])
    assert grupo(None)[0] == "A"  # sin medicion: lo minimo
    cantidades = [int(grupo(n)[1].rsplit(" ", 1)[1]) for n in (1, 2, 4, 7)]
    assert cantidades == sorted(cantidades) and max(cantidades) <= 30, cantidades  # mas ruido, mas; tope 30
    print("ruido_completa: OK (ruido medido ~ sigma, grupos A-D, tope 30, sin medicion va al minimo)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        _prueba()
    else:
        ev = float(sys.argv[sys.argv.index("--ev") + 1]) if "--ev" in sys.argv else 0.6
        for r in sorted(p for p in Path(sys.argv[1]).iterdir() if p.suffix.lower() == ".arw"):
            n = ruido_raw(r, ev)
            print(r.name, f"{n:.2f}" if n is not None else "-", grupo(n)[0])
