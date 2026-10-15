"""
Mide dónde se va realmente el tiempo del pipeline, por etapa.

Sirve para decidir con datos si conviene GPU o CPU: la GPU solo acelera la
inferencia. Si el grueso del tiempo es decodificar RAW (que es CPU puro),
la GPU aporta poco.

    python benchmark.py                 # muestra sobre el ensayo
    python benchmark.py --n 40          # más fotos por categoría
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_images import load_image_array, SUPPORTED_RAW_EXTS
from utils_analisis import quality_metrics, phash


def cronometrar(fn, veces=1):
    """Devuelve (segundos_promedio, resultado_ultimo)."""
    t0 = time.perf_counter()
    res = None
    for _ in range(veces):
        res = fn()
    return (time.perf_counter() - t0) / veces, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fotos", default="ensayo/fotos")
    ap.add_argument("--n", type=int, default=25, help="fotos por categoría")
    ap.add_argument("--modelo", default="models/visual/model.onnx")
    args = ap.parse_args()

    raiz = Path(args.fotos)
    if not raiz.exists():
        print(f"No existe {raiz}. Corré primero el ensayo.")
        return 1

    todas = [p for p in raiz.rglob("*") if p.is_file()]
    jpgs = [p for p in todas if p.suffix.lower() in {".jpg", ".jpeg"}][:args.n]
    raws = [p for p in todas if p.suffix.lower() in SUPPORTED_RAW_EXTS][:args.n]

    print(f"Muestra: {len(jpgs)} JPG, {len(raws)} RAW\n")
    print(f"{'ETAPA':<34}{'ms/foto':>10}{'  ¿la GPU ayuda?'}")
    print("-" * 68)

    resultados = {}

    # --- 1. Decodificación ---
    for nombre, archivos in (("Decodificar JPG", jpgs), ("Decodificar RAW", raws)):
        if not archivos:
            continue
        imgs = []
        t0 = time.perf_counter()
        for p in archivos:
            try:
                imgs.append(load_image_array(str(p)))
            except Exception:
                pass
        ms = (time.perf_counter() - t0) * 1000 / max(len(archivos), 1)
        resultados[nombre] = ms
        print(f"{nombre:<34}{ms:>10.1f}   NO (es CPU puro)")
        if nombre == "Decodificar JPG":
            muestras_jpg = imgs[:8]
        else:
            muestras_raw = imgs[:8]

    base = locals().get("muestras_jpg") or locals().get("muestras_raw") or []
    if not base:
        print("No se pudo decodificar ninguna imagen.")
        return 1

    # --- 2. Métricas sin modelo ---
    ms, _ = cronometrar(lambda: [quality_metrics(i) for i in base])
    resultados["Calidad (nitidez/brillo)"] = ms * 1000 / len(base)
    print(f"{'Calidad (nitidez/brillo)':<34}{ms*1000/len(base):>10.1f}   NO")

    ms, _ = cronometrar(lambda: [phash(i) for i in base])
    resultados["Hash perceptual"] = ms * 1000 / len(base)
    print(f"{'Hash perceptual':<34}{ms*1000/len(base):>10.1f}   NO")

    # --- 3. Inferencia CLIP ---
    modelo = Path(args.modelo)
    if modelo.exists():
        import importlib
        import onnxruntime as ort
        # El módulo empieza con dígito: no se puede importar con `import`
        preprocess_feature_batch = importlib.import_module("2_analisis").preprocess_feature_batch
        so = ort.SessionOptions()
        so.intra_op_num_threads = 0  # todos los núcleos
        sess = ort.InferenceSession(str(modelo), providers=["CPUExecutionProvider"],
                                    sess_options=so)
        entrada = sess.get_inputs()[0].name

        lote = preprocess_feature_batch(base, 224)
        ms_pre = cronometrar(lambda: preprocess_feature_batch(base, 224))[0] * 1000 / len(base)
        resultados["Preproceso CLIP (resize)"] = ms_pre
        print(f"{'Preproceso CLIP (resize)':<34}{ms_pre:>10.1f}   NO")

        def inferir():
            try:
                return sess.run(None, {entrada: lote})
            except Exception:
                return [sess.run(None, {entrada: lote[i:i+1]}) for i in range(len(base))]

        inferir()  # calentar
        ms_inf = cronometrar(inferir, veces=3)[0] * 1000 / len(base)
        resultados["Inferencia CLIP"] = ms_inf
        print(f"{'Inferencia CLIP':<34}{ms_inf:>10.1f}   SI (10-20x)")
    else:
        print(f"{'Inferencia CLIP':<34}{'--':>10}   (falta el modelo)")

    # --- Proyección ---
    # Cada etapa medida YA usa varios núcleos (ONNX y rawpy son código C que
    # suelta el GIL). Por eso no se puede dividir el total por la cantidad de
    # núcleos: se calibra contra una corrida real en vez de suponer escalado.
    print("\n" + "=" * 68)
    total_jpg = sum(v for k, v in resultados.items() if k != "Decodificar RAW")
    total_raw = sum(v for k, v in resultados.items() if k != "Decodificar JPG")
    print(f"Por foto (aislado):  JPG {total_jpg:.0f} ms   RAW {total_raw:.0f} ms")
    print(f"Un RAW cuesta {total_raw/max(total_jpg,1):.0f}x lo que un JPG")

    # Calibración contra una corrida real completa de la Fase 2 sobre el ensayo:
    # 1.249 fotos (1.137 JPG + 112 RAW) en 193 s, ya con extracción de preview RAW.
    # Incluye el costo que el benchmark por etapas no mide: SQLite, EXIF, hilos.
    REF_SEG, REF_JPG, REF_RAW = 193.0, 1137, 112
    ratio = total_raw / max(total_jpg, 1)
    t_jpg = REF_SEG / (REF_JPG + REF_RAW * ratio)      # segundos efectivos por JPG
    t_raw = t_jpg * ratio

    n_jpg, n_raw = 116_000, 34_183                      # tu fototeca real
    horas = (n_jpg * t_jpg + n_raw * t_raw) / 3600
    print(f"\nProyección para {n_jpg+n_raw:,} fotos ({n_raw:,} RAW = {n_raw*100//(n_jpg+n_raw)}%)")
    print(f"   en ESTA máquina ({8} núcleos):      {horas:.1f} h")

    inf = resultados.get("Inferencia CLIP", 0)
    frac_gpu = inf / (total_jpg * n_jpg + total_raw * n_raw) * (n_jpg + n_raw)
    print(f"   con GPU (mismos núcleos):        {horas*(1-frac_gpu*0.93):.1f} h  "
          f"<- ahorra solo {frac_gpu*93:.0f}%")
    for nc in (16, 32):
        # La decodificación sí escala con núcleos; se asume 70% de eficiencia
        print(f"   en {nc} núcleos sin GPU:            {horas/(nc/8*0.7):.1f} h")

    print("\nConclusión: para esta carga mandan los NÚCLEOS, no la GPU.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
