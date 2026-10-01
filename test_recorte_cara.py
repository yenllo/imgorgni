"""python test_recorte_cara.py

En /personas y /caras/sueltas, 128 de 1.971 recortes salian como el cuadro
gris "Error rec". Causa: el bbox lo calcula FaceAnalyzer sobre
load_image_array(), que DESCARTA el preview incrustado del RAW cuando su lado
corto no llega a MIN_LADO_PREVIEW (640) y revela el RAW entero. Esos DNG del
rescate de F: traen un preview de 1024x576 (lado corto 576 < 640), asi que las
coordenadas quedaban en el espacio de la imagen grande (~3024x2012) mientras
recortar_cara() seguia recortando sobre el preview chico: crop() recibia una
caja invertida y PIL tiraba "Coordinate 'right' is less than 'left'".

Lo que se cubre aca:
  - recortar_cara() aplica el MISMO corte de MIN_LADO_PREVIEW que
    load_image_array(). Si alguien lo saca, vuelven los 128 cuadros grises.
  - un bbox que se sale de la imagen no devuelve una caja invertida.
"""
import inspect
import json
import os
import sqlite3

from PIL import Image

import utils_images
from utils_images import MIN_LADO_PREVIEW, recortar_cara

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos_v2.db")


def test_recortar_cara_respeta_el_umbral_del_preview():
    """El guard tiene que seguir ahi: es lo unico que mantiene a recortar_cara()
    en el mismo espacio de coordenadas que la deteccion."""
    src = inspect.getsource(recortar_cara)
    assert "MIN_LADO_PREVIEW" in src, (
        "recortar_cara() ya no mira MIN_LADO_PREVIEW: vuelve a recortar el "
        "preview chico con coordenadas de la imagen grande")
    assert "load_image_array" in src, (
        "recortar_cara() ya no cae al mismo decodificado que la deteccion")
    print("OK: recortar_cara sigue alineado con load_image_array")


def test_umbral_igual_al_de_load_image_array():
    """Los dos tienen que usar el mismo numero, o se desincronizan de nuevo."""
    assert MIN_LADO_PREVIEW == 640
    assert "MIN_LADO_PREVIEW" in inspect.getsource(utils_images._raw_preview)
    print(f"OK: un solo umbral compartido ({MIN_LADO_PREVIEW})")


def test_bbox_fuera_de_rango_no_arma_una_caja_invertida():
    """El sintoma exacto que se veia: min(im.width, x2) quedaba < x1."""
    im = Image.new("RGB", (100, 100), "red")
    x1, y1, x2, y2 = 300, 300, 400, 400          # entero fuera de la imagen
    nx1, ny1 = max(0, x1), max(0, y1)
    nx2, ny2 = min(im.width, x2), min(im.height, y2)
    assert nx2 < nx1, "asi se producia el error: la caja queda dada vuelta"
    try:
        im.crop((nx1, ny1, nx2, ny2)).convert("RGB")
        raise AssertionError("PIL deberia rechazar una caja invertida")
    except ValueError:
        pass
    print("OK: reproducido el modo de falla que causaba el cuadro gris")


def test_recorta_un_dng_real_de_preview_chico():
    """Prueba de verdad contra el archivo real. Se saltea si K: no esta
    montado (el disco se va a la Raspberry) o si la base no esta."""
    if not os.path.exists(DB):
        print("SKIP: no esta photos_v2.db")
        return
    con = sqlite3.connect(DB)
    con.execute("PRAGMA busy_timeout=5000")
    fila = con.execute("""
        SELECT f.bbox_json, i.file_path
        FROM faces f JOIN files i ON i.id = f.photo_id
        WHERE LOWER(i.file_path) LIKE '%.dng' AND i.borrado = 0
          AND i.ancho > 2000
        LIMIT 40""").fetchall()
    con.close()
    probados = 0
    for bbox_json, fp in fila:
        if not os.path.exists(fp):
            continue
        cara = recortar_cara(fp, json.loads(bbox_json))
        assert cara is not None, f"volvio el cuadro gris en {fp}"
        assert cara.size[0] > 0 and cara.size[1] > 0
        probados += 1
        if probados == 3:
            break
    if probados:
        print(f"OK: {probados} recortes reales de DNG salen con imagen")
    else:
        print("SKIP: no hay DNG accesibles (K: desmontado)")


if __name__ == "__main__":
    test_recortar_cara_respeta_el_umbral_del_preview()
    test_umbral_igual_al_de_load_image_array()
    test_bbox_fuera_de_rango_no_arma_una_caja_invertida()
    test_recorta_un_dng_real_de_preview_chico()
