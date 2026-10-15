"""python test_no_mover.py

Con --no-mover la Fase 1 no puede tocar el disco. Un solo move_to_discard que
se escape reacomoda miles de archivos sin que nadie lo pida.
"""
import importlib
import json
import os
import sys
import tempfile

from PIL import Image

idx = importlib.import_module("1_indexado")


def foto(carpeta, nombre, color):
    p = os.path.join(carpeta, nombre)
    Image.new("RGB", (64, 48), color).save(p)
    return p


def main():
    d = tempfile.mkdtemp()
    raiz = os.path.join(d, "fotos")
    os.makedirs(raiz)
    a = foto(raiz, "a.jpg", (10, 20, 30))
    # b es copia byte a byte de a: duplicado exacto
    b = os.path.join(raiz, "b.jpg")
    with open(a, "rb") as s, open(b, "wb") as t:
        t.write(s.read())

    cfg_path = os.path.join(d, "cfg.json")
    cfg = {"root_dir": raiz, "db_path": os.path.join(d, "t.db"),
           "watermark_path": None, "discard_duplicates_dir": "_dups",
           "enable_mobile_filter": False, "max_workers_phase1": 2}
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    # Primera pasada solo con a.jpg: asi en la segunda su hash YA esta en la
    # base y se ejecuta de verdad la rama de "duplicado por contenido", que es
    # justo la que llama a move_to_discard.
    os.rename(b, b + ".oculto")
    idx.run(config_path=cfg_path, no_mover=True)
    os.rename(b + ".oculto", b)

    import sqlite3
    c = sqlite3.connect(cfg["db_path"])
    assert c.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    c.close()

    idx.run(config_path=cfg_path, no_mover=True)   # ahora b.jpg es duplicado

    despues = sorted(os.listdir(raiz))
    assert despues == ["a.jpg", "b.jpg"], \
        f"con --no-mover no se puede mover nada, quedo {despues}"
    assert not os.path.exists(os.path.join(raiz, "_dups")), \
        "no se puede ni crear la carpeta de descarte"

    c = sqlite3.connect(cfg["db_path"])
    n = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert n == 2, f"el duplicado tiene que quedar indexado igual, hay {n}"
    shas = [r[0] for r in c.execute("SELECT file_hash_sha256 FROM files")]
    assert shas[0] == shas[1], "son el mismo contenido: el hash tiene que coincidir"
    c.close()
    print("OK: --no-mover no toca el disco y deja los duplicados indexados")


if __name__ == "__main__":
    main()
