r"""Mide que tan editorial es el puntaje de Autocull contra la muestra juzgada a ciegas.

    $AC = "C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"
    & $AC -m autocull.evaluar_muestra <carpeta_con_las_fotos> [--ranker ranker.json] [--muestra ...]

La carpeta tiene que tener las fotos de la muestra (DSC09526.JPG, ...); se cruzan por nombre. Reporta:
  - Spearman entre final_score y votos_si (5 jueces): 1 = Autocull ordena como los jueces.
  - cuantas fotos con 0 votos quedan en el top 5 de Autocull (lo que mas duele: basura arriba).
  - la tabla por foto con cada componente, para ver POR QUE quedo donde quedo.

OJO con la escala: el puntaje se calcula con percentiles DENTRO de la sesion, y aca la sesion son
solo las fotos de la muestra (20), no el carrete entero. Sirve para comparar antes/despues del
mismo codigo sobre las mismas fotos, no como numero absoluto.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
MUESTRA = os.path.join(os.path.dirname(AQUI), "data", "muestra_final_2026-07-11.json")


def rangos(x):
    """Rangos promedio (empates comparten rango), como scipy.stats.rankdata."""
    x = np.asarray(x, dtype=float)
    orden = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[orden] = np.arange(len(x))
    for v in np.unique(x):
        m = x == v
        r[m] = r[m].mean()
    return r


def spearman(a, b):
    ra, rb = rangos(a), rangos(b)
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def metricas(puntajes, votos, k=5):
    """(spearman, cuantas con 0 votos en el top k)."""
    top = np.argsort(-np.asarray(puntajes, dtype=float), kind="mergesort")[:k]
    return spearman(puntajes, votos), int(sum(votos[i] == 0 for i in top))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta")
    ap.add_argument("--muestra", default=MUESTRA)
    ap.add_argument("--ranker", default="", help="ranker.json de aprender: mide el orden aprendido")
    a = ap.parse_args()

    from autocull.config import AnalysisConfig
    from autocull.pipeline import run_pipeline

    muestra = {f["archivo"].lower(): f for f in json.load(open(a.muestra, encoding="utf-8"))["fotos"]}
    fotos = [p for p in glob.glob(os.path.join(a.carpeta, "*")) if os.path.basename(p).lower() in muestra
             and os.path.getsize(p) > 0]
    if len(fotos) < len(muestra):
        print(f"ojo: encontre {len(fotos)} de {len(muestra)} fotos de la muestra", file=sys.stderr)
    cfg = AnalysisConfig()
    cfg.__post_init__()
    res = run_pipeline("", cfg, files=sorted(fotos), ranker=a.ranker or None)

    filas = []
    for r in res:
        m = muestra[os.path.basename(r["filepath"]).lower()]
        t1, t2 = r.get("tier1") or {}, r.get("tier2") or {}
        filas.append((r.get("final_score") or 0.0, m["votos_si"], os.path.basename(r["filepath"]), r.get("decision"),
                      r.get("reject_reason") or "", t1.get("snr_sharpness", 0), t1.get("fft_blur", 0),
                      t2.get("best_subject_snr", 0), t2.get("persons_detected", 0), t2.get("has_ball", False),
                      t2.get("crop_potential", 0), t2.get("pose_dynamic_stride", 0), t2.get("subject_fill", 0),
                      t2.get("ball_on_subject", 0), m["jueces"].get("claude", {}).get("motivo", "")))
    filas.sort(key=lambda f: -f[0])
    print(f"{'score':>6} {'si':>2} {'archivo':13} {'decision':8} {'snrG':>6} {'fft':>6} {'snrS':>6} "
          f"{'pers':>4} {'pel':>3} {'crop':>4} {'paso':>4} {'fill':>4} {'pelS':>4}  motivo claude")
    for f in filas:
        print(f"{f[0]:6.1f} {f[1]:2d} {f[2]:13} {f[3] or '':8} {f[5]:6.0f} {f[6]:6.1f} {f[7]:6.0f} "
              f"{f[8]:4d} {'si' if f[9] else '':>3} {f[10]:4.2f} {f[11]:4.2f} {f[12]:4.2f} {f[13]:4.2f}  "
              f"{f[4] + ' | ' if f[4] else ''}{f[14][:60]}")
    rho, malas = metricas([f[0] for f in filas], [f[1] for f in filas])
    print(f"\nSpearman(final_score, votos_si) = {rho:+.3f}   fotos con 0 votos en el top 5: {malas}")


if __name__ == "__main__" and sys.argv[1:] == ["--prueba"]:
    assert abs(spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1) < 1e-9
    assert abs(spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1) < 1e-9
    assert list(rangos([5, 1, 5])) == [1.5, 0.0, 1.5]
    assert metricas([9, 8, 7, 1], [0, 3, 0, 5], k=2) == (spearman([9, 8, 7, 1], [0, 3, 0, 5]), 1)
    print("ok")
elif __name__ == "__main__":
    main()
