r"""
Arma el plan de descarte de los exports web sin EXIF, para revisar y aprobar.

`clasificar_dispositivo` deja en 'desconocido' 8.758 fotos vivas. No son de
telefono: son exports web de material propio de camara (carpetas WEB/, web/,
'sin marca', previews de Lightroom). Muchas son una copia reducida de una foto
que ya esta en la base en su version original.

Este script empareja cada una con la foto de CAMARA mas parecida por phash
(distancia de Hamming sobre 64 bits) y deja un CSV ordenado de mas seguro a
mas dudoso. NO BORRA NI MUEVE NADA: es material para que Renzo apruebe por
tandas.

Como leer la distancia:
    0      la misma imagen, bit por bit del hash
    1-4    la misma foto reescalada; descartar la copia es seguro
    5-8    casi seguro la misma, mirar alguna al azar antes de aprobar
    9-12   dudoso, conviene ojo humano
    13+    no tienen original: NO son copias, son fotos unicas

    python plan_dedup_web.py
"""
import csv
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_analisis import clasificar_dispositivo

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
SALIDA = "plan_dedup_web.csv"

POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=180)
    ph = {p: int(h, 16) for p, h in c.execute(
        "SELECT photo_id, phash FROM photo_features WHERE phash IS NOT NULL")}

    web, cam = [], []
    meta = {}
    for pid, fp, cm, b, w, h in c.execute(
            """SELECT id, file_path, camera_model, bytes, ancho, alto FROM files
               WHERE COALESCE(borrado,0)=0"""):
        if pid not in ph:
            continue
        d = clasificar_dispositivo(cm, fp)
        meta[pid] = (fp, b or 0, w or 0, h or 0)
        if d == "desconocido":
            web.append(pid)
        elif d == "camara":
            cam.append(pid)

    print(f"exports sin EXIF: {len(web):,}   originales de camara: {len(cam):,}")
    a = np.array([ph[p] for p in web], dtype=np.uint64)
    b = np.array([ph[p] for p in cam], dtype=np.uint64)

    mejor = np.full(len(a), 64, dtype=np.uint8)
    quien = np.zeros(len(a), dtype=np.int64)
    for i in range(0, len(b), 4000):
        x = np.bitwise_xor(a[:, None], b[None, i:i + 4000])
        d = POP[x.view(np.uint8).reshape(x.shape[0], x.shape[1], 8)].sum(axis=2).astype(np.uint8)
        j = d.argmin(axis=1)
        v = d[np.arange(len(a)), j]
        cambia = v < mejor
        quien[cambia] = np.array(cam[i:i + 4000])[j][cambia]
        mejor[cambia] = v[cambia]
        print(f"  {min(i + 4000, len(b)):,}/{len(b):,}", end="\r")

    filas = []
    for k, pid in enumerate(web):
        dist = int(mejor[k])
        orig = int(quien[k])
        fp, by, w, h = meta[pid]
        ofp, oby, ow, oh = meta.get(orig, ("", 0, 0, 0))
        tramo = ("identica" if dist == 0 else "muy segura" if dist <= 4 else
                 "probable" if dist <= 8 else "dudosa" if dist <= 12 else "SIN ORIGINAL")
        filas.append({
            "distancia": dist, "veredicto": tramo,
            "id_copia": pid, "copia": fp, "mb_copia": round(by / 1024 ** 2, 2),
            "px_copia": w * h,
            "id_original": orig if dist <= 12 else "", "original": ofp if dist <= 12 else "",
            "mb_original": round(oby / 1024 ** 2, 2) if dist <= 12 else "",
            "px_original": ow * oh if dist <= 12 else "",
            "original_es_mayor": (ow * oh > w * h) if dist <= 12 else "",
        })
    filas.sort(key=lambda r: (r["distancia"], -r["mb_copia"]))
    with open(SALIDA, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(filas[0]))
        wr.writeheader()
        wr.writerows(filas)

    print(f"\n\nplan en {SALIDA}\n")
    from collections import Counter
    cnt = Counter(r["veredicto"] for r in filas)
    for k in ("identica", "muy segura", "probable", "dudosa", "SIN ORIGINAL"):
        gr = [r for r in filas if r["veredicto"] == k]
        if not gr:
            continue
        mb = sum(r["mb_copia"] for r in gr)
        mayor = sum(1 for r in gr if r["original_es_mayor"] is True)
        print(f"  {cnt[k]:5,}  {k:13}  {mb/1024:6.2f} GB"
              + (f"   el original es mas grande en {mayor}/{len(gr)}" if k != "SIN ORIGINAL" else ""))


if __name__ == "__main__":
    main()
