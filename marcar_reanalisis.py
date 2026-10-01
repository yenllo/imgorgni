r"""
Marca para reanálisis las fotos que la clasificación CORREGIDA considera de
cámara pero que nunca se analizaron (las saltó `analizar_solo_camara` cuando
caían en 'desconocido' por haber perdido el EXIF).

    python marcar_reanalisis.py --db C:/Renzo/Proyectos/IMGorgni/photos_v2.db
"""
import argparse
import sqlite3
import sys

sys.path.insert(0, ".")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_analisis import clasificar_dispositivo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db")
    args = ap.parse_args()

    c = sqlite3.connect(args.db, timeout=90)
    ya = {r[0] for r in c.execute("SELECT photo_id FROM photo_features")}
    ids = [(i,) for i, fp, cm in c.execute("SELECT id, file_path, camera_model FROM files")
           if i not in ya and clasificar_dispositivo(cm, fp) == "camara"]
    print(f"  de cámara y sin analizar: {len(ids):,}")
    c.executemany("UPDATE files SET needs_analysis=1 WHERE id=?", ids)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM files WHERE needs_analysis=1").fetchone()[0]
    print(f"  pendientes de Fase 2 ahora: {n:,}")
    c.close()


if __name__ == "__main__":
    main()
