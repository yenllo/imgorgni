"""
Puntúa cada modelo de embeddings contra las etiquetas que hiciste a mano.

Corre los modelos disponibles sobre las fotos del set de validación, predice la
categoría de carpeta de cada una, y la compara con tu etiqueta. Reporta
precisión global, por categoría, y las confusiones más frecuentes.

    python puntuar_validacion.py
    python puntuar_validacion.py --min-score 2.0
"""
import argparse
import importlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, ".")
from utils_images import load_image_array
from utils_analisis import (SceneClassifier, scene_to_category, clasificar_dispositivo,
                            es_no_foto, quality_metrics)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

m2 = importlib.import_module("2_analisis")

# (etiqueta, modelo visual, escenas, tamaño de entrada)
MODELOS = [
    ("B-32",    "models/visual/model.onnx",           "scene_embeddings.npz",         224),
    ("SigLIP1", "models_so400m/visual/model.onnx",    "scene_embeddings_so400m.npz",  384),
    ("SigLIP2", "models_siglip2/visual/model.onnx",   "scene_embeddings_siglip2.npz", 384),
]


def cargar_verdad(out: Path):
    etq_path = out / "etiquetas_validacion.json"
    mapa_path = out / "mapa_validacion.json"
    if not etq_path.exists():
        print(f"❌ Falta {etq_path}. Etiquetá primero con etiquetar.html y exportá.")
        sys.exit(1)
    etiquetas = json.loads(etq_path.read_text(encoding="utf-8"))
    mapa = json.loads(mapa_path.read_text(encoding="utf-8"))
    # id -> (ruta, categoria correcta)
    verdad = {}
    for id_, cat in etiquetas.items():
        if id_ in mapa and Path(mapa[id_]).exists():
            verdad[id_] = (mapa[id_], cat)
    return verdad


def predecir_categoria(clf, emb, camera_model, fp, quality, min_score):
    if es_no_foto(camera_model, fp, quality):
        return "_No son fotos"
    escenas = clf.classify_batch([emb], top_k=3, min_score=min_score)[0]
    disp = clasificar_dispositivo(camera_model, fp)
    return scene_to_category(escenas, quality, disp)


def evaluar_modelo(nombre, mdl, esc, px, verdad, min_score):
    if not Path(mdl).exists() or not Path(esc).exists():
        return None
    m2.configurar_preproceso(mdl)
    so = ort.SessionOptions(); so.intra_op_num_threads = 0
    sess = ort.InferenceSession(mdl, providers=["CPUExecutionProvider"], sess_options=so)
    inp = sess.get_inputs()[0]
    clf = SceneClassifier(esc)

    aciertos = 0
    por_cat_total = Counter()
    por_cat_ok = Counter()
    confusiones = Counter()

    for id_, (fp, real) in verdad.items():
        try:
            img = load_image_array(fp)
        except Exception:
            continue
        emb = np.asarray(sess.run(None, {inp.name:
                m2.preprocess_feature_batch([img], px)})[0]).squeeze().tolist()
        q = quality_metrics(img)
        # camera_model no está en el set: se deduce lo mínimo del nombre/ext
        pred = predecir_categoria(clf, emb, None, fp, q, min_score)

        por_cat_total[real] += 1
        if pred == real:
            aciertos += 1
            por_cat_ok[real] += 1
        else:
            confusiones[(real, pred)] += 1

    total = sum(por_cat_total.values())
    return {
        "nombre": nombre, "total": total, "aciertos": aciertos,
        "precision": aciertos / total if total else 0,
        "por_cat_total": por_cat_total, "por_cat_ok": por_cat_ok,
        "confusiones": confusiones,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validacion")
    ap.add_argument("--min-score", type=float, default=1.5)
    args = ap.parse_args()

    verdad = cargar_verdad(Path(args.out))
    print(f"Set de validación: {len(verdad)} fotos etiquetadas a mano\n")

    resultados = []
    for nombre, mdl, esc, px in MODELOS:
        r = evaluar_modelo(nombre, mdl, esc, px, verdad, args.min_score)
        if r:
            resultados.append(r)
            print(f"  {nombre:<9} {r['aciertos']:>3}/{r['total']}  "
                  f"= {r['precision']*100:5.1f}% de precisión")
        else:
            print(f"  {nombre:<9} (modelo no disponible, salteado)")

    if not resultados:
        return

    mejor = max(resultados, key=lambda r: r["precision"])
    print(f"\n🏆 Mejor: {mejor['nombre']} con {mejor['precision']*100:.1f}%\n")

    print("=" * 60)
    print(f"PRECISIÓN POR CATEGORÍA ({mejor['nombre']})")
    print("=" * 60)
    for cat in sorted(mejor["por_cat_total"], key=lambda c: -mejor["por_cat_total"][c]):
        tot = mejor["por_cat_total"][cat]
        ok = mejor["por_cat_ok"][cat]
        print(f"  {cat:<20} {ok:>3}/{tot:<3} = {ok/tot*100:5.0f}%")

    print("\n" + "=" * 60)
    print(f"CONFUSIONES MÁS FRECUENTES ({mejor['nombre']})")
    print("=" * 60)
    for (real, pred), n in mejor["confusiones"].most_common(10):
        print(f"  {n:>2}x  {real:<18} -> {pred}")

    print("\nSi una categoría rinde mal, se ajusta su regla en utils_analisis.py")
    print("(CATEGORY_RULES) o el vocabulario de escenas, y se vuelve a puntuar.")


if __name__ == "__main__":
    main()
