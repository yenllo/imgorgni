"""Separa las fotos de partidos simultaneos por el color de las poleras.

Para un trabajo de 3 horas donde a cada hora se juegan dos partidos a la vez y
se dispara alternando entre las dos canchas sin ningun orden.

    python separar_partidos.py "TrabajoFotografico/LigaBabbo/03-09-2026/Jpeg"

Corre con el python del SISTEMA, que tiene ultralytics y sklearn. El venv de
Autocull tiene ultralytics pero NO sklearn: ahi revienta recien al agrupar,
despues de gastar los minutos de extraccion.

Si dos equipos de la noche visten parecido (dos blancos distintos, que pasa
seguido), subi --equipos hasta que se separen. En la F2 con 8 se fundian y
con 12 salieron los 5 partidos.

Como funciona, y por que asi (los tres hallazgos que costaron medir):

  1. La polera se lee del TORSO, no de la foto entera. YOLO-pose da hombros y
     caderas; ese cuadrilatero es tela pura. Un histograma de color de la foto
     completa solo ve pasto y luz de sodio.
  2. Los jugadores del FONDO son del otro partido. Se descartan por tamano
     relativo al torso mas grande de la propia foto: un umbral absoluto depende
     del lente y de la distancia, el relativo no.
  3. Un partido es un PAR de colores que co-ocurren dentro de la misma foto:
     dos equipos que salen juntos en un cuadro estan jugando entre si. Eso arma
     los partidos sin que nadie etiquete nada.

De noche los oscuros (negro, verde botella, azul marino) son el mismo color y
separarlos inventa partidos que no existen: se fusionan. El horario solo entra
como prior suave, para desempatar cuando un equipo juega dos veces en la noche.

Salida: partidos.csv (archivo, hora, partido, colores, confianza). No mueve, no
copia y no borra nada.
"""
import argparse
import colorsys
import csv
import glob
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

PESOS = r"C:\Renzo\Proyectos\Autocull\yolov8n-pose.pt"
TORSO = [5, 6, 11, 12]          # hombros y caderas, en el orden de COCO


def extraer(carpeta, cache):
    """Color de polera de cada jugador de cada foto. ~140 ms/foto en CPU."""
    if os.path.exists(cache):
        print(f"reuso {cache}")
        return json.load(open(cache))
    from ultralytics import YOLO
    fs = sorted(glob.glob(os.path.join(carpeta, "**", "*.JPG"), recursive=True))
    if not fs:
        sys.exit(f"no hay JPG en {carpeta}")
    m = YOLO(PESOS)
    res, t0 = [], time.time()
    for n, p in enumerate(fs, 1):
        im = Image.open(p)
        ts = im.getexif().get(306) or ""
        im.draft("RGB", (1280, 1280))            # decodifica a 1/4: mucho mas rapido
        a = np.asarray(im.convert("RGB"))
        r = m.predict(a, imgsz=640, conf=0.35, verbose=False)[0]
        cols = []
        if r.keypoints is not None and len(r.keypoints):
            for k, c in zip(r.keypoints.xy.cpu().numpy(), r.keypoints.conf.cpu().numpy()):
                if (c[TORSO] < 0.5).any():
                    continue
                pts = k[TORSO]
                x0, y0 = pts.min(0)
                x1, y1 = pts.max(0)
                w, h = x1 - x0, y1 - y0
                if w < 8 or h < 12:
                    continue
                caja = a[int(y0 + h * .1):int(y1 - h * .25),
                         int(x0 + w * .2):int(x1 - w * .2)]
                if caja.size < 60:
                    continue
                px = caja.reshape(-1, 3)
                # el mediano es el color principal; el percentil 15 es el
                # SECUNDARIO (vivos, franjas, cuello). Dos poleras blancas
                # distintas tienen el mismo mediano y distinto secundario.
                cols.append([float(v) for v in np.median(px, 0)] + [float(w * h)]
                            + [float(v) for v in np.percentile(px, 15, axis=0)])
        res.append({"f": p, "t": ts, "cols": cols})
        if n % 500 == 0:
            print(f"  {n}/{len(fs)}  {(time.time() - t0) / n * 1000:.0f} ms/foto")
    json.dump(res, open(cache, "w"))
    print(f"{len(fs)} fotos en {time.time() - t0:.0f}s")
    return res


def tono(rgb):
    """Tono y saturacion mandan; el brillo casi no, para que la luz no agrupe."""
    h, s, v = colorsys.rgb_to_hsv(*[c / 255 for c in rgb])
    return [s * np.cos(2 * np.pi * h), s * np.sin(2 * np.pi * h), v * 0.35]


def descriptor(c, peso_sec=0.7):
    """Color principal del torso + el secundario, que es lo unico que separa
    dos poleras blancas distintas. Las cachés viejas solo traen el principal."""
    d = tono(c[:3])
    return d + [peso_sec * x for x in tono(c[4:7])] if len(c) >= 7 else d + [0.0, 0.0, 0.0]


def agrupar(res, k=11, frac=0.25, min_co=0.30, min_fotos=50):
    res = [r for r in res if r["t"]]
    T = np.array([datetime.strptime(r["t"], "%Y:%m:%d %H:%M:%S").timestamp() for r in res])

    cols, duenio = [], []
    for i, r in enumerate(res):
        if not r["cols"]:
            continue
        amax = max(c[3] for c in r["cols"])
        for c in r["cols"]:
            if c[3] >= max(300, frac * amax):    # (2) fuera los del fondo
                cols.append(descriptor(c))
                duenio.append((i, c[3], c[:3]))

    km = KMeans(k, n_init=10, random_state=0).fit(np.array(cols))
    lab = km.labels_.copy()
    rgbs = [np.median([d[2] for d, m in zip(duenio, lab == j) if m], 0) for j in range(k)]
    osc = [j for j in range(k) if max(rgbs[j]) < 55]
    for j in osc[1:]:                            # los oscuros son un solo color
        lab[lab == j] = osc[0]
    if osc:
        rgbs[osc[0]] = np.median(
            [d[2] for d, m in zip(duenio, np.isin(km.labels_, osc)) if m], 0)

    V = np.zeros((len(res), k))
    for (i, area, _), j in zip(duenio, lab):
        V[i, j] += area                          # peso lineal: manda el del primer plano
    P = (V > 0).astype(float)
    CO = P.T @ P
    np.fill_diagonal(CO, 0)

    # (3) pares por co-ocurrencia; un equipo puede jugar dos partidos en la noche
    cand = sorted(((CO[a, b] / (min(P[:, a].sum(), P[:, b].sum()) + 1e-9), a, b)
                   for a in range(k) for b in range(a + 1, k)), reverse=True)
    uso, part, vent = np.zeros(k, int), [], []
    for co, a, b in cand:
        if co < min_co or uso[a] >= 2 or uso[b] >= 2:
            continue
        m = (P[:, a] > 0) & (P[:, b] > 0)
        if m.sum() < min_fotos:
            continue
        lo, hi = np.percentile(T[m], 10), np.percentile(T[m], 90)
        # Un equipo juega dos veces en la noche, pero NO a la misma hora: si
        # este par se superpone con otro que ya tiene a alguno de los dos, el
        # par es falso (sale de ver la otra cancha de fondo, no de jugar).
        if any((x in (a, b)) and min(hi, h) - max(lo, l) > 0.5 * (hi - lo)
               for (x, y, l, h) in vent for x in (x, y)):
            continue
        uso[a] += 1
        uso[b] += 1
        vent.append((a, b, lo, hi))
        part.append((a, b, float(np.median(T[m])), float(co)))
    part.sort(key=lambda p: p[2])

    sc = np.zeros((len(res), len(part)))
    for j, (a, b, tm, _) in enumerate(part):
        prox = np.exp(-((T - tm) / (45 * 60)) ** 2)   # prior horario suave, no corta
        sc[:, j] = (V[:, a] + V[:, b]) * (0.4 + 0.6 * prox)
    tot = V.sum(1)
    asig = sc.argmax(1)
    conf = sc.max(1) / (tot + 1e-9)
    dudosa = (conf < 0.6) | (tot == 0)
    asig[dudosa] = -1
    ultimo = -1                        # las dudosas heredan del vecino en el tiempo
    for i in np.argsort(T):
        if asig[i] == -1:
            asig[i] = ultimo
        else:
            ultimo = asig[i]
    return res, T, asig, conf, dudosa, part, rgbs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--equipos", type=int, default=11, help="colores distintos a buscar")
    a = ap.parse_args()
    cache = os.path.join(a.carpeta, "_poleras_cache.json")
    res, T, asig, conf, dudosa, part, rgbs = agrupar(extraer(a.carpeta, cache), a.equipos)

    def hexa(j):
        return "#%02x%02x%02x" % tuple(int(x) for x in rgbs[j])

    print(f"\n{len(res)} fotos | {int(dudosa.sum())} sin color claro "
          f"({dudosa.mean() * 100:.0f}%) resueltas por vecino temporal\n")
    for j, (x, y, tm, co) in enumerate(part):
        sel = T[asig == j]
        print(f"partido {j}: {int((asig == j).sum()):>5} fotos  {hexa(x)} vs {hexa(y)}  "
              f"{datetime.fromtimestamp(np.percentile(sel, 5)):%H:%M}-"
              f"{datetime.fromtimestamp(np.percentile(sel, 95)):%H:%M}  co={co:.2f}")

    sal = a.salida or os.path.join(
        os.path.dirname(a.carpeta.rstrip("/\\")) or ".", "partidos.csv")
    with open(sal, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "hora", "partido", "colores", "confianza", "por_vecino"])
        for i, r in enumerate(res):
            j = int(asig[i])
            w.writerow([r["f"], r["t"], j,
                        f"{hexa(part[j][0])}/{hexa(part[j][1])}" if j >= 0 else "",
                        f"{conf[i]:.2f}", int(dudosa[i])])
    print(f"\n-> {sal}")


def demo():
    """Dos partidos sinteticos entrelazados: rojo-blanco y azul-amarillo.

    Las fotos del de rojo llevan ademas dos jugadores CHICOS de la otra cancha
    al fondo: si el filtro por tamano relativo se rompe, los partidos se funden
    y esto falla.
    """
    ROJO, BLANCO, AZUL, AMA = (200, 30, 30), (230, 230, 230), (30, 40, 200), (220, 210, 20)
    res = []
    for n in range(120):
        t = f"2026:09:03 20:{n // 2:02d}:{n % 2 * 30:02d}"
        if n % 2:
            cols = [list(ROJO) + [9000], list(BLANCO) + [8000],
                    list(AZUL) + [400], list(AMA) + [380]]      # (2) fondo, chico
        else:
            cols = [list(AZUL) + [9500], list(AMA) + [8500]]
        res.append({"f": f"x{n}.JPG", "t": t, "cols": cols})
    _, _, asig, _, _, part, _ = agrupar(res, k=4, min_fotos=10)
    assert len(part) == 2, f"esperaba 2 partidos, dio {len(part)}"
    a = {asig[i] for i in range(0, 120, 2)}
    b = {asig[i] for i in range(1, 120, 2)}
    assert len(a) == 1 and len(b) == 1 and a != b, f"no separo las canchas: {a} vs {b}"
    print("demo ok: 2 partidos, 60 y 60, el fondo no contamino")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        main()
