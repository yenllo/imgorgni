"""
Recalcula las escenas de todas las fotos reusando los embeddings ya guardados.

Los embeddings visuales de CLIP no cambian nunca; lo único que cambia al tocar
el vocabulario es la comparación contra las etiquetas. Esto evita re-correr la
Fase 2 completa (horas de GPU sobre 200k fotos) por un ajuste de vocabulario.

Flujo típico al afinar:
    python precompute_scene_embeddings.py    # regenera las etiquetas
    python recalcular_escenas.py             # reaplica sobre lo ya analizado
    python main.py -ph 3                     # re-agrupa con las escenas nuevas
"""
import argparse
import json
import sys

from db import get_conn
from utils_analisis import SceneClassifier
from utils_log import Spinner, log_step

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def run(config_path: str = "config.json", embeddings_path: str = None,
        top_k: int = 3, min_score: float = 0.20, batch: int = 2000):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    ruta = embeddings_path or cfg.get("scene_embeddings_path", "scene_embeddings.npz")
    clf = SceneClassifier(ruta)
    log_step(f"Vocabulario: {len(clf.labels)} escenas ({clf.dim} dims)")

    conn = get_conn(cfg["db_path"])
    total = conn.execute(
        "SELECT COUNT(*) FROM photo_features WHERE visual_embedding NOT IN ('','[]')"
    ).fetchone()[0]
    log_step(f"Fotos con embedding: {total:,}")
    if total == 0:
        log_step("⚠️ No hay embeddings. Corré la Fase 2 primero.")
        conn.close()
        return

    spinner = Spinner("Recalculando escenas...")
    spinner.start()

    actualizadas = 0
    offset = 0
    while True:
        filas = conn.execute("""
            SELECT photo_id, visual_embedding FROM photo_features
            WHERE visual_embedding NOT IN ('','[]')
            ORDER BY photo_id LIMIT ? OFFSET ?
        """, (batch, offset)).fetchall()
        if not filas:
            break

        ids, embs = [], []
        for pid, raw in filas:
            try:
                embs.append(json.loads(raw or "[]"))
            except json.JSONDecodeError:
                embs.append([])
            ids.append(pid)

        escenas = clf.classify_batch(embs, top_k=top_k, min_score=min_score)
        conn.executemany("UPDATE photo_features SET scene_tags=? WHERE photo_id=?",
                         [(json.dumps(e), pid) for pid, e in zip(ids, escenas)])
        conn.commit()
        actualizadas += len(filas)
        offset += batch
        spinner.text = f"Recalculando escenas... {actualizadas:,}/{total:,}"

    spinner.stop(f"✅ {actualizadas:,} fotos actualizadas")
    log_step("Ahora podés re-correr la Fase 3 para reagrupar: python main.py -ph 3")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Recalcular escenas sin re-correr CLIP")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--embeddings", default=None, help="Ruta a scene_embeddings.npz")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--min-score", type=float, default=0.20)
    args = ap.parse_args()
    run(args.config, args.embeddings, args.top_k, args.min_score)
