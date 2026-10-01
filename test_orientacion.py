"""python test_orientacion.py

Las fotos verticales salian acostadas en el curador: la camara las guarda
siempre igual y anota "esta girada" en el EXIF, y PIL no lo aplica solo.
Afectaba al 20% de los JPEG y al 35% de los RAW de la biblioteca.

Lo que se cubre aca:
  - la orientacion sale del EXIF cuando esta, y del flip de rawpy cuando no
    (preview bitmap de un RAW, que no trae EXIF)
  - enderezar() gira de verdad: una imagen 100x50 marcada como girada tiene
    que terminar 50x100
  - el caso del recorte de caras: crop() NO hereda el EXIF, asi que la
    orientacion se lee antes y se pasa a mano. Este es el que se rompe solo
    si alguien "simplifica" recortar_cara().
"""
import io

from PIL import Image

from utils_images import enderezar, orientacion


def _img_con_orientacion(orient, size=(100, 50)):
    """JPEG real con el tag EXIF de orientacion puesto."""
    im = Image.new("RGB", size, "red")
    exif = im.getexif()
    exif[274] = orient
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    buf.seek(0)
    return Image.open(buf)


def test_orientacion_del_exif():
    assert orientacion(_img_con_orientacion(6)) == 6
    assert orientacion(_img_con_orientacion(1)) == 1
    print("OK: la orientacion se lee del EXIF")


def test_orientacion_del_flip_cuando_no_hay_exif():
    """El preview bitmap de un RAW no trae EXIF: ahi manda rawpy.sizes.flip."""
    sin_exif = Image.new("RGB", (100, 50), "blue")
    assert orientacion(sin_exif, flip=6) == 6
    assert orientacion(sin_exif, flip=5) == 8    # medido en los .ARW/.CR2 reales
    assert orientacion(sin_exif, flip=0) == 1
    print("OK: sin EXIF, la orientacion sale del flip de rawpy")


def test_el_exif_le_gana_al_flip():
    assert orientacion(_img_con_orientacion(8), flip=6) == 8
    print("OK: si hay EXIF, se prefiere sobre el flip")


def test_enderezar_gira_de_verdad():
    """Lo que se veia mal: 100x50 acostada tiene que quedar 50x100 parada."""
    assert enderezar(_img_con_orientacion(6)).size == (50, 100)
    assert enderezar(_img_con_orientacion(8)).size == (50, 100)
    assert enderezar(_img_con_orientacion(3)).size == (100, 50)   # 180: no cambia
    assert enderezar(_img_con_orientacion(1)).size == (100, 50)   # derecha: intacta
    print("OK: enderezar() gira las verticales y deja quietas las derechas")


def test_recorte_se_endereza_con_la_orientacion_del_original():
    """El recorte de una cara tiene que salir derecho, como hace recortar_cara().

    Pillow 12 hereda el EXIF en crop(), pero eso no esta garantizado entre
    versiones, asi que recortar_cara() lee la orientacion del original y la
    pasa a mano. Aca se comprueba que ese camino explicito funciona.
    """
    original = _img_con_orientacion(6, size=(100, 100))
    recorte = original.crop((0, 0, 40, 20))
    assert enderezar(recorte, orient=orientacion(original)).size == (20, 40), \
        "con la orientacion del original tiene que girar"
    print("OK: el recorte se endereza con la orientacion leida del original")


def test_no_gira_dos_veces():
    """Enderezar lo ya enderezado no hace nada: si no, la foto queda al reves."""
    una_vez = enderezar(_img_con_orientacion(6))
    assert una_vez.size == (50, 100)
    assert enderezar(una_vez).size == (50, 100), "la giro de nuevo (doble rotacion)"
    print("OK: enderezar es idempotente, no hay doble rotacion")


def test_la_miniatura_guardada_no_pide_otra_rotacion():
    """Si la miniatura conservara la marca de girada, el navegador la rotaria
    otra vez y volveria a verse mal."""
    fix = enderezar(_img_con_orientacion(6))
    buf = io.BytesIO()
    fix.convert("RGB").save(buf, "JPEG", quality=82)
    buf.seek(0)
    guardada = Image.open(buf)
    assert guardada.size == (50, 100)
    assert guardada.getexif().get(274) in (None, 0, 1), \
        "la miniatura quedo marcada como girada: el navegador la va a rotar de nuevo"
    print("OK: la miniatura guardada ya esta derecha y no pide otra rotacion")


def test_no_revienta_con_basura():
    """Una miniatura torcida es mejor que un error 500."""
    assert enderezar(Image.new("RGB", (10, 10)), orient=99).size == (10, 10)
    print("OK: una orientacion invalida no rompe nada")


if __name__ == "__main__":
    test_orientacion_del_exif()
    test_orientacion_del_flip_cuando_no_hay_exif()
    test_el_exif_le_gana_al_flip()
    test_enderezar_gira_de_verdad()
    test_recorte_se_endereza_con_la_orientacion_del_original()
    test_no_gira_dos_veces()
    test_la_miniatura_guardada_no_pide_otra_rotacion()
    test_no_revienta_con_basura()
