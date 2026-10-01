r"""Calcula un umbral por etiqueta a partir de tus veredictos, y mide que cambiaria.

El problema que resuelve: `SceneClassifier.classify_batch` usa UN z-score global
(`min_score`) para las 101 etiquetas. Medido con tus propios veredictos, ese
unico umbral hace que `cat` acierte 87% y `fish underwater` 11%. No es que el
modelo sea malo: es que el corte que sirve para un concepto frecuente y visual
no sirve para uno raro.

Que hace:
  1. recalcula el z-score de cada etiqueta para las fotos que vos ya juzgaste
     (usa el embedding guardado: no abre ni una foto)
  2. para cada etiqueta busca el umbral que mejor separa tus SI de tus NO
  3. informa que pasaria con ese umbral, SIN tocar la base

    python calibrar_umbrales.py
    python calibrar_umbrales.py --guardar umbrales.json

Despues, aplicar es correr `recalcular_escenas.py` leyendo ese JSON: son minutos
sobre los embeddings ya guardados, no las horas de GPU de la Fase 2.
"""
import argparse
import collections
import json
import sqlite3
import sys

import numpy as np

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
EMB = r"C:\Renzo\Proyectos\IMGorgni\scene_embeddings.npz"
MIN_VEREDICTOS = 30      # con menos, el umbral que salga es ruido


def zscores(vec, matriz):
    """z-score de cada etiqueta para un embedding: cuantas desviaciones por
    encima de la similitud media queda. Es el mismo criterio que usa el
    pipeline, reproducido acá para poder medirlo."""
    sim = matriz @ vec
    return (sim - sim.mean()) / (sim.std() + 1e-8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guardar")
    a = ap.parse_args()

    data = np.load(EMB, allow_pickle=True)
    matriz = data["embeddings"].astype(np.float32)
    labels = [str(x) for x in data["labels"]]
    idx = {t: i for i, t in enumerate(labels)}
    print("vocabulario del modelo: %d etiquetas" % len(labels))

    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    c.row_factory = sqlite3.Row
    filas = c.execute("""SELECT v.photo_id, v.etiqueta, v.ok, pf.visual_embedding e
                           FROM etiqueta_veredicto v
                           JOIN photo_features pf ON pf.photo_id = v.photo_id
                          WHERE pf.visual_embedding NOT IN ('','[]')""").fetchall()
    print("veredictos con embedding: %s" % "{:,}".format(len(filas)))

    # z-score de cada (foto, etiqueta) juzgada
    cache = {}
    datos = collections.defaultdict(lambda: {"si": [], "no": []})
    for r in filas:
        t = r["etiqueta"]
        if t not in idx:
            continue
        pid = r["photo_id"]
        if pid not in cache:
            try:
                v = np.asarray(json.loads(r["e"]), dtype=np.float32)
                n = np.linalg.norm(v)
                cache[pid] = zscores(v / n if n else v, matriz)
            except Exception:
                cache[pid] = None
        z = cache[pid]
        if z is None:
            continue
        datos[t]["si" if r["ok"] else "no"].append(float(z[idx[t]]))

    print("\n%-24s %5s %5s | umbral | acierto con  | acierto" %
          ("etiqueta", "sí", "no", ))
    print("%-24s %5s %5s |  nuevo | el global 2.0 | calibrado" % ("", "", ""))
    print("-" * 78)

    umbrales = {}
    for t, d in sorted(datos.items(), key=lambda x: -(len(x[1]["si"]) + len(x[1]["no"]))):
        si, no = np.array(d["si"]), np.array(d["no"])
        n = len(si) + len(no)
        if n < MIN_VEREDICTOS or not len(si) or not len(no):
            continue
        # se prueba cada corte posible y se elige el de mejor F1: es el que
        # equilibra no perder fotos buenas contra no dejar entrar basura
        cands = np.unique(np.concatenate([si, no]))
        mejor, mejor_f1 = None, -1
        for u in cands:
            tp = int((si >= u).sum())
            fp = int((no >= u).sum())
            fn = int((si < u).sum())
            if tp == 0:
                continue
            prec = tp / (tp + fp)
            rec = tp / (tp + fn)
            f1 = 2 * prec * rec / (prec + rec)
            if f1 > mejor_f1:
                mejor, mejor_f1 = float(u), f1
        if mejor is None:
            continue
        umbrales[t] = round(mejor, 3)
        # acierto con el umbral global de 2.0 vs el calibrado
        glob_tp = int((si >= 2.0).sum()); glob_fp = int((no >= 2.0).sum())
        prec_glob = 100 * glob_tp / (glob_tp + glob_fp) if (glob_tp + glob_fp) else 0
        cal_tp = int((si >= mejor).sum()); cal_fp = int((no >= mejor).sum())
        prec_cal = 100 * cal_tp / (cal_tp + cal_fp) if (cal_tp + cal_fp) else 0
        print("%-24s %5d %5d | %6.2f | %11.1f%% | %6.1f%%"
              % (t[:24], len(si), len(no), mejor, prec_glob, prec_cal))

    print("\netiquetas calibradas: %d (las que tienen %d+ veredictos)"
          % (len(umbrales), MIN_VEREDICTOS))
    print("las otras %d se quedan con el umbral global hasta que las revises"
          % (len(labels) - len(umbrales)))
    if a.guardar:
        with open(a.guardar, "w", encoding="utf-8") as fh:
            json.dump(umbrales, fh, indent=1, ensure_ascii=False)
        print("umbrales ->", a.guardar)


if __name__ == "__main__":
    main()
