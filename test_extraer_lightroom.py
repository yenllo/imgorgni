"""python test_extraer_lightroom.py

Lo que no puede fallar: dos zips que traen el mismo dia no pueden pisarse.
Sobrescribir en silencio seria perder una foto sin enterarse.
"""
import os
import tempfile

from extraer_lightroom import destino_libre, numero


def main():
    # los zips se ordenan por numero, no por texto
    zs = ["LightroomLibrary_100.zip", "LightroomLibrary_2.zip",
          "LightroomLibrary_1.zip", "LightroomLibrary_20.zip"]
    assert sorted(zs, key=numero) == ["LightroomLibrary_1.zip", "LightroomLibrary_2.zip",
                                      "LightroomLibrary_20.zip", "LightroomLibrary_100.zip"]

    d = tempfile.mkdtemp()
    p = os.path.join(d, "foto.jpg")

    # no existe -> se escribe ahi
    assert destino_libre(p, 100) == (p, "nuevo")

    with open(p, "wb") as f:
        f.write(b"x" * 100)

    # existe con el MISMO tamaño -> ya esta, no hay nada que hacer
    assert destino_libre(p, 100) == (p, "igual")

    # existe con OTRO tamaño -> es otra foto: va al lado, no se pisa
    ruta, estado = destino_libre(p, 999)
    assert estado == "choque", estado
    assert ruta.endswith("foto__dup2.jpg"), ruta
    assert os.path.getsize(p) == 100, "el original no se puede haber tocado"

    # y si el __dup2 ya existe con ese tamaño, se reconoce en vez de duplicar
    with open(ruta, "wb") as f:
        f.write(b"y" * 999)
    assert destino_libre(p, 999) == (ruta, "igual")

    # un tercer tamaño distinto abre __dup3
    ruta3, estado3 = destino_libre(p, 555)
    assert estado3 == "choque" and ruta3.endswith("foto__dup3.jpg"), ruta3

    print("OK: ordena por numero y nunca pisa un archivo distinto")


if __name__ == "__main__":
    main()
