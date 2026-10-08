r"""Modelo de color personal de Renzo (8/10/2026): temperatura y exposicion desde el RAW.

    python color_modelo.py rasgos     # rasgos de cada foto (color de la luz, escena, jugador, EXIF)
    python color_modelo.py probar     # prueba honesta: entrena sin una FECHA y predice esa fecha
    python color_modelo.py entrenar   # modelo final con todo -> _color\modelo.pkl

Aprende de las 1.507 fotos de LigaExScuola editadas por Renzo (catalogo + RAW + JPEG entregado; ver
color_datos.py, color_pi.py, color_analisis.py). Mira la foto -luz de la cancha en el RAW lineal, cuanto
pasto / gradas grises / cielo, brillo del jugador contra el fondo, EXIF- y propone temperatura y exposicion.
La temperatura se trabaja en mired (1e6/K): ahi el color cambia parejo.

Medido el 8/10/2026 dejando cada fecha afuera (12 fechas, 1.478 fotos):
  temperatura: valor fijo 255 K de error -> modelo 97 K; en las 201 que Renzo ajusto a mano 496 -> 207 K.
  exposicion:  0,29 -> 0,20.  En la F3 de tarde (5.150 K) el fijo erraba 1.326 K y el modelo 282 K.
  - El matiz es +30 en el 91% de las fotos: va fijo, el modelo no le gana a la constante.
  - CLIP ViT-L-14 como "vision general" no mejoro (99 vs 97 K): fuera.
  - "Despues" (acercar cada foto a las de su partido con luz parecida) tampoco mejoro (100 vs 97 K):
    queda la funcion, apagada (ALFA = 1).
  - La temperatura guardada SI es la que se ve: calza con el JPEG entregado (corr -0,82 en modo Auto).
    Excepcion: F5 y F8 "A medida", temperatura pegada fija y JPEG que no calza: etiqueta con ruido.
"""
import json
import math
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
DATOS = AQUI / "_color"
OBJETIVOS = ("mired", "Exposure2012")
MATIZ = 30            # la constante de Renzo (91% de sus fotos)
PESO_A_MANO = 3.0     # las fotos que Renzo ajusto una por una dicen mas que las pegadas en lote
ALFA = 1.0            # peso de la propuesta propia frente a su grupo; 1 = "despues" apagado (no mejoro)


def num(x, defecto=None):
    try:
        if isinstance(x, str) and "/" in x:
            a, b = x.split("/")
            return float(a) / float(b)
        return float(x)
    except (TypeError, ValueError, ZeroDivisionError):
        return defecto


def fecha_de(raw):
    """La carpeta de la fecha (el partido): agrupa para probar sin hacer trampa."""
    partes = raw.replace("\\", "/").split("/")
    i = max(i for i, p in enumerate(partes) if p.upper() == "RAW")
    return partes[i - 1]


def detector():
    sys.path.insert(0, str(AQUI))
    from autocull.analyzers.pose import PoseDetector
    d = PoseDetector()
    d.initialize()
    return d


def rasgos_foto(f, lin, vista, det):
    """Un vector de rasgos con nombre. lin: RAW lineal (camara, sin balance); vista: preview de camara (RGB)."""
    r = {}
    ex = f["exif"]
    iso, t, fn = num(ex.get("iso"), 3200), num(ex.get("exposicion"), 1 / 500), num(ex.get("f"), 2.8)
    r["log_iso"] = math.log2(iso)
    r["ev100"] = math.log2(fn * fn / t) - math.log2(iso / 100)      # luz de la escena: dia ~12, noche ~4
    eb = f["eb_camara"]
    r["cam_lr"], r["cam_lb"] = math.log(eb[0] / eb[1]), math.log(eb[2] / eb[1])
    r["recorte"] = f["recorte"]

    h, w = lin.shape[:2]
    v = cv2.resize(vista, (w, h), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(v, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
    lum = lin.mean(axis=2)
    ok = (lin.max(axis=2) < 0.9) & (lum > 1e-3)
    verde = ok & (hue > 60) & (hue < 170) & (sat > 0.2)
    neutro = ok & (sat < 0.15) & (val > 0.15)
    cielo = ok & np.zeros_like(ok)
    cielo[: h // 3] = (val[: h // 3] > 0.7) & (((hue[: h // 3] > 180) & (hue[: h // 3] < 250)) | (sat[: h // 3] < 0.1))
    cielo &= ok
    for nombre, m in (("verde", verde), ("neutro", neutro), ("cielo", cielo)):
        r[f"frac_{nombre}"] = float(m.mean())

    def croma(m):
        if m.sum() < 20:
            m = ok
        s = lin[m].mean(axis=0) + 1e-6
        return math.log(s[0] / s[1]), math.log(s[2] / s[1])

    # color de la luz, medido de varias formas (en el sensor, sin el balance de la camara)
    r["luz_lr"], r["luz_lb"] = croma(ok)                       # promedio de todo
    r["neu_lr"], r["neu_lb"] = croma(neutro)                   # lo que la camara ve gris (gradas, lineas)
    r["noverde_lr"], r["noverde_lb"] = croma(ok & ~verde)      # sin el pasto, que engaña al automatico
    alto = ok & (lum > np.percentile(lum[ok], 95)) if ok.sum() > 100 else ok
    r["alto_lr"], r["alto_lb"] = croma(alto)                   # lo mas claro (parche blanco)
    for p in (5, 50, 95):
        r[f"lum_p{p}"] = math.log(np.percentile(lum[ok], p) + 1e-5) if ok.any() else -12

    # el jugador: brillo de las personas contra el resto (contraluz de tarde)
    bgr = cv2.cvtColor(vista, cv2.COLOR_RGB2BGR)
    personas = det.detect_persons(bgr)
    sx, sy = w / vista.shape[1], h / vista.shape[0]
    masc = np.zeros((h, w), bool)
    area_max = 0.0
    for p in personas:
        x1, y1, x2, y2 = p["bbox"]
        masc[int(y1 * sy):int(y2 * sy) + 1, int(x1 * sx):int(x2 * sx) + 1] = True
        area_max = max(area_max, (x2 - x1) * (y2 - y1) / (vista.shape[0] * vista.shape[1]))
    r["n_personas"] = len(personas)
    r["area_jugador"] = area_max
    if (masc & ok).sum() > 10 and (~masc & ok).sum() > 10:
        r["jugador_vs_fondo"] = math.log(np.median(lum[masc & ok]) + 1e-5) - math.log(np.median(lum[~masc & ok]) + 1e-5)
        r["jugador_lum"] = math.log(np.median(lum[masc & ok]) + 1e-5)
    else:
        r["jugador_vs_fondo"], r["jugador_lum"] = 0.0, r["lum_p50"]
    return r


def rasgos():
    from PIL import Image
    tabla = json.loads((DATOS / "tabla.json").read_text(encoding="utf-8"))
    cache_f = DATOS / "rasgos_modelo.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    det = detector()
    nuevas = 0
    for f in tabla:
        clave = str(f["id"])
        if clave in cache:
            continue
        vista_p = DATOS / "salida" / f"{f['id']}.jpg"
        lin_p = DATOS / "salida" / f"{f['id']}.lin.npy"
        if not vista_p.exists() or not lin_p.exists():
            continue
        lin = np.load(lin_p).astype(np.float32)
        vista = np.asarray(Image.open(vista_p).convert("RGB"))
        cache[clave] = rasgos_foto(f, lin, vista, det)
        nuevas += 1
        if nuevas % 100 == 0:
            print(f"{nuevas} fotos", flush=True)
            cache_f.write_text(json.dumps(cache), encoding="utf-8")
    cache_f.write_text(json.dumps(cache), encoding="utf-8")
    print(f"rasgos: {nuevas} nuevas, {len(cache)} en total")


def conjunto():
    """(X, Y, pesos, fechas, nombres de rasgos, filas) de las fotos con rasgos y objetivos completos."""
    tabla = json.loads((DATOS / "tabla.json").read_text(encoding="utf-8"))
    cache = json.loads((DATOS / "rasgos_modelo.json").read_text(encoding="utf-8"))
    filas = []
    for f in tabla:
        r = cache.get(str(f["id"]))
        fin = f["final"]
        t, e = num(fin.get("Temperature")), num(fin.get("Exposure2012"))
        if r is None or t is None or e is None or e < -50:
            continue
        filas.append((f, r, {"mired": 1e6 / t, "Exposure2012": e}))
    nombres = sorted(filas[0][1])
    X = np.array([[r[k] for k in nombres] for _, r, _ in filas], dtype=np.float64)
    Y = np.array([[o[k] for k in OBJETIVOS] for _, _, o in filas])
    pesos = np.array([PESO_A_MANO if (f["temp_a_mano"] or f["expo_a_mano"]) else 1.0 for f, _, _ in filas])
    fechas = np.array([fecha_de(f["raw"]) for f, _, _ in filas])
    return X, Y, pesos, fechas, nombres, [f for f, _, _ in filas]


def nuevo_modelo():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.04, max_leaf_nodes=15,
                                         min_samples_leaf=15, l2_regularization=1.0)


def despues(pred, X, nombres, grupos, alfa):
    """Cada foto se acerca a la mediana de las fotos de su mismo partido con luz parecida (ev100 y color
    de la luz cercanos). alfa=1 deja la propuesta tal cual; 0 la reemplaza por el grupo."""
    if alfa >= 1:
        return pred
    i_ev, i_lr, i_lb = nombres.index("ev100"), nombres.index("luz_lr"), nombres.index("luz_lb")
    out = pred.copy()
    for g in set(grupos):
        idx = np.where(grupos == g)[0]
        for i in idx:
            d = np.hypot(np.hypot(X[idx, i_ev] - X[i, i_ev], (X[idx, i_lr] - X[i, i_lr]) / 0.05),
                         (X[idx, i_lb] - X[i, i_lb]) / 0.05)
            vecinos = idx[d < 1.5]
            if len(vecinos) >= 3:
                out[i] = alfa * pred[i] + (1 - alfa) * np.median(pred[vecinos], axis=0)
    return out


def kelvin_mae(m_real, m_pred):
    return float(np.mean(np.abs(1e6 / m_real - 1e6 / m_pred)))


def probar():
    X, Y, pesos, fechas, nombres, filas = conjunto()
    a_mano = np.array([f["temp_a_mano"] for f in filas])
    uniq = sorted(set(fechas))
    print(f"{len(Y)} fotos, {len(uniq)} fechas, temperatura a mano en {a_mano.sum()}")
    pred, base = np.zeros_like(Y), np.zeros_like(Y)
    for g in uniq:           # dejo una fecha afuera, entreno con el resto, predigo esa fecha
        tr, te = fechas != g, fechas == g
        for j in range(Y.shape[1]):
            pred[te, j] = nuevo_modelo().fit(X[tr], Y[tr, j], sample_weight=pesos[tr]).predict(X[te])
            base[te, j] = np.median(Y[tr, j])
    resultados = {"base (mediana fija)": base, "modelo": pred}
    for alfa in (0.5, 0.25):
        resultados[f"modelo + despues (alfa {alfa})"] = despues(pred, X, nombres, fechas, alfa)
    print(f"{'':30} {'Temp K (todas)':>15} {'Temp K (a mano)':>16} {'Expos.':>7}")
    for nombre, p in resultados.items():
        print(f"{nombre:30} {kelvin_mae(Y[:, 0], p[:, 0]):15.0f} {kelvin_mae(Y[a_mano, 0], p[a_mano, 0]):16.0f}"
              f" {np.mean(np.abs(Y[:, 1] - p[:, 1])):7.2f}")
    print("\nError de temperatura por fecha (K), base vs modelo:")
    for g in uniq:
        te = fechas == g
        print(f"  {g[:34]:34} n={te.sum():4}  base {kelvin_mae(Y[te, 0], base[te, 0]):5.0f}"
              f"  modelo {kelvin_mae(Y[te, 0], pred[te, 0]):5.0f}  real {np.median(1e6 / Y[te, 0]):5.0f} K")
    json.dump({"fechas": fechas.tolist(), "real": Y.tolist(), "pred": pred.tolist(), "ids": [f["id"] for f in filas]},
              open(DATOS / "prueba.json", "w"), ensure_ascii=False)


def entrenar():
    X, Y, pesos, fechas, nombres, _ = conjunto()
    modelos = {k: nuevo_modelo().fit(X, Y[:, j], sample_weight=pesos) for j, k in enumerate(OBJETIVOS)}
    with open(DATOS / "modelo.pkl", "wb") as fh:
        pickle.dump({"nombres": nombres, "modelos": modelos, "fotos": len(Y), "fechas": len(set(fechas))}, fh)
    print(f"modelo entrenado con {len(Y)} fotos de {len(set(fechas))} fechas -> {DATOS / 'modelo.pkl'}")


def predecir(rutas):
    """Para una tanda de RAW nuevos (un partido): {ruta: {Temperature, Tint, Exposure2012}}.
    Saca los rasgos aca mismo con el mismo codigo que el entrenamiento (color_pi.lineal)."""
    import io
    import rawpy
    from PIL import Image
    from color_pi import exif_de, lineal
    with open(DATOS / "modelo.pkl", "rb") as fh:
        m = pickle.load(fh)
    det = detector()
    X = []
    for ruta in rutas:
        with rawpy.imread(str(ruta)) as raw:
            lin, recorte = lineal(raw)
            eb = [float(x) for x in raw.camera_whitebalance]
            th = raw.extract_thumb()
        vista = Image.open(io.BytesIO(th.data)).convert("RGB")
        vista.thumbnail((512, 512))
        f = {"exif": exif_de(str(ruta)), "eb_camara": eb, "recorte": recorte}
        r = rasgos_foto(f, lin.astype(np.float32), np.asarray(vista), det)
        X.append([r[k] for k in m["nombres"]])
    X = np.array(X, dtype=np.float64)
    pred = np.column_stack([m["modelos"][k].predict(X) for k in OBJETIVOS])
    pred = despues(pred, X, m["nombres"], np.zeros(len(X), dtype=int), ALFA)   # la tanda es un partido
    return {str(r): {"Temperature": int(round(1e6 / p[0] / 50) * 50), "Tint": MATIZ,
                     "Exposure2012": round(float(p[1]), 2)} for r, p in zip(rutas, pred)}


if __name__ == "__main__":
    {"rasgos": rasgos, "probar": probar, "entrenar": entrenar}[sys.argv[1]]()
