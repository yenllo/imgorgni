#!/usr/bin/env python3
"""Analisis facial de una galeria a la venta, EN LA NOTEBOOK y con las fotos locales.

Arma caras/<slug>.npz en la Pi: lo que usa caras_pi.py para buscar por selfie. Lo
corre agente_calentado.py, de noche y en casa, cuando apretan "Analizar caras" en el
panel. La Pi nunca analiza: solo busca.

    python caras_galeria.py -- <slug>             # analiza, sube y prende la busqueda
    python caras_galeria.py --probar 5 -- <slug>  # 5 fotos, no escribe nada en la Pi

Las fotos se leen de TrabajoFotografico, jamas por Y:. Si falta alguna no se sube un
indice a medias: una busqueda que se salta fotos parece que anduvo y no anduvo.
"""
import argparse
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from calentar_local import PI_GALERIA, TRABAJO, datos, host_pi, pi_python, ssh
from utils_analisis import FaceAnalyzer
from utils_images import load_image_array

AQUI = Path(__file__).resolve().parent
LOCAL = AQUI / "TrabajoFotografico"
RAIZ_NAS = "/mnt/fotos/Fotos/Galeria/"


def ruta_local(ruta, base=LOCAL):
    """/mnt/fotos/Fotos/Galeria/Trabajo/<liga>/<fecha>/x.jpg -> TrabajoFotografico/<liga>/<fecha>/x.jpg
    Lo que no es de Trabajo/ conserva su categoria (Cumpleaños/...). Fuera de la raiz: None."""
    if not ruta.startswith(RAIZ_NAS):
        return None
    rel = ruta[len(RAIZ_NAS):]
    return base / (rel[len("Trabajo/"):] if rel.startswith("Trabajo/") else rel)


def avance(slug, i, n):
    """Lo que muestra el panel. Si falla la red no se corta el analisis por un contador."""
    try:
        ssh("echo %d/%d > %s/cache/caras-trabajando-%s" % (i, n, PI_GALERIA, slug), timeout=30)
    except subprocess.SubprocessError:
        pass


def subir(slug, npz, resumen):
    """npz y resumen aparecen de una vez: la busqueda nunca lee un archivo a medio subir."""
    json_local = npz.with_suffix(".json")
    json_local.write_text(json.dumps(resumen), encoding="utf-8")
    destino = "%s/caras/%s" % (PI_GALERIA, slug)
    for local, ext in ((npz, ".npz"), (json_local, ".json")):
        subprocess.run(["scp", "-q", str(local), "%s:%s%s.tmp" % (host_pi(), destino, ext)], check=True)
    r = ssh('mv "%s.npz.tmp" "%s.npz" && mv "%s.json.tmp" "%s.json"' % ((destino,) * 4), timeout=30)
    if r.returncode:
        sys.exit("no pude dejar el indice en la Pi: " + r.stderr[-200:])
    pi_python('''
import json, sqlite3
c = sqlite3.connect("galeria.db", timeout=30)
c.execute("UPDATE galeria SET buscar_cara = 1 WHERE slug = ?", (%r,))
c.commit()
print(json.dumps(True))
''' % slug)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--probar", type=int, metavar="N", help="solo N fotos; no escribe nada en la Pi")
    a = ap.parse_args()

    d = datos(a.slug)
    fotos = [r for _i, r in d["fotos"]]
    if not d["gal"]["venta"] and not a.probar:
        sys.exit("la galeria no esta a la venta")
    locales = [(r, ruta_local(r)) for r in fotos]
    faltan = [r for r, p in locales if p is None or not p.is_file()]
    if faltan:
        sys.exit("faltan %d de %d fotos en TrabajoFotografico, por ejemplo %s"
                 % (len(faltan), len(fotos), faltan[0]))

    # que la notebook no se suspenda a mitad de la noche; Windows lo suelta al salir
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    if a.probar:
        locales = locales[:a.probar]

    fa = FaceAnalyzer(use_gpu=False)
    emb, archivo, bbox, score = [], [], [], []
    errores, t0, n = 0, time.time(), len(locales)
    for i, (ruta, p) in enumerate(locales, 1):
        try:
            caras = fa.analyze(load_image_array(str(p)))
        except Exception as e:
            errores += 1
            print("  error %s: %s" % (p.name, e), flush=True)
            continue
        for c in caras:
            emb.append(c["embedding"])
            archivo.append(ruta)        # la ruta entera: los DSC se repiten entre carpetas
            bbox.append(c["bbox"])
            score.append(c["det_score"])
        if i % 25 == 0 or i == n:
            print("  %d/%d  caras=%d  %.0f s" % (i, n, len(emb), time.time() - t0), flush=True)
            if not a.probar:
                avance(a.slug, i, n)

    print("%d fotos, %d caras, %d errores, %.1f s por foto" % (n, len(emb), errores, (time.time() - t0) / n))
    if a.probar:
        return
    if errores or not emb:
        sys.exit("%d fotos no se pudieron leer y hay %d caras: no subo el indice" % (errores, len(emb)))

    npz = TRABAJO / ("caras-%s.npz" % a.slug)
    TRABAJO.mkdir(exist_ok=True)
    np.savez(npz, emb=np.asarray(emb, np.float32).reshape(-1, 512), archivo=np.asarray(archivo),
             bbox=np.asarray(bbox, np.int32).reshape(-1, 4), score=np.asarray(score, np.float32))
    subir(a.slug, npz, {"fotos": n, "caras": len(emb), "cuando": time.strftime("%Y-%m-%d %H:%M")})
    npz.unlink()
    npz.with_suffix(".json").unlink()
    print("listo: indice subido")


if __name__ == "__main__":
    base = Path("B")
    assert ruta_local(RAIZ_NAS + "Trabajo/Liga/F1/jpeg/a.JPG", base) == base / "Liga/F1/jpeg/a.JPG"
    assert ruta_local(RAIZ_NAS + "Cumpleaños/X/a.JPG", base) == base / "Cumpleaños/X/a.JPG"
    assert ruta_local("/home/yenllo/galeria/derivadas/a.jpg", base) is None
    main()
