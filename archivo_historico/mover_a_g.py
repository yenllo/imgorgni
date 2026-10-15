"""Mueve carpetas de fecha de C:\\Fotos_F_rescatadas\\2024 a G:\\Fotos_F_rescatadas\\2024
y actualiza las rutas correspondientes en la base de datos."""
import shutil
import sqlite3
from pathlib import Path

CARPETAS = ["2024-07-01", "2024-07-02", "2024-07-03", "2024-12-18"]
ORIGEN_BASE = Path(r"C:\Fotos_F_rescatadas\2024")
DESTINO_BASE = Path(r"G:\Fotos_F_rescatadas\2024")


def main():
    DESTINO_BASE.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect("C:/Renzo/Proyectos/IMGorgni/photos_v2.db", timeout=10)
    total = 0
    for nombre in CARPETAS:
        origen = ORIGEN_BASE / nombre
        destino = DESTINO_BASE / nombre
        n_antes = sum(1 for _ in origen.rglob("*") if _.is_file())
        print(f"{nombre}: {n_antes} archivos, moviendo...", flush=True)
        shutil.move(str(origen), str(destino))
        n_despues = sum(1 for _ in destino.rglob("*") if _.is_file())
        if n_despues != n_antes:
            print(f"  MISMATCH: antes {n_antes}, despues {n_despues}")
            continue

        origen_str = str(origen)
        destino_str = str(destino)
        filas = c.execute(
            "SELECT id, file_path FROM files WHERE file_path LIKE ?",
            (origen_str + "%",),
        ).fetchall()
        actualizados = 0
        for fid, fp in filas:
            nueva = destino_str + fp[len(origen_str):]
            c.execute("UPDATE files SET file_path=? WHERE id=?", (nueva, fid))
            actualizados += 1
        c.commit()
        print(f"  ok: {n_despues} archivos movidos, {actualizados} rutas actualizadas en BD", flush=True)
        total += n_despues

    print(f"TOTAL: {total} archivos movidos")


if __name__ == "__main__":
    main()
