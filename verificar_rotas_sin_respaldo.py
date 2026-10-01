r"""
Abre de verdad las fotos marcadas como rotas que NO tienen otra copia sana.

El barrido de integridad marco 1.007 fotos como danadas, pero 883 tienen un
respaldo: 163 con el mismo sha256 y 720 con un archivo sano del mismo nombre
(el caso tipico es el RAW ilegible con su JPEG bueno al lado). Las que importan
son las 124 restantes: si esas no abren, la imagen se perdio y no hay vuelta.

Este script intenta abrirlas de verdad —decodificar los pixeles, no leer la
miniatura del EXIF, que en un RAW roto engana— y deja el veredicto en un CSV.

    python verificar_rotas_sin_respaldo.py
"""
import csv
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
SALIDA = "rotas_sin_respaldo.csv"
RAW = {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}


def abre(ruta):
    """(bool, motivo). Fuerza la decodificacion: un .load() de verdad."""
    p = Path(ruta)
    if not p.exists():
        return False, "no esta en disco"
    try:
        if p.suffix.lower() in RAW:
            import rawpy
            with rawpy.imread(str(p)) as r:
                r.postprocess(half_size=True, no_auto_bright=True)
        else:
            from PIL import Image
            with Image.open(p) as im:
                im.load()
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:180]


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=180)
    rotas = c.execute("""SELECT id, file_path, file_hash_sha256, danado_motivo, bytes
                         FROM files WHERE danado=1 AND COALESCE(borrado,0)=0""").fetchall()
    sanas_hash, sanas_stem = set(), defaultdict(list)
    for fp, h in c.execute("""SELECT file_path, file_hash_sha256 FROM files
                              WHERE COALESCE(danado,0)=0 AND COALESCE(borrado,0)=0"""):
        if h:
            sanas_hash.add(h)
        sanas_stem[Path(fp).stem.lower()].append(fp)

    solas = [r for r in rotas
             if not (r[2] and r[2] in sanas_hash)
             and Path(r[1]).stem.lower() not in sanas_stem]
    print(f"rotas sin ningun respaldo: {len(solas)}  (de {len(rotas)} marcadas)")

    perdidas = recuperables = 0
    with open(SALIDA, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "ruta", "bytes", "motivo_registrado", "abre_ahora", "error"])
        for i, (pid, ruta, _h, motivo, bytes_) in enumerate(solas, 1):
            ok, err = abre(ruta)
            recuperables += ok
            perdidas += not ok
            w.writerow([pid, ruta, bytes_, motivo, "si" if ok else "no", err])
            fh.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(solas)}  perdidas={perdidas} recuperables={recuperables}")

    print(f"\nPERDIDAS de verdad : {perdidas}")
    print(f"SE ABREN igual     : {recuperables}  <- el marcado fue falso positivo")
    print(f"detalle en {SALIDA}")


if __name__ == "__main__":
    main()
