"""
Smoke test del pipeline completo con fotos sintéticas.
Sin modelos ONNX: Fase 2 cae al embedding HSV de fallback.

    python test_smoke.py
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np


def make_photo(path: Path, color_bgr, when: datetime):
    img = np.full((480, 640, 3), color_bgr, dtype=np.uint8)
    # ruido para que los embeddings no sean idénticos
    noise = np.random.randint(0, 30, img.shape, dtype=np.uint8)
    cv2.imwrite(str(path), cv2.add(img, noise))
    ts = when.timestamp()
    os.utime(path, (ts, ts))  # read_exif_info cae a mtime si no hay EXIF


def main():
    tmp = Path(tempfile.mkdtemp(prefix="imgorgni_smoke_"))
    fotos = tmp / "fotos"
    fotos.mkdir()

    # 3 "eventos": colores y fechas bien separadas, 6 fotos c/u (> min_samples=4)
    groups = [
        ((40, 40, 200), datetime(2023, 5, 10, 12, 0)),   # rojizo
        ((200, 40, 40), datetime(2023, 8, 20, 15, 0)),   # azulado
        ((40, 200, 40), datetime(2024, 1, 5, 10, 0)),    # verdoso
    ]
    n = 0
    for color, base in groups:
        for i in range(6):
            # Nombre de cámara dedicada: sin EXIF, clasificar_dispositivo() cae al
            # nombre, y todo lo que no sea 'camara' va a _Telefono sin organizar.
            make_photo(fotos / f"DSC_{n:04d}.jpg", color, base + timedelta(minutes=30 * i))
            n += 1

    cfg = {
        "root_dir": str(fotos),
        "db_path": str(tmp / "photos.db"),
        "watermark_path": None,
        "watermark_threshold": 0.85,
        "enable_mobile_filter": False,
        "enable_yolo": False,
        "enable_features": False,
        "enable_scenes": False,   # requiere scene_embeddings.npz
        "enable_faces": False,    # requiere insightface
        "enable_quality": True,
        "enable_phash": True,
        "enable_gps": True,
        "cluster_time_window_hours": 4,
        "cluster_cosine_threshold": 0.75,
        "dbscan_min_samples": 4,
        "auto_merge_similar_events": False,
        "max_workers_phase1": 4,
        "sqlite_cache_mb": 64,
        "image_batch_size": 8,
        "parallel_loading_workers": 4,
        "commit_batch_size": 10,
        "cluster_n_jobs": 1,
    }
    config_path = tmp / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")

    try:
        print("=== Fase 1: indexado ===")
        importlib.import_module("1_indexado").run(config_path=str(config_path))

        import db
        conn = db.get_conn(cfg["db_path"])
        files_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert files_count == 18, f"Esperaba 18 files, hay {files_count}"
        dates = conn.execute("SELECT COUNT(*) FROM files WHERE capture_date IS NOT NULL").fetchone()[0]
        assert dates == 18, f"Faltan capture_dates: {dates}/18"
        conn.close()
        print(f"OK: {files_count} fotos indexadas con fecha")

        print("=== Fase 2: análisis (fallback HSV) ===")
        importlib.import_module("2_analisis").run(config_path=str(config_path))

        conn = db.get_conn(cfg["db_path"])
        feats = conn.execute(
            "SELECT COUNT(*) FROM photo_features WHERE visual_embedding != '[]'").fetchone()[0]
        assert feats == 18, f"Esperaba 18 embeddings, hay {feats}"
        pending = conn.execute("SELECT COUNT(*) FROM files WHERE needs_analysis=1").fetchone()[0]
        assert pending == 0, f"Quedaron {pending} pendientes"
        # análisis extendido: calidad y phash
        nq = conn.execute("SELECT COUNT(*) FROM photo_features WHERE quality_json LIKE '%sharpness%'").fetchone()[0]
        assert nq == 18, f"Esperaba 18 con métricas de calidad, hay {nq}"
        hashes = [r[0] for r in conn.execute("SELECT phash FROM photo_features WHERE phash IS NOT NULL")]
        assert len(hashes) == 18, f"Esperaba 18 phash, hay {len(hashes)}"
        assert len(set(hashes)) > 1, "Todos los phash idénticos: el hash no discrimina"
        conn.close()
        print(f"OK: {feats} embeddings + calidad + {len(set(hashes))} phash distintos")

        print("=== Fase 3: agrupación ===")
        importlib.import_module("3_agrupacion_mejorado").run(config_path=str(config_path))

        conn = db.get_conn(cfg["db_path"])
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        grouped = conn.execute("SELECT COUNT(DISTINCT photo_id) FROM event_photos").fetchone()[0]
        assert events >= 2, f"Esperaba >=2 eventos, hay {events}"
        assert grouped >= 12, f"Esperaba >=12 fotos agrupadas, hay {grouped}"
        conn.close()
        print(f"OK: {events} eventos, {grouped} fotos agrupadas")

        print("=== Fase 4: export ===")
        out_dir = tmp / "eventos"
        importlib.import_module("4_exportar").run(
            config_path=str(config_path), out_dir=str(out_dir), mode="link")

        # Jerarquía de 2 niveles: {Categoría}/{Evento}/fotos
        categorias = [d for d in out_dir.iterdir() if d.is_dir()]
        assert categorias, "No se creó ninguna carpeta de categoría"
        assert all(c.name.startswith("Fotos ") for c in categorias), \
            f"Categorías con nombre inesperado: {[c.name for c in categorias]}"
        eventos_dirs = [e for c in categorias for e in c.iterdir() if e.is_dir()]
        assert len(eventos_dirs) == events, f"Esperaba {events} carpetas de evento, hay {len(eventos_dirs)}"
        exported = sum(len(list(e.glob("*.jpg"))) for e in eventos_dirs)
        assert exported == grouped, f"Esperaba {grouped} fotos exportadas, hay {exported}"
        print(f"OK: {len(categorias)} categorías / {len(eventos_dirs)} eventos / {exported} fotos")
        for c in categorias:
            print(f"     {c.name}/ -> {[e.name for e in c.iterdir() if e.is_dir()]}")

        print("\n✅ SMOKE TEST COMPLETO: los 4 módulos reconstruidos funcionan de punta a punta")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
