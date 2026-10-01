r"""Aprende un ranker personal a partir de selecciones pasadas de Renzo.

    $AC = "C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"
    & $AC -m autocull aprender --par <carrete> <seleccion> [--par ...] -o ranker.json
    & $AC -m autocull <carpeta> --dry-run --ranker ranker.json      # usarlo
    & $AC -m autocull.aprender --prueba                             # self-check con datos sinteticos

- <seleccion> es una CARPETA con las elegidas (lo exportado a `EdicionBasica`, `RAW\Seleccionadas`...)
  o un CSV `archivo,tomada,elegida` (p. ej. lo que eligio un cliente en la galeria de la Pi; ver
  `data/selecciones_renato/`). Va por carrete a proposito: los DSC se repiten entre fechas, asi que
  una etiqueta "global" por nombre marcaria fotos de otros carretes.
- Positivos: las fotos del carrete que estan en la seleccion. Se cruzan por nombre sin extension y
  sin sufijo " (2)"/"(2)"; si el nombre se repite en el carrete (las tarjetas reinician la
  numeracion DSC), desempata la fecha de captura (EXIF de la carpeta, `tomada` del CSV).
- Negativos: el resto del carrete que paso los filtros duros. Lo rechazado por movida, ojos
  cerrados, etc. NO entra: el ranker solo ordena lo que ya sobrevivio, igual que el percentil.

Features: las que Autocull ya calcula por foto, normalizadas como z-score DENTRO de cada carrete
(el puntaje de Autocull tambien es relativo a la sesion: la nitidez absoluta depende del ISO y la
luz de esa noche). Modelo: regresion logistica con L2 y peso por clase (las elegidas son pocas).
Se guarda como JSON legible, un peso por feature, para poder auditar que aprendio.

Las features de cada carrete se miden una vez con el codigo actual y se guardan en `--cache`
(por defecto `_autocull_aprender\`). Si cambia el analisis, borra el cache.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys

import numpy as np

# (nombre en el JSON del modelo, como se saca del resultado del pipeline)
FEATURES = [
    ("nitidez_global", lambda t1, t2: t1.get("snr_sharpness", 0.0)),
    ("fft_movimiento", lambda t1, t2: t1.get("fft_blur", 0.0)),
    ("nitidez_sujeto", lambda t1, t2: t2.get("best_subject_snr", 0.0)),
    ("brillo_sujeto", lambda t1, t2: t2.get("best_subject_brightness", 0.0)),
    ("confianza_yolo", lambda t1, t2: t2.get("best_subject_confidence", 0.0)),
    ("pelota_en_sujeto", lambda t1, t2: t2.get("ball_on_subject", 0.0)),
    ("tamano_sujeto", lambda t1, t2: t2.get("subject_fill", 0.0)),
    ("margen_recorte", lambda t1, t2: t2.get("crop_potential", 0.0)),
    ("cara_visible", lambda t1, t2: t2.get("pose_face_visible", 0.0)),
    ("zancada", lambda t1, t2: t2.get("pose_dynamic_stride", 0.0)),
    ("penalizacion_cara", lambda t1, t2: t2.get("face_expression_penalty", 0.0)),
    ("personas", lambda t1, t2: t2.get("persons_detected", 0)),
]
NOMBRES = [n for n, _ in FEATURES]
_ELEGIBLES = {None, "", "below_final_percentile", "burst_duplicate"}


# ---------------------------------------------------------------- datos

def elegible(r: dict) -> bool:
    """Paso todos los filtros duros: es lo que el ranker tiene que ordenar."""
    return bool(r.get("tier2")) and (r.get("decision") == "PASS" or r.get("reject_reason") in _ELEGIBLES)


def vector(r: dict) -> list[float]:
    t1, t2 = r.get("tier1") or {}, r.get("tier2") or {}
    return [float(f(t1, t2) or 0.0) for _, f in FEATURES]


def zscore(X: np.ndarray) -> np.ndarray:
    """Normaliza por columna dentro de UN carrete (std 0 -> la columna queda en 0)."""
    if len(X) == 0:
        return X
    mu, sd = X.mean(axis=0), X.std(axis=0)
    constante = sd <= 1e-9 * (np.abs(mu) + 1.0)  # 0.7 repetido da sd ~1e-16, no 0: seria ruido amplificado
    return np.where(constante, 0.0, (X - mu) / np.where(constante, 1.0, sd))


def clave(nombre: str) -> str:
    """DSC00281 (2).JPG -> dsc00281 ; DSC00921(2).ARW -> dsc00921."""
    base = os.path.splitext(os.path.basename(nombre))[0]
    return re.sub(r"\s*\(\d+\)$", "", base).strip().lower()


def _fecha(r_o_exif) -> str:
    dt = (r_o_exif or {}).get("datetime_obj")
    return str(dt)[:19] if dt else ""


def leer_seleccion(seleccion: str) -> dict[str, set[str]]:
    """{clave: {fechas de captura}} de las elegidas, desde una carpeta o un CSV archivo,tomada,elegida."""
    elegidas: dict[str, set[str]] = {}
    if seleccion.lower().endswith(".csv"):
        with open(seleccion, encoding="utf-8") as fh:
            for f in csv.DictReader(fh):
                if str(f.get("elegida", "1")).strip() == "1":
                    elegidas.setdefault(clave(f["archivo"]), set()).add((f.get("tomada") or "")[:19])
        return elegidas
    from autocull.extractors.metadata import read_exif
    from autocull.extractors.preview import find_images
    archivos, _fmt = find_images(seleccion)
    for p in archivos:
        elegidas.setdefault(clave(p), set()).add(_fecha(read_exif(p)))
    return elegidas


def etiquetar(resultados: list[dict], elegidas: dict[str, set[str]]) -> list[int]:
    """1/0 por resultado, segun *elegidas* = {clave: {fechas}} de leer_seleccion()."""
    repetidas = {}
    for r in resultados:
        k = clave(r["filepath"])
        repetidas[k] = repetidas.get(k, 0) + 1
    y = []
    for r in resultados:
        k = clave(r["filepath"])
        if k not in elegidas:
            y.append(0)
        elif repetidas[k] > 1 and "" not in elegidas[k]:
            y.append(int(_fecha(r.get("exif")) in elegidas[k]))  # mismo nombre en otra tarjeta
        else:
            y.append(1)
    return y


# Subcarpetas que son COPIAS de fotos del carrete (lo elegido, lo editado, lo vendido), nunca el
# carrete crudo. find_images es recursivo: sin esto, en PrimeLeague/ entrarian Seleccion/ y
# Exportacion/ y cada elegida apareceria dos o tres veces, una de ellas ya editada.
_DERIVADAS = re.compile(r"^(export\w*|exportaci[oó]n|selecci[oó]n\w*|seleccionadas|compras|entregados?|"
                        r"marca\s*de\s*agua|marcadeagua|edici[oó]n.*|entrega.*|_selected|_autocull\w*)$", re.I)


def archivos_carrete(carrete: str, seleccion: str | None) -> list[str]:
    from autocull.extractors.preview import find_images
    archivos, _fmt = find_images(carrete)
    sel = os.path.abspath(seleccion).lower() if seleccion else None
    salida = []
    for p in archivos:
        if sel and os.path.abspath(p).lower().startswith(sel + os.sep):
            continue
        carpetas = os.path.relpath(os.path.dirname(p), carrete).split(os.sep)
        if not any(_DERIVADAS.match(c.strip()) for c in carpetas):
            salida.append(p)
    return salida


def cargar_carrete(carrete: str, cache_dir: str, seleccion: str | None = None) -> list[dict]:
    """Resultados del pipeline para un carrete: del cache, o se analiza una vez y se cachea.

    No reusa `autocull_report.json` viejos a proposito: los de antes del 1/10/2026 no traen
    ball_on_subject/subject_fill ni la nitidez medida sobre el cuerpo, y mezclarlos entrenaria con
    dos mediciones distintas. Si cambia el analisis, borra el cache y se vuelve a medir.
    """
    from autocull.config import AnalysisConfig
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, hashlib.sha1(os.path.abspath(carrete).lower().encode()).hexdigest()[:16] + ".json")
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    from autocull.pipeline import run_pipeline
    cfg = AnalysisConfig()
    cfg.__post_init__()
    archivos = archivos_carrete(carrete, seleccion)
    print(f"analizando {carrete}: {len(archivos)} fotos (una sola vez, queda en {cache})", flush=True)
    res = run_pipeline("", cfg, files=archivos)
    json.dump(res, open(cache, "w", encoding="utf-8"), default=str)
    return json.loads(json.dumps(res, default=str))


# ---------------------------------------------------------------- modelo

def entrenar(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 3000, lr: float = 0.1):
    """Regresion logistica por descenso de gradiente, con peso por clase y L2. -> (w, b)."""
    n, d = X.shape
    pos = max(1, int(y.sum()))
    peso = np.where(y == 1, (n - pos) / pos, 1.0)  # las elegidas son pocas: que pesen lo mismo
    peso = peso / peso.mean()
    w, b = np.zeros(d), 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(X @ w + b)))
        g = peso * (p - y)
        w -= lr * (X.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return w, float(b)


def puntuar(modelo: dict, resultados: list[dict]) -> np.ndarray:
    """Probabilidad de 'la elegiria Renzo' para cada resultado, normalizando DENTRO de esta sesion."""
    X = zscore(np.array([vector(r) for r in resultados], dtype=float))
    w = np.array([modelo["pesos"].get(n, 0.0) for n in NOMBRES])
    return 1.0 / (1.0 + np.exp(-(X @ w + modelo["sesgo"])))


def precision_at(puntajes, y, k: int) -> float:
    if len(y) == 0:
        return 0.0
    top = np.argsort(-np.asarray(puntajes, dtype=float), kind="mergesort")[:k]
    return float(np.mean([y[i] for i in top]))


def _modelo(w, b, extra=None) -> dict:
    m = {"pesos": {n: round(float(v), 4) for n, v in zip(NOMBRES, w)}, "sesgo": round(b, 4),
         "normalizacion": "z-score por carrete sobre las fotos que pasaron los filtros duros"}
    m.update(extra or {})
    return m


def validar(conjuntos: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]]) -> dict:
    """Dejar-un-carrete-afuera: precision@10/@30 del ranker vs el final_score actual."""
    salida = {}
    for i, (nombre, Xz, y, base) in enumerate(conjuntos):
        resto = [c for j, c in enumerate(conjuntos) if j != i]
        w, b = entrenar(np.vstack([c[1] for c in resto]), np.concatenate([c[2] for c in resto]))
        p = Xz @ w + b
        salida[nombre] = {k: {"ranker": round(precision_at(p, y, n), 3), "final_score": round(precision_at(base, y, n), 3)}
                          for k, n in (("p@10", 10), ("p@30", 30))}
        salida[nombre]["elegidas"] = int(y.sum())
        salida[nombre]["fotos"] = int(len(y))
    return salida


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m autocull aprender")
    ap.add_argument("--par", nargs=2, action="append", metavar=("CARRETE", "SELECCION"), default=[],
                    help="carrete completo y la carpeta con las que elegiste de el")
    ap.add_argument("--cache", default="_autocull_aprender")
    ap.add_argument("-o", "--salida", default="ranker.json")
    a = ap.parse_args(argv)
    if not a.par:
        ap.error("hace falta al menos un --par CARRETE SELECCION")

    conjuntos = []
    for carrete, seleccion in a.par:
        # Una corrida son horas: un par malo se salta con aviso, no tumba a los demas.
        try:
            res = [r for r in cargar_carrete(carrete, a.cache, seleccion) if elegible(r)]
            y = np.array(etiquetar(res, leer_seleccion(seleccion)))
        except Exception as e:
            print(f"SALTO {carrete}: {e}", file=sys.stderr, flush=True)
            continue
        print(f"{carrete}: {len(res)} fotos elegibles, {int(y.sum())} elegidas", flush=True)
        if y.sum() == 0:
            # Todo un carrete como "no elegida" seria mentira: casi seguro los nombres no calzan.
            print("  SALTO: ninguna elegida calzo por nombre; revisa la seleccion", file=sys.stderr, flush=True)
            continue
        Xz = zscore(np.array([vector(r) for r in res], dtype=float))
        base = np.array([float(r.get("final_score") or 0.0) for r in res])
        conjuntos.append((carrete, Xz, y, base))
    if not conjuntos:
        sys.exit("ningun carrete sirvio: no hay nada que aprender")

    X = np.vstack([c[1] for c in conjuntos])
    y = np.concatenate([c[2] for c in conjuntos])
    if y.sum() == 0:
        sys.exit("ninguna foto elegida: no hay nada que aprender")
    w, b = entrenar(X, y)
    modelo = _modelo(w, b, {"carretes": [c[0] for c in conjuntos], "fotos": int(len(y)), "elegidas": int(y.sum())})
    if len(conjuntos) >= 2:
        modelo["validacion_dejando_un_carrete_afuera"] = validar(conjuntos)
    else:
        print("con un solo carrete no hay validacion: suma otro --par para medir si generaliza")
    json.dump(modelo, open(a.salida, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(modelo, ensure_ascii=False, indent=1))


def _prueba():
    rng = np.random.default_rng(7)
    assert clave(r"K:\x\DSC00281 (2).JPG") == "dsc00281" and clave("DSC00921(2).ARW") == "dsc00921"

    def carrete(n, sesgo_iso):
        """Fotos sinteticas: Renzo elige las con pelota en el sujeto y sujeto nitido."""
        res, y = [], []
        for i in range(n):
            pelota, nit = rng.random(), rng.normal(300 * sesgo_iso, 40)
            elegida = int(pelota > 0.7 and nit > 300 * sesgo_iso)
            res.append({"filepath": f"DSC{i:05d}.JPG", "decision": "PASS", "final_score": rng.random() * 100,
                        "tier1": {"snr_sharpness": rng.normal(150, 20), "fft_blur": 0.7},
                        "tier2": {"best_subject_snr": nit, "ball_on_subject": pelota, "subject_fill": rng.random(),
                                  "persons_detected": int(rng.integers(1, 10))}})
            y.append(elegida)
        return res, np.array(y)

    conjuntos = []
    for k, iso in enumerate((1.0, 0.6, 1.4)):  # 3 noches con distinta luz: por eso el z-score por carrete
        res, y = carrete(400, iso)
        assert all(elegible(r) for r in res)
        Xz = zscore(np.array([vector(r) for r in res]))
        conjuntos.append((f"c{k}", Xz, y, np.array([r["final_score"] for r in res])))
    w, b = entrenar(np.vstack([c[1] for c in conjuntos]), np.concatenate([c[2] for c in conjuntos]))
    pesos = dict(zip(NOMBRES, w))
    assert pesos["pelota_en_sujeto"] > 0.5 and pesos["nitidez_sujeto"] > 0.5, pesos
    assert abs(pesos["tamano_sujeto"]) < pesos["pelota_en_sujeto"] / 2, pesos  # ruido no deberia pesar
    v = validar(conjuntos)
    for nombre, m in v.items():  # el ranker le gana al final_score aleatorio en cada carrete dejado afuera
        assert m["p@30"]["ranker"] > m["p@30"]["final_score"] + 0.2, (nombre, m)
    # puntuar() con un modelo guardado reproduce el orden
    modelo = json.loads(json.dumps(_modelo(w, b)))
    res, y = carrete(200, 1.0)
    assert precision_at(puntuar(modelo, res), y, 20) > 0.6
    # las copias derivadas no son parte del carrete
    for c in ("Seleccion", "Exportacion", "EdicionBasica", "Marca De Agua", "Compras", "export"):
        assert _DERIVADAS.match(c), c
    for c in ("RAW", "Jpeg", "103MSDCF", "2026-07-11 F9 (Final)"):
        assert not _DERIVADAS.match(c), c
    # seleccion desde CSV (galeria): nombre repetido en el carrete -> desempata la hora de captura
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
        fh.write("archivo,tomada,elegida\nDSC2.JPG,2026-09-12 19:00:01,1\nDSC3.JPG,,0\n")
    sel = leer_seleccion(fh.name)
    os.unlink(fh.name)
    assert sel == {"dsc2": {"2026-09-12 19:00:01"}}, sel
    carr = [{"filepath": "a/DSC1.JPG"}, {"filepath": "a/DSC2.JPG", "exif": {"datetime_obj": "2026-09-12 18:00:00"}},
            {"filepath": "b/DSC2 (2).JPG", "exif": {"datetime_obj": "2026-09-12 19:00:01"}}, {"filepath": "DSC3.JPG"}]
    assert etiquetar(carr, sel) == [0, 0, 1, 0], etiquetar(carr, sel)
    print("ok", {k: round(float(x), 2) for k, x in pesos.items() if abs(x) > 0.05}, v["c0"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--prueba"]:
        _prueba()
    else:
        main()
