"""
Fase 3b: Agrupa las caras detectadas en personas.

Corre después de la Fase 2 (que detecta las caras) y es independiente de la
Fase 3. Podés re-correrla cambiando el umbral sin re-analizar nada.

    python 3b_personas.py
    python 3b_personas.py --eps 0.5      # más estricto (menos fusiones erróneas)
    python 3b_personas.py --listar       # ver las personas ya detectadas
"""
import argparse
import json
import os
from collections import Counter

import numpy as np

from db import get_conn, init_db, fetch_all_faces, set_face_persons
from utils_analisis import cluster_faces
from utils_images import recortar_cara
from utils_log import Spinner, log_step

UMBRAL_MISMA_PERSONA = 0.5  # similitud coseno de centroide para reconocer una persona ya nombrada


def listar(conn):
    filas = conn.execute("""
        SELECT fa.person_id, COALESCE(p.name, ''), COUNT(*) AS n,
               COUNT(DISTINCT fa.photo_id) AS fotos
        FROM faces fa LEFT JOIN persons p ON fa.person_id = p.id
        WHERE fa.person_id IS NOT NULL AND fa.person_id >= 0
        GROUP BY fa.person_id ORDER BY n DESC
    """).fetchall()
    if not filas:
        log_step("No hay personas agrupadas todavía.")
        return
    log_step(f"{len(filas)} personas detectadas:")
    for pid, nombre, n, fotos in filas:
        etiqueta = nombre or f"(sin nombre)"
        log_step(f"   Persona {pid:<4} {etiqueta:<25} {n:>5} caras en {fotos:>5} fotos")
    log_step("Para nombrar una persona:")
    log_step("   python 3b_personas.py --nombrar 3 'Marta'")


def _centroide(vectores):
    c = np.mean(vectores, axis=0)
    return c / max(np.linalg.norm(c), 1e-8)


def _elegir_representantes(conn, person_ids, max_candidatos=12):
    """Por persona, elige como miniatura la cara mas nitida (varianza de
    Laplaciano) entre las de mejor det_score, para que se entienda quien es
    de un vistazo en vez de una foto borrosa al azar."""
    import cv2
    for pid in person_ids:
        candidatos = conn.execute("""
            SELECT f.id, f.bbox_json, img.file_path
            FROM faces f JOIN files img ON f.photo_id = img.id
            WHERE f.person_id=? ORDER BY f.det_score DESC LIMIT ?
        """, (pid, max_candidatos)).fetchall()
        mejor_id, mejor_score = None, -1.0
        for fid, bbox_json, fp in candidatos:
            if not fp or not os.path.exists(fp):
                continue
            im = recortar_cara(fp, json.loads(bbox_json), margen=0.0)
            if im is None:
                continue
            gray = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2GRAY)
            score = cv2.Laplacian(gray, cv2.CV_64F).var()
            if score > mejor_score:
                mejor_score, mejor_id = score, fid
        if mejor_id is None and candidatos:
            mejor_id = candidatos[0][0]
        if mejor_id is not None:
            conn.execute("UPDATE persons SET rep_face_id=? WHERE id=?", (mejor_id, pid))


def run(eps: float = 0.35, min_samples: int = 3):
    from web_curador import DB
    conn = get_conn(DB)
    init_db(conn)

    spinner = Spinner("Cargando caras desde la BD...")
    spinner.start()
    faces = fetch_all_faces(conn)
    spinner.stop(f"{len(faces):,} caras cargadas")

    if not faces:
        log_step("⚠️ No hay caras. ¿Corriste la Fase 2 con enable_faces=true?")
        conn.close()
        return

    face_ids = [f[0] for f in faces]
    embeddings = [f[2] for f in faces]
    X = np.asarray(embeddings, dtype=np.float32)
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-8)
    idx_por_face = {fid: i for i, fid in enumerate(face_ids)}

    # DBSCAN no da ids de cluster estables entre corridas (el "3" de hoy puede
    # no tener nada que ver con el "3" de ayer). Para no mezclar nombres ya
    # puestos a mano, guardamos el centroide de cada persona nombrada ANTES
    # de reclusterizar y se lo reasignamos al cluster nuevo mas parecido.
    viejos = conn.execute("SELECT id, name FROM persons WHERE name IS NOT NULL").fetchall()
    centroides_viejos = []
    for pid, name in viejos:
        caras_pid = [r[0] for r in conn.execute("SELECT id FROM faces WHERE person_id=?", (pid,))]
        idxs = [idx_por_face[f] for f in caras_pid if f in idx_por_face]
        if idxs:
            centroides_viejos.append((name, _centroide(X[idxs])))

    spinner2 = Spinner(f"Agrupando en personas (eps={eps})...")
    spinner2.start()
    labels = cluster_faces(embeddings, eps=eps, min_samples=min_samples)
    spinner2.stop("Clustering completado")

    labels_arr = np.asarray(labels)
    conteo = Counter(l for l in labels if l >= 0)
    sin_agrupar = sum(1 for l in labels if l < 0)

    nombres_nuevos = {}
    usados_nombre = set()
    if centroides_viejos and conteo:
        candidatos = []
        for label in conteo:
            c = _centroide(X[labels_arr == label])
            for name, cv in centroides_viejos:
                sim = float(np.dot(c, cv))
                if sim >= UMBRAL_MISMA_PERSONA:
                    candidatos.append((sim, label, name))
        # greedy: mejor similitud primero, cada nombre y cada cluster se usan una sola vez
        # (si una persona nombrada se partio en 2 clusters, el nombre queda solo en el mas parecido)
        candidatos.sort(reverse=True)
        usados_label, usados_nombre = set(), set()
        for sim, label, name in candidatos:
            if label in usados_label or name in usados_nombre:
                continue
            nombres_nuevos[label] = name
            usados_label.add(label)
            usados_nombre.add(name)

    conn.execute("DELETE FROM persons")
    for person_id, n_caras in conteo.items():
        conn.execute("INSERT INTO persons (id, name, face_count) VALUES (?, ?, ?)",
                     (person_id, nombres_nuevos.get(person_id), n_caras))

    # -1 (ruido de DBSCAN) se guarda como NULL, no como -1: ese valor no es un
    # id de persona real y antes quedaba como FK huerfana en faces.person_id.
    asignaciones = [(fid, (l if l >= 0 else None)) for fid, l in zip(face_ids, labels)]
    set_face_persons(conn, asignaciones)

    spinner3 = Spinner(f"Eligiendo cara representativa de {len(conteo):,} personas...")
    spinner3.start()
    _elegir_representantes(conn, list(conteo.keys()))
    conn.commit()
    spinner3.stop("Representantes listos")

    perdidos = len(centroides_viejos) - len(usados_nombre) if centroides_viejos else 0

    log_step("=" * 60)
    log_step(f"✅ {len(conteo)} personas detectadas en {len(faces):,} caras")
    log_step(f"   Sin agrupar (apariciones únicas): {sin_agrupar:,}")
    if centroides_viejos:
        log_step(f"   Nombres recuperados: {len(usados_nombre)}/{len(centroides_viejos)}"
                  + (f" ({perdidos} no encontraron cluster parecido, quedaron sin nombre)" if perdidos else ""))
    for person_id, n in conteo.most_common(10):
        etiqueta = nombres_nuevos.get(person_id) or f"Persona {person_id}"
        log_step(f"   {etiqueta}: {n:,} caras")
    if len(conteo) > 10:
        log_step(f"   ... y {len(conteo) - 10} más")
    log_step("=" * 60)
    log_step("Ponéles nombre con: python 3b_personas.py --nombrar <id> '<nombre>'")

    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Agrupar caras en personas")
    ap.add_argument("--eps", type=float, default=0.35,
                    help="Distancia coseno máxima; menor = más estricto")
    ap.add_argument("--min-samples", type=int, default=3)
    ap.add_argument("--listar", action="store_true", help="Listar personas ya agrupadas")
    ap.add_argument("--nombrar", nargs=2, metavar=("ID", "NOMBRE"),
                    help="Asignar nombre a una persona")
    args = ap.parse_args()

    if args.listar or args.nombrar:
        from web_curador import DB
        conn = get_conn(DB)
        if args.nombrar:
            pid, nombre = int(args.nombrar[0]), args.nombrar[1]
            conn.execute("INSERT INTO persons (id, name) VALUES (?, ?) "
                         "ON CONFLICT(id) DO UPDATE SET name=excluded.name", (pid, nombre))
            conn.commit()
            log_step(f"✅ Persona {pid} = '{nombre}'")
        else:
            listar(conn)
        conn.close()
    else:
        run(eps=args.eps, min_samples=args.min_samples)
