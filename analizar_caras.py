"""Deteccion facial sobre TODA la biblioteca, independiente de la Fase 2 (que
ya corrio con enable_faces=false). Pausable/retomable: cada foto procesada
(tenga o no caras) se marca en face_scan_done, asi que frenar el proceso
(Ctrl+C o matar el background) y volver a correr el mismo comando retoma
justo donde quedo, sin reprocesar nada.

    python analizar_caras.py                 # corre hasta terminar la bd
    python analizar_caras.py --limite 5000    # corre un lote y para
"""
import argparse
import sqlite3

from web_curador import DB
from utils_analisis import FaceAnalyzer
from utils_images import load_image_array


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0, help="0 = sin limite")
    ap.add_argument("--commit-cada", type=int, default=200)
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=180)
    c.execute("CREATE TABLE IF NOT EXISTS face_scan_done (photo_id INTEGER PRIMARY KEY)")
    c.commit()

    pend = c.execute("""
        SELECT f.id, f.file_path FROM files f
        WHERE COALESCE(f.borrado,0)=0
          AND f.id NOT IN (SELECT photo_id FROM face_scan_done)
        ORDER BY f.id
    """).fetchall()
    if args.limite:
        pend = pend[:args.limite]
    print(f"pendientes: {len(pend):,}")
    if not pend:
        return

    analyzer = FaceAnalyzer(use_gpu=True)
    print("modelo cargado, arrancando...")

    con_caras = procesadas = 0
    lote = []
    for pid, fp in pend:
        try:
            img = load_image_array(fp)
            caras = analyzer.analyze(img)
            if caras:
                c.executemany(
                    "INSERT INTO faces (photo_id, embedding_json, bbox_json, det_score) VALUES (?,?,?,?)",
                    [(pid, __import__("json").dumps(f["embedding"]), __import__("json").dumps(f["bbox"]),
                      f.get("det_score")) for f in caras])
                con_caras += 1
        except Exception:
            pass
        lote.append(pid)
        procesadas += 1
        if len(lote) >= args.commit_cada:
            c.executemany("INSERT OR IGNORE INTO face_scan_done (photo_id) VALUES (?)",
                          [(i,) for i in lote])
            c.commit()
            lote = []
            print(f"  {procesadas:,}/{len(pend):,}  ({con_caras:,} con caras)", flush=True)
    if lote:
        c.executemany("INSERT OR IGNORE INTO face_scan_done (photo_id) VALUES (?)", [(i,) for i in lote])
        c.commit()

    print(f"listo: {procesadas:,} procesadas, {con_caras:,} con al menos una cara")
    c.close()


if __name__ == "__main__":
    main()
