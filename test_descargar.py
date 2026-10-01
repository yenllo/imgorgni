"""La descarga tiene que dar el RAW cuando existe y un ZIP que se entienda solo.

Lo que se rompe facil y por eso se prueba:
  - el RAW y su JPEG son dos filas distintas en `files`: si la busqueda del
    hermano falla, el fotografo se baja el JPEG creyendo que lleva el RAW.
  - dos fotos con el mismo nombre en carpetas distintas se pisan dentro del ZIP.
  - el nombre del ZIP lleva evento, cantidad y estrellas, y tiene que sobrevivir
    a los caracteres que Windows no acepta en un nombre de archivo.

    python test_descargar.py
"""
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CURADOR_SIN_MIGRAR", "1")

import web_curador as W


def armar_disco(base):
    """Tres archivos: uno con RAW al lado, uno sin, y uno que repite nombre."""
    (base / "sub").mkdir()
    rutas = {
        "jpg_con_raw": base / "DSC09850.JPG",
        "raw":         base / "DSC09850.ARW",
        "jpg_solo":    base / "DSC09851.JPG",
        # mismo nombre que jpg_solo pero en otra carpeta: el caso que se pisa
        "jpg_repe":    base / "sub" / "DSC09851.JPG",
    }
    for k, p in rutas.items():
        p.write_bytes(k.encode() * 10)
    return rutas


def armar_db(db, rutas):
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, file_path TEXT UNIQUE, borrado INT DEFAULT 0);
        CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT, suggested_name TEXT);
        CREATE TABLE event_photos (event_id INT, photo_id INT);
    """)
    ids = {}
    for i, (k, p) in enumerate(rutas.items(), start=1):
        c.execute("INSERT INTO files (id, file_path, borrado) VALUES (?,?,0)", (i, str(p)))
        ids[k] = i
    c.execute("INSERT INTO events (id, name, suggested_name) VALUES (7, 'F2 - 09/05: liga', 'soccer')")
    c.commit()
    c.close()
    return ids


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rutas = armar_disco(base)
        db = str(base / "t.db")
        ids = armar_db(db, rutas)
        W.DB = db
        cli = W.app.test_client()

        # 1. el hermano RAW gana sobre el JPEG que se pidio
        with W.app.app_context():
            assert W._ruta_original(ids["jpg_con_raw"]) == rutas["raw"], \
                "pidiendo el JPEG tiene que salir el ARW que esta al lado"
            assert W._ruta_original(ids["jpg_solo"]) == rutas["jpg_solo"], \
                "sin RAW al lado se baja el archivo tal cual"
            assert W._ruta_original(ids["raw"]) == rutas["raw"], \
                "un RAW no busca hermano de si mismo"
        print("OK: baja el RAW cuando existe, y el original cuando no")

        # 2. la descarga suelta manda el archivo, no la vista de 1600px
        r = cli.get(f"/descargar/{ids['jpg_con_raw']}")
        assert r.status_code == 200, r.status_code
        assert "DSC09850.ARW" in r.headers["Content-Disposition"], r.headers["Content-Disposition"]
        assert r.data == rutas["raw"].read_bytes(), "tiene que ser el archivo entero, byte a byte"
        assert cli.get("/descargar/9999").status_code == 404
        print("OK: /descargar sirve el original completo y 404 si no existe")

        # 3. el ZIP: nombres unicos y contenido real
        pedidos = [ids["jpg_con_raw"], ids["jpg_solo"], ids["jpg_repe"]]
        r = cli.post("/descargar/zip", data={"ids": ",".join(map(str, pedidos)), "eid": "7"})
        assert r.status_code == 200, r.status_code
        z = zipfile.ZipFile(__import__("io").BytesIO(r.data))
        dentro = sorted(z.namelist())
        assert dentro == ["DSC09850.ARW", "DSC09851 (2).JPG", "DSC09851.JPG"], dentro
        assert z.read("DSC09850.ARW") == rutas["raw"].read_bytes()
        assert z.read("DSC09851 (2).JPG") == rutas["jpg_repe"].read_bytes(), \
            "la segunda DSC09851 tiene que ser la de sub/, no una copia de la primera"
        print(f"OK: ZIP sin pisadas de nombre -> {dentro}")

        # 4. el nombre del ZIP se entiende solo y no rompe Windows
        cd = r.headers["Content-Disposition"]
        assert "3 fotos" in cd, cd
        assert "/" not in cd.split("filename=")[-1], "la barra del nombre del evento tiene que salir"
        print(f"OK: nombre del ZIP -> {cd.split('filename=')[-1].strip()}")

        # 5. faltantes: no explota, deja constancia adentro
        c = sqlite3.connect(db)
        c.execute("INSERT INTO files (id, file_path, borrado) VALUES (99, ?, 0)",
                  (str(base / "no_existe.JPG"),))
        c.commit()
        c.close()
        r = cli.post("/descargar/zip", data={"ids": f"99,{ids['jpg_solo']}"})
        assert r.status_code == 200
        z = zipfile.ZipFile(__import__("io").BytesIO(r.data))
        assert "FALTANTES.txt" in z.namelist(), z.namelist()
        assert "DSC09851.JPG" in z.namelist(), "lo que si esta se baja igual"
        print("OK: un archivo ilegible no tumba la descarga, queda anotado")

        # 6. como se describen las estrellas
        assert W._desc_estrellas([5, 5, 5]) == "5 estrellas"
        assert W._desc_estrellas([4, 5]) == "4 y 5 estrellas"
        assert W._desc_estrellas([2, 3, 5]) == "2 a 5 estrellas"
        assert W._desc_estrellas([0, 0]) == "", "solo descartadas: no se menciona"
        assert W._desc_estrellas([0, 5]) == "5 estrellas", "las 0 no entran en el rango"
        assert W._limpiar_nombre('F2: liga/2026 <final>') == "F2- liga-2026 -final-"
        print("OK: estrellas y limpieza de nombre")

    print("\nTodo OK")


if __name__ == "__main__":
    main()
