"""
Chequeos de la integración con los modelos de embedding.

Blinda dos hallazgos que costaron encontrar y que fallan EN SILENCIO
(no lanzan error, solo devuelven clasificaciones absurdas):

  1. SigLIP debe rellenar con </s> (id=1), no con <pad> (id=0). Estos ONNX no
     reciben attention_mask y SigLIP agrupa por atención sobre todos los
     tokens, así que el relleno se procesa como texto real.
  2. Cada familia normaliza distinto: CLIP usa los valores de OpenAI,
     SigLIP usa 0.5/0.5.

    python test_modelos.py
"""
import importlib
import sys
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def test_pad_id_por_familia():
    from precompute_scene_embeddings import MODELOS
    assert MODELOS["b32"][2] == 0, "CLIP rellena con 0"
    for k in ("siglip", "so400m"):
        assert MODELOS[k][2] == 1, (
            f"{k} debe rellenar con </s> (1). Con <pad> (0) el embedding de "
            "texto se corrompe y la clasificación da resultados absurdos.")
    print("OK: pad_id correcto por familia (CLIP=0, SigLIP=1)")


def test_normalizacion_por_familia():
    m2 = importlib.import_module("2_analisis")
    m2.configurar_preproceso("models/visual/model.onnx")
    mean_clip = m2._norm_actual[0]
    assert abs(float(mean_clip[0]) - 0.48145466) < 1e-4, f"CLIP: {mean_clip}"

    m2.configurar_preproceso("models_siglip_inexistente/visual/model.onnx")
    mean_sig = m2._norm_actual[0]
    assert abs(float(mean_sig[0]) - 0.5) < 1e-6, f"SigLIP por nombre: {mean_sig}"
    print("OK: normalización CLIP vs SigLIP")


def test_preprocess_cfg_manda():
    """Si el modelo trae preprocess_cfg.json, gana sobre la deducción por nombre."""
    cfg = Path("models_so400m/visual/preprocess_cfg.json")
    if not cfg.exists():
        print("SKIP: falta models_so400m (modelo no descargado)")
        return
    m2 = importlib.import_module("2_analisis")
    m2.configurar_preproceso("models_so400m/visual/model.onnx")
    assert abs(float(m2._norm_actual[0][0]) - 0.5) < 1e-6
    import json
    assert json.loads(cfg.read_text())["size"] == [384, 384]
    print("OK: preprocess_cfg.json se respeta (384px, mean 0.5)")


def test_zscore_es_invariante_a_escala():
    """
    La selección por z-score debe dar el mismo resultado con similitudes de
    escala distinta. CLIP da cosenos ~0.29 y SigLIP ~0.03 para el mismo acierto:
    un umbral absoluto sirve para uno y descarta todo con el otro.
    """
    from utils_analisis import SceneClassifier
    ruta = "scene_embeddings.npz"
    if not Path(ruta).exists():
        print("SKIP: falta scene_embeddings.npz")
        return
    clf = SceneClassifier(ruta)

    rng = np.random.default_rng(0)
    base = rng.normal(size=clf.dim).astype(np.float32)
    base /= np.linalg.norm(base)
    # Un embedding pegado a una etiqueta concreta, más ruido
    objetivo = clf.matrix[7]
    for mezcla in (0.9, 0.3):     # márgenes muy distintos
        v = mezcla * objetivo + (1 - mezcla) * base
        v /= np.linalg.norm(v)
        r = clf.classify_batch([v.tolist()], top_k=1, min_score=2.0)[0]
        assert r and r[0] == clf.labels[7], (
            f"con mezcla={mezcla} deberia detectar '{clf.labels[7]}', dio {r}")
    print("OK: z-score detecta igual con margen alto y bajo")


def test_dimension_coincide():
    """La matriz de escenas debe tener la dimensión del modelo visual que la generó."""
    from utils_analisis import SceneClassifier
    pares = [("scene_embeddings.npz", 512), ("scene_embeddings_so400m.npz", 1152)]
    vistos = 0
    for ruta, dim in pares:
        if not Path(ruta).exists():
            continue
        clf = SceneClassifier(ruta)
        assert clf.dim == dim, f"{ruta}: dim {clf.dim}, esperaba {dim}"
        assert len(clf.labels) == clf.matrix.shape[0]
        vistos += 1
    print(f"OK: dimensiones coherentes ({vistos} archivos de escenas)")


if __name__ == "__main__":
    test_pad_id_por_familia()
    test_normalizacion_por_familia()
    test_preprocess_cfg_manda()
    test_dimension_coincide()
    test_zscore_es_invariante_a_escala()
    print("\n✅ TODOS LOS CHEQUEOS DE MODELOS PASARON")
