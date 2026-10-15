"""python test_borrar_edited.py

Un borrado fisico no se deshace. Lo unico que impide perder una foto acá es que
`original_de` no devuelva ruta cuando el original no existe.
"""
import os
import tempfile

from borrar_edited import RE_ED, original_de


def toca(p):
    with open(p, "wb") as f:
        f.write(b"x")
    return p


def main():
    d = tempfile.mkdtemp()

    # el patron: que cuenta como _edited y que no
    assert RE_ED.match("_DSC9081_edited")
    assert RE_ED.match("_DSC7761_edited-1")
    assert RE_ED.match("_DSC0215__3__edited")
    assert not RE_ED.match("_DSC9081"), "el original no es un _edited"
    assert not RE_ED.match("editado_a_mano"), "no alcanza con contener 'edited'"

    # con original al lado, mismo formato
    toca(os.path.join(d, "foto.jpg"))
    ed = toca(os.path.join(d, "foto_edited.jpg"))
    assert original_de(ed) == os.path.join(d, "foto.jpg")

    # el original puede ser RAW aunque el edited sea jpg
    toca(os.path.join(d, "otra.ARW"))
    ed2 = toca(os.path.join(d, "otra_edited.jpg"))
    assert original_de(ed2) == os.path.join(d, "otra.ARW")

    # variante -1 tambien encuentra su base
    ed3 = toca(os.path.join(d, "foto_edited-1.jpg"))
    assert original_de(ed3) == os.path.join(d, "foto.jpg")

    # SIN original -> None. Este es el caso que evita perder una foto.
    huerfano = toca(os.path.join(d, "solita_edited.jpg"))
    assert original_de(huerfano) is None, \
        "sin original NO se puede devolver ruta: se borraria la unica copia"

    # un archivo que no es _edited nunca se toca
    assert original_de(os.path.join(d, "foto.jpg")) is None

    print("OK: solo borra cuando el original existe de verdad")


if __name__ == "__main__":
    main()
