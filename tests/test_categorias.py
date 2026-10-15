"""
Chequeo del mapeo escena -> carpeta y del voto por evento.

    python test_categorias.py
"""
import sys

import numpy as np

from utils_analisis import (SCENE_LABELS, categorize_event, scene_to_category,
                            hamming, is_low_quality, phash)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def test_escena_a_categoria():
    casos = [
        (["a dog"], "Fotos animales"),
        (["a cat", "a living room"], "Fotos animales"),
        (["a birthday party", "birthday cake"], "Fotos eventos"),
        (["a wedding ceremony"], "Fotos eventos"),
        (["a beach", "a sunset"], "Fotos paisajes"),
        (["a starry night sky"], "Fotos Astro"),
        (["a car"], "Fotos autos"),
        (["flowers"], "Fotos plantas"),
        (["a selfie"], "Fotos personas"),
        (["a classroom"], "Fotos escolares"),
        (["playing soccer"], "Fotos deportes"),
        (["a plate of food"], "Fotos comida"),
        (["a blurry unusable photo"], "Fotos descartes"),
        ([], "Fotos varias"),
    ]
    for escenas, esperada in casos:
        got = scene_to_category(escenas)
        assert got == esperada, f"{escenas} -> {got}, esperaba {esperada}"
    print(f"OK: {len(casos)} mapeos escena->categoría")


def test_voto_por_evento():
    # Mayoría de playa gana aunque haya una foto de comida
    evento_playa = [["a beach"], ["a beach", "a sunset"], ["a plate of food"], ["a beach"]]
    assert categorize_event(evento_playa) == "Fotos paisajes"

    # Escenas vacías + caras -> personas
    assert categorize_event([[], [], []], has_faces=True) == "Fotos personas"

    # Escenas vacías sin caras -> varias
    assert categorize_event([[], []], has_faces=False) == "Fotos varias"

    # Un cumpleaños en la playa con gente es un evento, no un paisaje
    mixto = [["a beach"], ["a birthday party"], ["a beach"], ["a beach"]]
    assert categorize_event(mixto, has_faces=True) == "Fotos eventos", \
        categorize_event(mixto, has_faces=True)
    print("OK: voto por evento (mayoría, caras, evento sobre paisaje)")


def test_phash_discrimina():
    rng = np.random.default_rng(0)
    a = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    b = a.copy()
    b[:10, :10] = 0  # cambio chico -> debe seguir siendo parecida
    c = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)  # imagen distinta

    ha, hb, hc = phash(a), phash(b), phash(c)
    assert ha and hb and hc
    assert hamming(ha, hb) < hamming(ha, hc), \
        f"phash no discrimina: similar={hamming(ha, hb)}, distinta={hamming(ha, hc)}"
    print(f"OK: phash (casi-igual={hamming(ha, hb)} < distinta={hamming(ha, hc)})")


def test_calidad():
    assert is_low_quality({"sharpness": 5.0, "brightness": 120.0})      # borrosa
    assert is_low_quality({"sharpness": 500.0, "brightness": 3.0})      # negra
    assert not is_low_quality({"sharpness": 500.0, "brightness": 120.0})  # buena
    print("OK: umbrales de calidad")


def test_dispositivo():
    from utils_analisis import clasificar_dispositivo as cd
    telefonos = [
        ("Apple iPhone 13 Pro", ""), ("samsung SM-G991B", ""), ("Xiaomi Redmi Note 10", ""),
        ("Google Pixel 7", ""), ("motorola moto g52", ""), ("HUAWEI P30", ""),
        ("OnePlus IN2023", ""), ("Sony Xperia 1 III", ""),
    ]
    for modelo, path in telefonos:
        assert cd(modelo, path) == "telefono", f"'{modelo}' debería ser teléfono, dio {cd(modelo, path)}"

    camaras = [
        ("Canon EOS R5", ""), ("NIKON D850", ""), ("SONY ILCE-7M3", ""),
        ("SONY DSC-RX100", ""), ("FUJIFILM X-T4", ""), ("Panasonic DMC-GH5", ""),
        ("OLYMPUS E-M1", ""), ("DJI FC3170", ""),
    ]
    for modelo, path in camaras:
        assert cd(modelo, path) == "camara", f"'{modelo}' debería ser cámara, dio {cd(modelo, path)}"

    # Sony es la marca ambigua: cámara por prefijo de modelo, teléfono si no
    assert cd("SONY ILCE-6400", "") == "camara"
    assert cd("Sony Xperia XZ", "") == "telefono"

    # Sin EXIF: RAW es cámara; nombre de WhatsApp/captura es teléfono
    assert cd(None, "/f/_DSC0123.ARW") == "camara"
    assert cd(None, "/f/IMG-20230101-WA0001.jpg") == "telefono"
    assert cd("", "/f/Screenshot_20230101.png") == "telefono"
    assert cd(None, "/f/PXL_20230101_120000.jpg") == "telefono"
    assert cd(None, "/f/escaneo.jpg") == "desconocido"
    print(f"OK: teléfono vs cámara ({len(telefonos)} tel, {len(camaras)} cám, Sony, sin-EXIF)")


def test_no_foto():
    """Casos reales encontrados en F:\\Descargas durante el ensayo."""
    from utils_analisis import es_no_foto
    grande = {"w": 4000, "h": 3000}
    chico = {"w": 200, "h": 150}

    # Basura web: sin EXIF y chica, o con nombre delator
    assert es_no_foto(None, "/d/bullet.png", chico)
    assert es_no_foto(None, "/d/Help_Background_Footer.png", chico)
    assert es_no_foto(None, "/d/338ced_ab2f41-mv2.jpg", grande), "patrón CDN Wix"
    assert es_no_foto(None, "/d/$IOIMJO5.jpg", grande), "archivo de papelera"
    assert es_no_foto(None, "/d/file.webp", grande)
    assert es_no_foto(None, "/d/favicon.png", chico)

    # Fotos reales: NUNCA se descartan
    assert not es_no_foto("Canon EOS 60D", "/f/logo_familia.jpg", chico), \
        "con EXIF de cámara jamás se descarta, aunque el nombre suene raro"
    assert not es_no_foto("samsung SM-G998B", "/f/IMG_001.jpg", grande)
    assert not es_no_foto(None, "/f/_DSC0123.ARW", None), "RAW siempre es foto"
    assert not es_no_foto(None, "/f/cumpleanios.jpg", grande), "sin EXIF pero grande y nombre normal"
    print("OK: detección de assets web / no-fotos (6 basura, 4 fotos reales)")


def test_clasificador_escenas():
    """Si scene_embeddings.npz existe, un 'embedding de imagen' = etiqueta beach
    debe clasificar como beach. Valida el centrado de punta a punta."""
    from pathlib import Path
    if not Path("scene_embeddings.npz").exists():
        print("SKIP: scene_embeddings.npz no existe (correr precompute_scene_embeddings.py)")
        return
    from utils_analisis import SceneClassifier
    clf = SceneClassifier("scene_embeddings.npz")
    # Reconstruir el embedding de imagen que daría exactamente la etiqueta 'beach':
    # como la matriz está centrada, sumamos de vuelta la media para simular el crudo
    idx = clf.labels.index("beach")
    img_emb = (clf.matrix[idx] + clf.mean).tolist()
    top = clf.classify_batch([img_emb], top_k=3, min_score=0.0)[0]
    assert top and top[0] == "beach", f"esperaba 'beach' primero, dio {top}"
    print(f"OK: clasificador de escenas (query beach -> {top})")


def test_vocabulario_consistente():
    assert len(SCENE_LABELS) == len(set(SCENE_LABELS)), "Hay etiquetas duplicadas"
    # Toda categoría con reglas debe ser alcanzable desde alguna escena del vocabulario
    alcanzadas = {scene_to_category([s]) for s in SCENE_LABELS}
    from utils_analisis import CATEGORY_RULES
    for categoria, _ in CATEGORY_RULES:
        assert categoria in alcanzadas, f"'{categoria}' no la dispara ninguna escena del vocabulario"
    print(f"OK: {len(SCENE_LABELS)} escenas, {len(CATEGORY_RULES)} categorías alcanzables")


if __name__ == "__main__":
    test_escena_a_categoria()
    test_voto_por_evento()
    test_phash_discrimina()
    test_calidad()
    test_dispositivo()
    test_no_foto()
    test_clasificador_escenas()
    test_vocabulario_consistente()
    print("\n✅ TODOS LOS CHEQUEOS DE CATEGORIZACIÓN PASARON")
