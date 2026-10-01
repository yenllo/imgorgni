r"""Guarda cuanta confianza tuvo el modelo en cada etiqueta que puso.

`scene_tags` guarda que etiquetas quedaron, pero no con cuanta fuerza: una foto
que apenas paso el umbral y otra que lo paso por lejos se ven iguales en la
base. Eso obliga a revisar 88.922 fotos de `sports game` en orden de fecha, que
es el peor orden posible — las primeras 200 son todas obvias y no enseñan nada.

Con el z-score guardado, el repaso puede empezar por las que estan JUSTO en el
borde, que son las unicas donde tu respuesta agrega informacion: ahi es donde el
modelo duda y donde se decide el umbral.

Se recalcula todo desde los embeddings ya guardados: ~1 minuto, sin GPU y sin
abrir una sola foto.

    python calcular_confianza.py
"""
import json
import sqlite3
import time

import numpy as np

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
EMB = r"C:\Renzo\Proyectos\IMGorgni\scene_embeddings.npz"
LOTE = 4000


def main():
    d = np.load(EMB, allow_pickle=True)
    M = d["embeddings"].astype(np.float32)
    labels = [str(x) for x in d["labels"]]
    print("vocabulario: %d etiquetas" % len(labels))

    c = sqlite3.connect(DB, timeout=60)
    c.execute("""CREATE TABLE IF NOT EXISTS etiqueta_score (
                     photo_id INTEGER NOT NULL,
                     etiqueta TEXT NOT NULL,
                     z REAL NOT NULL,
                     PRIMARY KEY (photo_id, etiqueta))""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_score_etq ON etiqueta_score(etiqueta, z)")
    c.execute("DELETE FROM etiqueta_score")
    c.commit()

    total = c.execute("""SELECT COUNT(*) FROM photo_features
                          WHERE visual_embedding NOT IN ('','[]')""").fetchone()[0]
    print("fotos con embedding: %s" % "{:,}".format(total))

    t0 = time.time()
    hechas = guardadas = 0
    offset = 0
    while True:
        filas = c.execute("""SELECT photo_id, visual_embedding, scene_tags
                               FROM photo_features
                              WHERE visual_embedding NOT IN ('','[]')
                              ORDER BY photo_id LIMIT ? OFFSET ?""",
                          (LOTE, offset)).fetchall()
        if not filas:
            break
        offset += len(filas)

        vecs, ids, tags = [], [], []
        for pid, emb, st in filas:
            try:
                v = np.asarray(json.loads(emb), dtype=np.float32)
                if v.shape[0] != M.shape[1]:
                    continue
                n = np.linalg.norm(v)
                vecs.append(v / n if n else v)
                ids.append(pid)
                tags.append(set(json.loads(st or "[]")))
            except Exception:
                continue
        if not vecs:
            continue
        V = np.stack(vecs)
        sim = V @ M.T
        z = (sim - sim.mean(axis=1, keepdims=True)) / (sim.std(axis=1, keepdims=True) + 1e-8)

        # Solo se guarda el z de las etiquetas que la foto REALMENTE tiene: son
        # las que van a aparecer en un repaso. Guardar las 98 por foto serian
        # 17 millones de filas para no usar 96 de cada 100.
        lote = []
        for i, pid in enumerate(ids):
            for t in tags[i]:
                j = labels.index(t) if t in labels else -1
                if j >= 0:
                    lote.append((pid, t, float(z[i, j])))
        c.executemany("INSERT OR REPLACE INTO etiqueta_score VALUES (?,?,?)", lote)
        guardadas += len(lote)
        hechas += len(ids)
        if hechas % 20000 < LOTE:
            c.commit()
            print("   %s/%s fotos  %s scores  %.0f s"
                  % ("{:,}".format(hechas), "{:,}".format(total),
                     "{:,}".format(guardadas), time.time() - t0))
    c.commit()

    print()
    print("listo: %s fotos, %s scores en %.0f s"
          % ("{:,}".format(hechas), "{:,}".format(guardadas), time.time() - t0))
    print("\n=== las etiquetas mas grandes, y donde esta su borde ===")
    for r in c.execute("""SELECT etiqueta, COUNT(*) n, MIN(z) mn, AVG(z) prom, MAX(z) mx
                            FROM etiqueta_score GROUP BY etiqueta
                           ORDER BY n DESC LIMIT 10"""):
        print("   %-24s %7s  z de %.2f a %.2f (prom %.2f)"
              % (r[0][:24], "{:,}".format(r[1]), r[2], r[4], r[3]))


if __name__ == "__main__":
    main()
